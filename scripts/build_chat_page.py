#!/usr/bin/env python3
"""Build the self-contained course-assistant chat page (course_chat.html).

Bakes the slide/page chunks (with loc, section, images) and the assistant
system prompt into templates/chat_page_template.html. The page runs BM25
retrieval + LLM reranking in the browser and calls the class endpoint
directly (CORS-open), with a materials/topic selection bar and image
evidence per source.

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
        {k: c[k] for k in ("id", "doc_id", "doc", "loc", "section",
                           "title", "text", "images")}
        for c in chunks if c["text"].strip()
    ]
    docs = []
    for c in chunks:
        if not any(d["doc_id"] == c["doc_id"] for d in docs):
            docs.append({"doc_id": c["doc_id"], "doc": c["doc"],
                         "kind": c["kind"],
                         "chunks": sum(1 for x in chunks
                                       if x["doc_id"] == c["doc_id"])})
    template = (ROOT / "templates" / "chat_page_template.html").read_text()
    out = (template
           .replace("__CHUNKS_DATA__", json.dumps(compact, ensure_ascii=True))
           .replace("__DOCS_DATA__", json.dumps(docs, ensure_ascii=True))
           .replace("__SYSTEM_PROMPT__", json.dumps(system_prompt())))
    dest = ROOT / "course_chat.html"
    dest.write_text(out)
    imgs = sum(len(c["images"]) for c in compact)
    print(f"wrote {dest.relative_to(ROOT)} — {len(compact)} chunks, "
          f"{len(docs)} docs, {imgs} evidence images, {len(out)} bytes")


if __name__ == "__main__":
    main()
