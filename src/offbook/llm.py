"""NanoGPT chat completions (OpenAI-compatible), with JSON extraction, validate-then-retry, and a usage log.
Ported from Trellis; the addition is `image_part`, for sending a photographed page.

Tests set `FAKE` to a function (model, messages) -> str so no network call is made.
"""

import base64
import json
import logging
import os
import re
import sqlite3
import time
from collections.abc import Callable

import httpx

from offbook.config import Settings
from offbook.db import now_utc

log = logging.getLogger(__name__)
FAKE: Callable[[str, list[dict]], str] | None = None
DAILY_CAP_DEFAULT = 200


class LLMError(Exception):
    pass


class Truncated(LLMError):
    """The reply hit max_tokens. Re-asking with the same budget would fail the same way."""


def image_part(data: bytes, mime: str) -> dict:
    """A message content part carrying an image inline, in the OpenAI `image_url` data-URL form."""
    return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{base64.b64encode(data).decode()}"}}


def extract_json(text: str) -> dict:
    text = (text or "").strip()
    text = re.sub(r"^<think>.*?</think>\s*", "", text, flags=re.S)
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < 0:
        raise ValueError("no JSON object in response")
    return json.loads(text[start:end + 1])


def _log(conn, profile_id, purpose, model, usage, ms, ok, error=None):
    conn.execute(
        """INSERT INTO llm_call(profile_id, purpose, model, prompt_tokens, completion_tokens, duration_ms, ok, error,
                                created_at) VALUES (?,?,?,?,?,?,?,?,?)""",
        (profile_id, purpose, model, usage.get("prompt_tokens"), usage.get("completion_tokens"), ms, int(ok),
         (error or "")[:500] or None, now_utc()))


def calls_today(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM llm_call WHERE created_at >= date('now')").fetchone()[0]


def call_llm(conn: sqlite3.Connection, s: Settings, *, purpose: str, model: str, messages: list[dict],
             profile_id: int | None = None, temperature: float = 0.0, max_tokens: int = 2500) -> str:
    cap = int(os.environ.get("LLM_DAILY_CAP", DAILY_CAP_DEFAULT))
    if calls_today(conn) >= cap:
        raise LLMError(f"daily AI call cap ({cap}) reached")
    t0 = time.monotonic()
    usage: dict = {}
    try:
        if FAKE is not None:
            text = FAKE(model, messages)
        else:
            if not s.nanogpt_api_key:
                raise LLMError("NANOGPT_API_KEY is not set")
            r = httpx.post(f"{s.nanogpt_base_url.rstrip('/')}/chat/completions",
                           headers={"Authorization": f"Bearer {s.nanogpt_api_key}"},
                           json={"model": model, "messages": messages, "temperature": temperature,
                                 "max_tokens": max_tokens},
                           timeout=180)
            r.raise_for_status()
            body = r.json()
            usage = body.get("usage") or {}
            choice = body["choices"][0]
            text = choice["message"]["content"] or ""
            if choice.get("finish_reason") == "length":
                raise Truncated(f"reply cut off at max_tokens={max_tokens} ({len(text)} chars)")
    except Exception as e:
        _log(conn, profile_id, purpose, model, usage, int((time.monotonic() - t0) * 1000), False,
             f"{type(e).__name__}: {e}")
        raise
    _log(conn, profile_id, purpose, model, usage, int((time.monotonic() - t0) * 1000), True)
    return text


def call_json(conn: sqlite3.Connection, s: Settings, *, purpose: str, model: str, messages: list[dict],
              validate: Callable[[dict], list[str]], profile_id: int | None = None, max_tokens: int = 2500,
              temperature: float = 0.0) -> dict:
    """Call, extract JSON, validate; on failure send the errors back once. Raises LLMError if both tries fail.

    `validate` may also normalise the object in place; it returns a list of human-readable errors."""
    messages = list(messages)
    errors: list[str] = []
    for attempt in range(2):
        raw = call_llm(conn, s, purpose=purpose, model=model, messages=messages, profile_id=profile_id,
                       max_tokens=max_tokens, temperature=temperature)
        try:
            obj = extract_json(raw)
            errors = validate(obj)
        except (ValueError, TypeError, KeyError) as e:
            obj, errors = None, [f"could not parse a JSON object: {e}"]
            log.warning("%s unparseable reply (%d chars), tail: %r", purpose, len(raw), raw[-200:])
        if obj is not None and not errors:
            return obj
        log.warning("%s attempt %d failed validation: %s", purpose, attempt + 1, errors[:5])
        messages += [{"role": "assistant", "content": raw},
                     {"role": "user", "content": "That failed validation:\n- " + "\n- ".join(errors[:20])
                      + "\nFix these and return the full JSON object again, with no other text."}]
    raise LLMError(f"{purpose}: invalid output after retry: {'; '.join(errors[:5])}")
