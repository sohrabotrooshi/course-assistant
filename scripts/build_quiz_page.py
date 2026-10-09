#!/usr/bin/env python3
"""Build the self-contained practice-quiz page (quiz_practice.html).

Bakes results/quiz_questions.json + the referenced slide chunks into
templates/quiz_page_template.html. Output works offline in any browser.

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
    for wk, info in quiz["weeks"].items():
        for q in info["questions"]:
            sid = q["source_chunk_id"]
            c = by_id.get(sid)
            if c:
                sources[sid] = {k: c[k] for k in ("title", "text", "slide")}
                sources[sid]["week"] = c["week"]

    template = (ROOT / "templates" / "quiz_page_template.html").read_text()
    out = (template
           .replace("__QUIZ_DATA__", json.dumps(quiz, ensure_ascii=True))
           .replace("__SOURCES_DATA__", json.dumps(sources, ensure_ascii=True)))
    dest = ROOT / "quiz_practice.html"
    dest.write_text(out)
    n = sum(len(info["questions"]) for info in quiz["weeks"].values())
    print(f"wrote {dest.relative_to(ROOT)} — {n} questions, "
          f"{len(sources)} source slides, {len(out)} bytes")


if __name__ == "__main__":
    main()
