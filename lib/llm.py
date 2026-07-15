"""Centralized LLM access.

Every model call in the app goes through this module so the provider and the
two model roles can be swapped in exactly one place (per the build spec).

Provider: OpenRouter (OpenAI-compatible) via the ``openai`` SDK.
  - INTAKE_MODEL  -> conversational discovery-agent turns, one-pager, renderings
  - EXTRACT_MODEL -> per-turn structured field extraction (JSON mode)

No API key is ever hard-coded; it is read from ``OPENROUTER_API_KEY``. When the
key is absent the module reports ``has_api_key() == False`` and callers fall
back to a deterministic offline mode so the prototype stays demoable.
"""
from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from typing import Any

from dotenv import load_dotenv

load_dotenv()

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

INTAKE_MODEL = os.getenv("INTAKE_MODEL", "anthropic/claude-sonnet-4.6")
EXTRACT_MODEL = os.getenv("EXTRACT_MODEL", "anthropic/claude-haiku-4.5")


class LLMUnavailable(RuntimeError):
    """Raised when a real model call is attempted with no API key configured."""


def has_api_key() -> bool:
    return bool(os.getenv("OPENROUTER_API_KEY"))


@lru_cache(maxsize=1)
def _client():
    if not has_api_key():
        raise LLMUnavailable(
            "OPENROUTER_API_KEY is not set. Copy .env.example to .env and add a key."
        )
    from openai import OpenAI

    return OpenAI(
        base_url=OPENROUTER_BASE_URL,
        api_key=os.getenv("OPENROUTER_API_KEY"),
    )


def _extra_headers() -> dict[str, str]:
    headers: dict[str, str] = {}
    if url := os.getenv("OPENROUTER_APP_URL"):
        headers["HTTP-Referer"] = url
    if name := os.getenv("OPENROUTER_APP_NAME"):
        headers["X-Title"] = name
    return headers


def chat(
    messages: list[dict[str, str]],
    *,
    model: str | None = None,
    temperature: float = 0.6,
    max_tokens: int = 1400,
) -> str:
    """Conversational / long-form completion. Returns assistant text."""
    resp = _client().chat.completions.create(
        model=model or INTAKE_MODEL,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        extra_headers=_extra_headers(),
    )
    return (resp.choices[0].message.content or "").strip()


def _strip_code_fences(text: str) -> str:
    text = text.strip()
    # ```json ... ```  or  ``` ... ```
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    if fence:
        return fence.group(1).strip()
    return text


def extract_json(
    messages: list[dict[str, str]],
    *,
    model: str | None = None,
    temperature: float = 0.0,
    max_tokens: int = 1600,
) -> dict[str, Any]:
    """Structured extraction call. Requests JSON output and parses defensively.

    Uses ``response_format={"type": "json_object"}`` where supported, then a
    strip-code-fences + try/catch parse fallback so a chatty model that wraps
    JSON in prose or fences still yields a dict.
    """
    create_kwargs: dict[str, Any] = dict(
        model=model or EXTRACT_MODEL,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        extra_headers=_extra_headers(),
    )
    try:
        resp = _client().chat.completions.create(
            response_format={"type": "json_object"}, **create_kwargs
        )
    except Exception:
        # Some models/routes reject response_format; retry without it.
        resp = _client().chat.completions.create(**create_kwargs)

    raw = (resp.choices[0].message.content or "").strip()
    return _parse_json_loose(raw)


def _parse_json_loose(raw: str) -> dict[str, Any]:
    candidates = [raw, _strip_code_fences(raw)]
    # Last resort: grab the outermost {...} block.
    brace = re.search(r"\{.*\}", raw, re.DOTALL)
    if brace:
        candidates.append(brace.group(0))
    for cand in candidates:
        try:
            parsed = json.loads(cand)
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, TypeError):
            continue
    return {}
