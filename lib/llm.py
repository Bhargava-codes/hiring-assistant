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
import logging
import os
import re
from functools import lru_cache
from typing import Any

from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger("contract_hrms.llm")

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


def _log_usage(label: str, resp) -> None:
    """Log exact tokens + real USD cost for a completion, per OpenRouter's
    usage.include response (not an estimate — this is what was actually billed)."""
    u = getattr(resp, "usage", None)
    if not u:
        return
    cost = getattr(u, "cost", None)
    cost_str = f"${cost:.6f}" if cost is not None else "n/a"
    log.info(
        "%s: prompt=%d completion=%d total=%d tokens, cost=%s (model=%s)",
        label, u.prompt_tokens, u.completion_tokens, u.total_tokens, cost_str, resp.model,
    )


def chat(
    messages: list[dict[str, str]],
    *,
    model: str | None = None,
    temperature: float = 0.6,
    max_tokens: int = 1400,
    _label: str = "chat",
) -> str:
    """Conversational / long-form completion. Returns assistant text."""
    resp = _client().chat.completions.create(
        model=model or INTAKE_MODEL,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        extra_headers=_extra_headers(),
        extra_body={"usage": {"include": True}},
    )
    _log_usage(_label, resp)
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
    max_tokens: int = 500,
    _label: str = "extract_json",
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
        extra_body={"usage": {"include": True}},
    )
    try:
        resp = _client().chat.completions.create(
            response_format={"type": "json_object"}, **create_kwargs
        )
    except Exception as e:
        # Billing/rate-limit errors would just fail identically a second time —
        # retrying only doubles the wasted call. Only retry (without
        # response_format) for the "this route doesn't support it" case.
        if getattr(e, "status_code", None) in (402, 429):
            raise
        resp = _client().chat.completions.create(**create_kwargs)

    _log_usage(_label, resp)
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
