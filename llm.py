"""Vision LLM client (Groq, free tier).

We talk to Groq's OpenAI-compatible REST API directly with ``requests`` — no
vendor SDK, so the dependency surface stays tiny and the request/response shape
is explicit. Groq serves Llama-4 vision models very fast on a generous free
tier, which suits an interactive automation agent.

The model is given a screenshot plus the task and must reply with a single JSON
action. Requesting ``response_format: json_object`` keeps the output clean and
parseable. Transient failures (429 rate limit, 5xx) are retried with backoff,
honouring the server's suggested retry delay when one is provided.
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

_GROQ_ENDPOINT = "https://api.groq.com/openai/v1/chat/completions"


class LLMError(RuntimeError):
    """Raised when the LLM call fails or returns something unusable."""


class VisionLLM:
    """Minimal Groq client for one-shot, image-grounded JSON decisions."""

    def __init__(self, api_key: str, model: str, timeout: int = 60, max_retries: int = 4) -> None:
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.max_retries = max_retries

    def decide(self, system_prompt: str, user_text: str, image_path: str) -> Dict[str, Any]:
        """Send the prompt + screenshot and return the parsed JSON decision."""
        with open(image_path, "rb") as fh:
            image_b64 = base64.standard_b64encode(fh.read()).decode("ascii")

        payload = {
            "model": self.model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user_text},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{image_b64}"},
                        },
                    ],
                },
            ],
        }
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        body = json.dumps(payload)

        last_error = ""
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = requests.post(_GROQ_ENDPOINT, headers=headers, data=body, timeout=self.timeout)
            except requests.RequestException as exc:
                last_error = f"Network error: {exc}"
                self._sleep(self._backoff(attempt), last_error, attempt)
                continue

            if resp.status_code == 200:
                try:
                    text = resp.json()["choices"][0]["message"]["content"]
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
            raise LLMError(f"Groq API returned {resp.status_code}: {resp.text[:500]}")

        raise LLMError(f"Giving up after {self.max_retries} attempts. Last error: {last_error}")

    # -- helpers --------------------------------------------------------------

    def _sleep(self, seconds: float, reason: str, attempt: int) -> None:
        log.warning("LLM attempt %d/%d failed (%s). Retrying in %.1fs...",
                    attempt, self.max_retries, reason, seconds)
        time.sleep(seconds)

    @staticmethod
    def _backoff(attempt: int) -> float:
        return min(2.0 ** attempt, 30.0)

    @staticmethod
    def _retry_delay(body: str) -> float:
        """Pull the server's suggested retry delay out of an error body, if present."""
        match = (
            re.search(r'retry after ([\d.]+)', body, re.IGNORECASE)
            or re.search(r'try again in ([\d.]+)s', body, re.IGNORECASE)
            or re.search(r'"retry_after":\s*([\d.]+)', body)
        )
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
