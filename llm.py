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
from typing import Any, Dict

import requests

from logger import get_logger

log = get_logger()

_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


class LLMError(RuntimeError):
    """Raised when the LLM call fails or returns something unusable."""


class VisionLLM:
    """Minimal client for one-shot, image-grounded JSON decisions."""

    def __init__(self, api_key: str, model: str, timeout: int = 60) -> None:
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    def decide(self, system_prompt: str, user_text: str, image_path: str) -> Dict[str, Any]:
        """Send the prompt + screenshot and return the parsed JSON decision."""
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
        try:
            resp = requests.post(
                url,
                headers={
                    "Content-Type": "application/json",
                    "x-goog-api-key": self.api_key,
                },
                data=json.dumps(payload),
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise LLMError(f"Network error talking to the model: {exc}") from exc

        if resp.status_code != 200:
            raise LLMError(
                f"Model API returned {resp.status_code}: {resp.text[:500]}"
            )

        try:
            data = resp.json()
            text = data["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError, ValueError) as exc:
            raise LLMError(f"Unexpected model response shape: {resp.text[:500]}") from exc

        return self._parse_json(text)

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
