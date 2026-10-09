#!/usr/bin/env python3
"""Build the self-contained course-assistant chat page (course_chat.html).

Bakes the slide chunks and the assistant system prompt into
templates/chat_page_template.html. The page performs BM25 retrieval and LLM
reranking in the browser and calls the class endpoint directly (CORS-open),
so it needs no server of its own — just a browser with the API key.

Usage: python3 scripts/build_chat_page.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from prompt_source import system_prompt  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def main():
    chunks = json.loads((ROOT / "data" / "course_chunks.json").read_text())
    compact = [
        {k: c[k] for k in ("id", "week", "slide", "title", "text")}
        for c in chunks if c["text"].strip()
    ]
    template = (ROOT / "templates" / "chat_page_template.html").read_text()
    out = (template
           .replace("__CHUNKS_DATA__", json.dumps(compact, ensure_ascii=True))
           .replace("__SYSTEM_PROMPT__", json.dumps(system_prompt())))
    dest = ROOT / "course_chat.html"
    dest.write_text(out)
    print(f"wrote {dest.relative_to(ROOT)} — {len(compact)} chunks, "
          f"{len(out)} bytes")


if __name__ == "__main__":
    main()
