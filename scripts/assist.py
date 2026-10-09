#!/usr/bin/env python3
"""Course assistant CLI — answer questions from the MBAX 6418 slides.

Pipeline (matches Week 5, slide 18): parse/chunk (ingest_slides.py), BM25
keyword retrieval, LLM reranking, grounded answer generation. The prompt
lives in prompts/assistant_prompt.md.

Usage:
    python3 scripts/assist.py --once "What is quantization?"   # one question
    python3 scripts/assist.py                                  # REPL
    python3 scripts/assist.py --once "Q" --save                # append to
                            # results/qa_samples.json (quotes + sources)
    python3 scripts/assist.py --once "Q" --no-rerank           # BM25 only
"""

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from llm import chat                      # noqa: E402
from prompt_source import system_prompt   # noqa: E402
from retrieve import load_index, retrieve  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SAMPLES_FILE = ROOT / "results" / "qa_samples.json"


def format_context(chunks):
    lines = ["CONTEXT"]
    for c in chunks:
        lines.append(f"[Week {c['week']}, slide {c['slide']}] "
                     f"{c['title']} (chunk {c['id']})\n{c['text']}")
    return "\n\n".join(lines)


def answer(question, index, top=8, rerank_top=4, use_rerank=True,
           max_tokens=900):
    chunks = retrieve(question, index, top=top, rerank_top=rerank_top,
                      use_rerank=use_rerank)
    if not chunks:
        return ("The course materials don't cover that — nothing in the "
                "lecture slides matches your question.", [])
    user = f"{format_context(chunks)}\n\nQUESTION\n{question}"
    messages = [{"role": "system", "content": system_prompt()},
                {"role": "user", "content": user}]
    text = chat(messages, max_tokens=max_tokens, temperature=0.0)
    return text, chunks


def save_sample(question, answer_text, chunks):
    samples = []
    if SAMPLES_FILE.exists():
        samples = json.loads(SAMPLES_FILE.read_text())
    samples.append({
        "question": question,
        "answer": answer_text,
        "sources": [{"id": c["id"], "week": c["week"],
                     "slide": c["slide"], "title": c["title"]}
                    for c in chunks],
    })
    SAMPLES_FILE.parent.mkdir(parents=True, exist_ok=True)
    SAMPLES_FILE.write_text(json.dumps(samples, indent=1, ensure_ascii=False))
    print(f"(appended to {SAMPLES_FILE.relative_to(ROOT)})")


def repl(index):
    print("MBAX 6418 course assistant — ask about the lecture slides.")
    print("(Ctrl-D or 'quit' to exit)\n")
    while True:
        try:
            q = input("ask> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not q or q.lower() in ("quit", "exit"):
            return
        text, chunks = answer(q, index)
        print(f"\n{text}\n")
        if chunks:
            print("Sources: " + ", ".join(
                f"[Week {c['week']}, slide {c['slide']}]" for c in chunks))
        print()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--once", help="answer a single question and exit")
    ap.add_argument("--top", type=int, default=8, help="BM25 candidates")
    ap.add_argument("--rerank", type=int, default=4, help="chunks after rerank")
    ap.add_argument("--no-rerank", action="store_true")
    ap.add_argument("--save", action="store_true",
                    help="append the Q/A to results/qa_samples.json")
    args = ap.parse_args()

    index, _ = load_index()
    if args.once:
        text, chunks = answer(args.once, index, top=args.top,
                              rerank_top=args.rerank,
                              use_rerank=not args.no_rerank)
        print(text)
        if chunks:
            print("\nSources: " + ", ".join(
                f"[Week {c['week']}, slide {c['slide']}]" for c in chunks))
        if args.save:
            save_sample(args.once, text, chunks)
    else:
        repl(index)


if __name__ == "__main__":
    main()
