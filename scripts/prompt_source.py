#!/usr/bin/env python3
"""Shared prompt-reader: extracts the assistant system prompt from
prompts/assistant_prompt.md (the canonical copy) for both CLI and web builds.

Usage (import): from prompt_source import system_prompt
"""

from pathlib import Path

_PROMPT_FILE = Path(__file__).resolve().parent.parent / "prompts" / "assistant_prompt.md"


def system_prompt():
    text = _PROMPT_FILE.read_text()
    start = text.index("## System prompt") + len("## System prompt")
    end = text.index("## User message format")
    return text[start:end].strip()
