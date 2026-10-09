#!/usr/bin/env python3
"""Generate practice quiz questions anchored on the MBAX 6418 lecture slides.

For each week, a seeded sample of slides is picked; each slide produces
exactly one multiple-choice question that is answerable from that slide's
text alone (the model sees only the one slide, so the question cannot
require outside knowledge). Outputs are schema-validated and retried once on
failure; survivors land in results/quiz_questions.json with the answer key,
rationale, and the exact source slide for every question.

Usage:
    python3 scripts/generate_quiz.py [--per-week 6] [--seed 20261009]
                                     [--weeks 2,3,4,5,6] [-o results/quiz_questions.json]
"""

import argparse
import json
import random
import sys
import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from llm import chat_json, MODEL  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CHUNKS = json.loads((ROOT / "data" / "course_chunks.json").read_text())

SYSTEM = (
    "You write practice multiple-choice questions for MBAX 6418, a graduate "
    "LLM course. You are given ONE lecture slide. Write exactly one "
    "multiple-choice question that a student who read THIS slide can answer "
    "from THIS slide alone — no outside knowledge, no trivia. Ask about the "
    "slide's core teaching point. Give 4 answer options, exactly one correct, "
    "and a one-sentence rationale that quotes or paraphrases the slide fact "
    "supporting the correct answer. Distractors must be plausible but "
    "clearly wrong per the slide."
)


def quiz_prompt(chunk):
    return (
        f"SLIDE (chunk {chunk['id']}, Week {chunk['week']}, slide {chunk['slide']})\n"
        f"Title: {chunk['title']}\n\n{chunk['text']}\n\n"
        "Return JSON exactly:\n"
        "{\"question\": string, \"options\": [4 distinct strings], "
        "\"answer\": integer index of the correct option (0-3), "
        "\"rationale\": string, \"source_chunk_id\": string}"
    )


def validate(q, anchor_id):
    errs = []
    if not isinstance(q, dict):
        return False, ["not an object"]
    qq = (q.get("question") or "").strip()
    opts = [str(o).strip() for o in q.get("options", [])]
    ans, rat = q.get("answer"), (q.get("rationale") or "").strip()
    if not qq or len(qq) > 320:
        errs.append(f"question empty/too long ({len(qq)} chars)")
    if len(opts) != 4 or any(len(o) < 1 for o in opts):
        errs.append(f"need exactly 4 non-empty options, got {len(opts)}")
    elif len(set(o.lower() for o in opts)) < 4:
        errs.append("options are not distinct")
    if not isinstance(ans, int) or not (0 <= ans <= 3):
        errs.append(f"answer not an int in 0..3: {ans!r}")
    if len(rat) < 10:
        errs.append("rationale too short")
    if str(q.get("source_chunk_id")) != anchor_id:
        errs.append(f"source_chunk_id {q.get('source_chunk_id')!r} != {anchor_id!r}")
    return (not errs), errs


def make_question(chunk):
    for attempt in range(2):
        try:
            q = chat_json(
                [{"role": "system", "content": SYSTEM},
                 {"role": "user", "content": quiz_prompt(chunk)}],
                required_keys=["question", "options", "answer",
                               "rationale", "source_chunk_id"],
                max_tokens=700, temperature=0.0,
            )
        except ValueError as e:
            return None, f"parse failed: {e}"
        ok, errs = validate(q, chunk["id"])
        if ok:
            q["source_chunk_id"] = chunk["id"]
            return q, None
        if attempt == 0:
            continue  # retry once, then give up
        return None, "; ".join(errs)


def distribute_answers(weeks_out, base_seed):
    """Kill the model's answer-position bias by shuffling each question's
    options with a per-question seeded RNG (same seed -> same shuffle).
    Deterministic: re-running on the same questions yields the same layout."""
    for info in weeks_out.values():
        for q in info["questions"]:
            qrng = random.Random(base_seed + sum(ord(c) for c in q["question"]))
            order = list(range(len(q["options"])))
            qrng.shuffle(order)
            q["options"] = [q["options"][i] for i in order]
            q["answer"] = order.index(q["answer"])
    return weeks_out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--per-week", type=int, default=6)
    ap.add_argument("--seed", type=int, default=20261009)
    ap.add_argument("--weeks", default="2,3,4,5,6")
    ap.add_argument("-o", "--out", default="results/quiz_questions.json")
    args = ap.parse_args()

    weeks = [int(w) for w in args.weeks.split(",")]
    rng = random.Random(args.seed)

    by_week = {}
    for c in CHUNKS:
        by_week.setdefault(c["week"], []).append(c)

    out, meta_fail, stats = {}, {}, {}
    for w in weeks:
        pool = [c for c in by_week.get(w, []) if len(c["text"].split()) >= 8]
        pick = rng.sample(pool, min(args.per_week, len(pool)))
        questions, fails = [], []
        for c in pick:
            q, err = make_question(c)
            if q:
                questions.append(q)
            else:
                fails.append({"slide": c["id"], "error": err})
        deck = pool[0]["deck"] if pool else f"Week {w}"
        out[str(w)] = {"name": deck, "questions": questions}
        stats[str(w)] = {"slides_sampled": len(pick),
                         "questions_kept": len(questions),
                         "failed": fails}
        print(f"Week {w}: {len(questions)}/{len(pick)} questions kept "
              f"({len(fails)} failed)")

    result = {
        "meta": {"seed": args.seed, "per_week": args.per_week,
                 "model": MODEL,
                 "generated_at": datetime.datetime.now(
                     datetime.timezone.utc).isoformat(timespec="seconds")},
        "weeks": distribute_answers(out, args.seed),
    }
    out_path = ROOT / args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=1, ensure_ascii=False))
    total = sum(len(v["questions"]) for v in out.values())
    print(f"wrote {out_path.relative_to(ROOT)} — {total} questions kept")


if __name__ == "__main__":
    main()
