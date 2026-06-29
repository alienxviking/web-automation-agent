"""Vision LLM client (Google Gemini, free tier).

We talk to the Generative Language REST API directly with ``requests`` instead of
a vendor SDK. This keeps the dependency surface tiny, avoids SDK version churn,
and makes the exact request/response shape obvious for the viva.

The model is given a screenshot plus the task and must reply with a single JSON
action. Forcing ``responseMimeType: application/json`` means we get clean,
parseable output every time.
"""

from __future__ import annotations

import base64
import json
import re
import time
from typing import Any, Dict

import requests

from logger import get_logger

log = get_logger()

_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


class LLMError(RuntimeError):
    """Raised when the LLM call fails or returns something unusable."""


class VisionLLM:
    """Minimal client for one-shot, image-grounded JSON decisions."""

    def __init__(self, api_key: str, model: str, timeout: int = 60, max_retries: int = 4) -> None:
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.max_retries = max_retries

    def decide(self, system_prompt: str, user_text: str, image_path: str) -> Dict[str, Any]:
        """Send the prompt + screenshot and return the parsed JSON decision.

        Transient failures (429 rate limit, 5xx) are retried with backoff,
        honouring the server's suggested retry delay when one is provided.
        """
        with open(image_path, "rb") as fh:
            image_b64 = base64.standard_b64encode(fh.read()).decode("ascii")

        payload = {
            "system_instruction": {"parts": [{"text": system_prompt}]},
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {"text": user_text},
                        {"inline_data": {"mime_type": "image/png", "data": image_b64}},
                    ],
                }
            ],
            "generationConfig": {
                "temperature": 0,
                "responseMimeType": "application/json",
            },
        }

        url = _ENDPOINT.format(model=self.model)
        headers = {"Content-Type": "application/json", "x-goog-api-key": self.api_key}
        body = json.dumps(payload)

        last_error = ""
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = requests.post(url, headers=headers, data=body, timeout=self.timeout)
            except requests.RequestException as exc:
                last_error = f"Network error: {exc}"
                self._sleep(self._backoff(attempt), last_error, attempt)
                continue

            if resp.status_code == 200:
                try:
                    data = resp.json()
                    text = data["candidates"][0]["content"]["parts"][0]["text"]
                except (KeyError, IndexError, ValueError) as exc:
                    raise LLMError(f"Unexpected model response shape: {resp.text[:500]}") from exc
                return self._parse_json(text)

            # Retry on rate limit (429) and transient server errors (5xx).
            if resp.status_code == 429 or resp.status_code >= 500:
                last_error = f"HTTP {resp.status_code}: {resp.text[:200]}"
                delay = self._retry_delay(resp.text) or self._backoff(attempt)
                self._sleep(delay, last_error, attempt)
                continue

            # Non-retryable client error (bad key, bad model, etc.).
            raise LLMError(f"Model API returned {resp.status_code}: {resp.text[:500]}")

        raise LLMError(f"Giving up after {self.max_retries} attempts. Last error: {last_error}")

    # -- retry helpers --------------------------------------------------------

    def _sleep(self, seconds: float, reason: str, attempt: int) -> None:
        log.warning("LLM attempt %d/%d failed (%s). Retrying in %.1fs...",
                    attempt, self.max_retries, reason, seconds)
        time.sleep(seconds)

    @staticmethod
    def _backoff(attempt: int) -> float:
        return min(2.0 ** attempt, 30.0)

    @staticmethod
    def _retry_delay(body: str) -> float:
        """Pull the server's suggested retry delay out of a 429 body, if present."""
        match = re.search(r'retry in ([\d.]+)s', body) or re.search(r'"retryDelay":\s*"([\d.]+)s"', body)
        if match:
            try:
                return float(match.group(1)) + 1.0
            except ValueError:
                return 0.0
        return 0.0

    @staticmethod
    def _parse_json(text: str) -> Dict[str, Any]:
        """Parse the model's JSON, tolerating accidental ```json fences."""
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`")
            if cleaned.lower().startswith("json"):
                cleaned = cleaned[4:]
            cleaned = cleaned.strip()
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError as exc:
            raise LLMError(f"Model did not return valid JSON: {text[:500]}") from exc
