#!/usr/bin/env python3
"""Shared OpenAI-compatible LLM client for the MBAX 6418 class endpoint.

Abstraction over the class vLLM server:
  base_url http://dobolyi.com:9001/v1   (override LLM_API_URL)
  api_key  6418                         (override LLM_API_KEY)
  model    cyankiwi/Qwen3.6-35B-A3B-AWQ-4bit (override LLM_MODEL)

Endpoint specifics learned in Assignment 1:
- The model is a Qwen3 *thinking* model: with thinking enabled, JSON lands in
  the `reasoning` field and `content` comes back null. Thinking is disabled via
  chat_template_kwargs {"enable_thinking": false}.
- Long outputs can truncate mid-JSON at small max_tokens; chat_json retries
  with a larger token budget when the parsed object is missing fields.
- The model occasionally emits out-of-schema values; the quiz/assistant
  prompts enforce JSON, and consumers normalize (see generate_quiz.py).
"""

import os
import json
import time
import urllib.request

BASE_URL = os.environ.get("LLM_API_URL", "http://dobolyi.com:9001/v1")
API_KEY = os.environ.get("LLM_API_KEY", "6418")
MODEL = os.environ.get("LLM_MODEL", "cyankiwi/Qwen3.6-35B-A3B-AWQ-4bit")

CHAT_TEMPLATE_KWARGS = {"enable_thinking": False}


def _post(path, payload, timeout=120, tries=3):
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{BASE_URL.rstrip('/')}/{path}",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {API_KEY}",
        },
        method="POST",
    )
    last_err = None
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode())
        except Exception as e:  # network / 5xx / parse — retry with backoff
            last_err = e
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"LLM request failed after {tries} tries: {last_err}")


def chat(messages, max_tokens=1024, temperature=0.0, model=None):
    """One completion. Returns the content string (may be empty)."""
    payload = {
        "model": model or MODEL,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "chat_template_kwargs": CHAT_TEMPLATE_KWARGS,
    }
    data = _post("chat/completions", payload)
    try:
        return data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        raise RuntimeError(f"unexpected completion shape: {json.dumps(data)[:400]}")


def chat_json(messages, required_keys=(), max_tokens=1024, temperature=0.0,
              attempts=3):
    """Chat then parse JSON. Retries (larger budget) until `required_keys`
    are all present. Returns the parsed object."""
    for attempt in range(attempts):
        text = chat(messages, max_tokens=max_tokens, temperature=temperature)
        obj = extract_json(text)
        if obj is not None and all(k in obj for k in required_keys):
            return obj
        max_tokens = max(2048, int(max_tokens * 1.8))  # truncated JSON fix
    raise ValueError(
        f"could not parse JSON with keys {required_keys} after {attempts} "
        f"attempts; last text: {text[:300]!r}")


def extract_json(text):
    """Pull the first JSON object/array out of a string (tolerant of prose
    around it, code fences, leading text). Returns None if none found."""
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    # outermost { ... } : scan braces; else outermost [ ... ]
    for open_ch, close_ch in (("{", "}"), ("[", "]")):
        start = text.find(open_ch)
        if start == -1:
            continue
        depth = 0
        for i in range(start, len(text)):
            if text[i] == open_ch:
                depth += 1
            elif text[i] == close_ch:
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except json.JSONDecodeError:
                        break
    return None
