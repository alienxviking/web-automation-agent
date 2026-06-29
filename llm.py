"""Vision LLM layer with automatic multi-provider fallback.

Two free, vision-capable providers are supported:

  * Gemini  - Google AI Studio free tier (REST).
  * Groq    - Llama-4 vision on Groq's free tier (OpenAI-compatible REST).

Each provider is a small class with a single ``attempt`` method that makes one
request and returns the parsed JSON decision (or raises). ``VisionLLM``
orchestrates them: for every decision it tries each available provider in
priority order and returns the first success. If a provider is rate-limited or
errors, it falls through to the next one *immediately* — so an exhausted Gemini
quota transparently hands off to Groq with no long wait. Only when every
provider fails in a pass does it back off and retry the whole pass.

Talking to both providers over plain ``requests`` keeps the dependency surface
tiny and the request/response shapes explicit.
"""

from __future__ import annotations

import base64
import json
import re
import time
from typing import Any, Dict, List

import requests

from logger import get_logger

log = get_logger()

_GEMINI_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
_GROQ_ENDPOINT = "https://api.groq.com/openai/v1/chat/completions"


class LLMError(RuntimeError):
    """A provider call failed in a way that isn't worth retrying on its own."""


class RateLimitError(LLMError):
    """Transient failure (rate limit / server error) — try another provider."""

    def __init__(self, message: str, retry_after: float = 0.0) -> None:
        super().__init__(message)
        self.retry_after = retry_after


def _parse_json(text: str) -> Dict[str, Any]:
    """Parse a model's JSON reply, tolerating accidental ```json fences."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:]
        cleaned = cleaned.strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise LLMError(f"Model did not return valid JSON: {text[:300]}") from exc


def _retry_after(body: str) -> float:
    """Extract a server-suggested retry delay (seconds) from an error body."""
    match = re.search(r"retry in ([\d.]+)s", body) or re.search(r'"retryDelay":\s*"([\d.]+)s"', body)
    if match:
        try:
            return float(match.group(1)) + 1.0
        except ValueError:
            return 0.0
    return 0.0


# --------------------------------------------------------------------------- #
# Providers
# --------------------------------------------------------------------------- #


class _Provider:
    name = "base"

    def __init__(self, api_key: str, model: str, timeout: int = 60) -> None:
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    def available(self) -> bool:
        return bool(self.api_key)

    def attempt(self, system_prompt: str, user_text: str, image_b64: str) -> Dict[str, Any]:
        raise NotImplementedError

    def _post(self, url: str, headers: Dict[str, str], payload: Dict[str, Any]) -> requests.Response:
        try:
            return requests.post(url, headers=headers, data=json.dumps(payload), timeout=self.timeout)
        except requests.RequestException as exc:
            raise RateLimitError(f"{self.name} network error: {exc}") from exc


class GeminiProvider(_Provider):
    name = "gemini"

    def attempt(self, system_prompt: str, user_text: str, image_b64: str) -> Dict[str, Any]:
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
            "generationConfig": {"temperature": 0, "responseMimeType": "application/json"},
        }
        resp = self._post(
            _GEMINI_ENDPOINT.format(model=self.model),
            {"Content-Type": "application/json", "x-goog-api-key": self.api_key},
            payload,
        )
        if resp.status_code == 200:
            try:
                text = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
            except (KeyError, IndexError, ValueError) as exc:
                raise LLMError(f"gemini: unexpected response shape: {resp.text[:300]}") from exc
            return _parse_json(text)
        if resp.status_code == 429 or resp.status_code >= 500:
            raise RateLimitError(f"gemini HTTP {resp.status_code}", _retry_after(resp.text))
        raise LLMError(f"gemini HTTP {resp.status_code}: {resp.text[:300]}")


class GroqProvider(_Provider):
    name = "groq"

    def attempt(self, system_prompt: str, user_text: str, image_b64: str) -> Dict[str, Any]:
        data_uri = f"data:image/png;base64,{image_b64}"
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
                        {"type": "image_url", "image_url": {"url": data_uri}},
                    ],
                },
            ],
        }
        resp = self._post(
            _GROQ_ENDPOINT,
            {"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"},
            payload,
        )
        if resp.status_code == 200:
            try:
                text = resp.json()["choices"][0]["message"]["content"]
            except (KeyError, IndexError, ValueError) as exc:
                raise LLMError(f"groq: unexpected response shape: {resp.text[:300]}") from exc
            return _parse_json(text)
        if resp.status_code == 429 or resp.status_code >= 500:
            raise RateLimitError(f"groq HTTP {resp.status_code}", _retry_after(resp.text))
        raise LLMError(f"groq HTTP {resp.status_code}: {resp.text[:300]}")


# --------------------------------------------------------------------------- #
# Orchestrator
# --------------------------------------------------------------------------- #


class VisionLLM:
    """Tries each available provider in order, with cross-provider fallback."""

    def __init__(self, providers: List[_Provider], max_passes: int = 3) -> None:
        self.providers = [p for p in providers if p.available()]
        self.max_passes = max_passes
        if not self.providers:
            raise LLMError("No vision providers are configured (set a Gemini or Groq API key).")
        log.info("Vision providers (in priority order): %s",
                 ", ".join(f"{p.name}:{p.model}" for p in self.providers))

    def decide(self, system_prompt: str, user_text: str, image_path: str) -> Dict[str, Any]:
        with open(image_path, "rb") as fh:
            image_b64 = base64.standard_b64encode(fh.read()).decode("ascii")

        suggested_delay = 0.0
        last_error = ""

        for pass_no in range(1, self.max_passes + 1):
            for provider in self.providers:
                try:
                    result = provider.attempt(system_prompt, user_text, image_b64)
                    if pass_no > 1 or provider is not self.providers[0]:
                        log.info("Decision served by provider '%s'.", provider.name)
                    return result
                except RateLimitError as exc:
                    last_error = str(exc)
                    suggested_delay = max(suggested_delay, exc.retry_after)
                    log.warning("Provider '%s' unavailable (%s) — trying next.", provider.name, exc)
                except LLMError as exc:
                    last_error = str(exc)
                    log.warning("Provider '%s' failed (%s) — trying next.", provider.name, exc)

            # Every provider failed this pass. Back off before retrying.
            if pass_no < self.max_passes:
                delay = suggested_delay or min(2.0 ** pass_no, 20.0)
                log.warning("All providers failed (pass %d/%d). Backing off %.1fs...",
                            pass_no, self.max_passes, delay)
                time.sleep(delay)
                suggested_delay = 0.0

        raise LLMError(f"All providers failed after {self.max_passes} passes. Last error: {last_error}")


def build_llm(
    gemini_api_key: str,
    gemini_model: str,
    groq_api_key: str,
    groq_model: str,
    provider_order: List[str],
) -> VisionLLM:
    """Construct a VisionLLM honouring the configured provider priority order."""
    catalogue = {
        "gemini": GeminiProvider(gemini_api_key, gemini_model),
        "groq": GroqProvider(groq_api_key, groq_model),
    }
    ordered: List[_Provider] = []
    for name in provider_order:
        provider = catalogue.get(name.strip().lower())
        if provider and provider not in ordered:
            ordered.append(provider)
    # Include any provider omitted from the order list, so a configured key is
    # never silently ignored.
    for provider in catalogue.values():
        if provider not in ordered:
            ordered.append(provider)
    return VisionLLM(ordered)
