#!/usr/bin/env python3
"""Build the self-contained practice-quiz page (quiz_practice.html).

Bakes results/quiz_questions.json (one group per document), the referenced
source metadata (loc, section, text, images), and the corpus document list +
compact chunks (for in-page quiz generation) into
templates/quiz_page_template.html. The bank works offline; generating a new
quiz needs the endpoint (key entered once, kept in localStorage).

Usage: python3 scripts/build_quiz_page.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from llm import MODEL  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def main():
    quiz = json.loads((ROOT / "results" / "quiz_questions.json").read_text())
    chunks = json.loads((ROOT / "data" / "course_chunks.json").read_text())
    by_id = {c["id"]: c for c in chunks}

    quiz["meta"]["model"] = MODEL

    sources = {}
    for _doc_id, info in quiz["docs"].items():
        for q in info["questions"]:
            sid = q["source_chunk_id"]
            c = by_id.get(sid)
            if c:
                sources[sid] = {k: c[k] for k in
                                ("title", "text", "loc", "section", "images")}

    docs = []
    for c in chunks:
        if not any(d["doc_id"] == c["doc_id"] for d in docs):
            docs.append({"doc_id": c["doc_id"], "doc": c["doc"],
                         "kind": c["kind"],
                         "chunks": sum(1 for x in chunks
                                       if x["doc_id"] == c["doc_id"])})

    compact = [
        {k: c[k] for k in ("id", "doc_id", "doc", "loc", "section",
                           "title", "text")}
        for c in chunks if c["text"].strip()
    ]

    template = (ROOT / "templates" / "quiz_page_template.html").read_text()
    out = (template
           .replace("__QUIZ_DATA__", json.dumps(quiz, ensure_ascii=True))
           .replace("__SOURCES_DATA__", json.dumps(sources, ensure_ascii=True))
           .replace("__DOCS_DATA__", json.dumps(docs, ensure_ascii=True))
           .replace("__CHUNKS_DATA__", json.dumps(compact, ensure_ascii=True)))
    dest = ROOT / "quiz_practice.html"
    dest.write_text(out)
    n = sum(len(info["questions"]) for info in quiz["docs"].values())
    print(f"wrote {dest.relative_to(ROOT)} — {n} questions, "
          f"{len(docs)} docs, {len(sources)} sources, {len(out)} bytes")


if __name__ == "__main__":
    main()
