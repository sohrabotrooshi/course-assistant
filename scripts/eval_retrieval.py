#!/usr/bin/env python3
"""Evaluate retrieval quality on questions with known target sources.

Follows Week 5, slide 19: "Accurate RAG requires checking retrieval quality
... using questions relevant to your app." 19 hand-written questions (15
slide-targeted + 4 PDF-targeted), measured at each pipeline stage:

- bm25_top8: would the keyword-only leg (BM25 top 8) find the target?
- hybrid_top8: does reciprocal-rank fusion of BM25 + Nemotron embeddings
  (class port 9002) keep the target in its top 8?
- rerank_top4: does the dedicated reranker (class port 9004) keep the
  target in the final top 4?

Usage: python3 scripts/eval_retrieval.py [-o results/retrieval_metrics.json]
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from retrieve import load_index, retrieve  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

# (question, target chunk id) — targets chosen from the actual slide/page text
CASES = [
    ("What is quantization and how does it make models smaller?", "w2-s15"),
    ("How does a KV cache speed up decoding?", "w2-s11"),
    ("What does speculative decoding guess ahead and verify?", "w2-s13"),
    ("What is sycophancy and how do benchmarks measure it?", "w2-s20"),
    ("What is ChatML and how are prompts formatted in it?", "w3-s8"),
    ("What does the few-shot prompting example look like?", "w3-s21"),
    ("What is an OpenAI-compatible endpoint?", "w4-s12"),
    ("What makes a bug report useful?", "w4-s19"),
    ("What is retrieval augmented generation (RAG)?", "w5-s10"),
    ("Why is accurate RAG challenging?", "w5-s19"),
    ("What is the lost in the middle effect?", "w5-s7"),
    ("How do embeddings enable semantic search?", "w5-s15"),
    ("What are multimodal GenAI use cases?", "w6-s4"),
    ("Why is multimodal not the same as generative?", "w6-s7"),
    ("What should you weigh when choosing model licensing?", "w6-s17"),
    # PDF targets (Hermes guide + Assignment 1 handout + syllabus)
    ("How do you install Hermes Agent on macOS?",
     "hermes-agent-installation-guide-2026-v1-p1"),
    ("What should you check after the Windows installation finishes?",
     "hermes-agent-installation-guide-2026-v1-p9"),
    ("Which endpoint and model does the class recommend for Assignment 1 classification?",
     "mbax-6418-assignment-1-p2"),
    ("How are assignments graded in this course?",
     "mbax-6418-syllabus-fall-2026-1-p2"),
]


def rank_of(ids, target):
    return ids.index(target) + 1 if target in ids else None


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("-o", "--out", default="results/retrieval_metrics.json")
    args = ap.parse_args()

    index, _ = load_index()
    rows = []
    for q, target in CASES:
        bm25 = [c["id"] for c in index.search(q, k=8, use_vector=False)]
        hybrid = [c["id"] for c in index.search(q, k=8, use_vector=True)]
        reranked = [c["id"] for c in retrieve(q, index, top=8, rerank_top=4,
                                              use_rerank=True, use_vector=True)]
        bm25_reranked = [c["id"] for c in retrieve(q, index, top=8,
                                                   rerank_top=4,
                                                   use_rerank=True,
                                                   use_vector=False)]
        row = {
            "question": q,
            "target": target,
            "bm25_top8": bm25,
            "bm25_rank": rank_of(bm25, target),
            "hybrid_top8": hybrid,
            "hybrid_rank": rank_of(hybrid, target),
            "rerank_top4": reranked,
            "rerank_hit": target in reranked,
            "bm25_rerank_top4": bm25_reranked,
            "bm25_rerank_hit": target in bm25_reranked,
        }
        rows.append(row)
        tag = ("BM25+r" if row["bm25_rerank_hit"] and not row["rerank_hit"] else
               ("HIT " if row["rerank_hit"] else
                ("hybrid" if row["hybrid_rank"] else
                 ("bm25" if row["bm25_rank"] else "MISS"))))
        print(f"{tag:>6}  {q[:56]:<56} -> {target}  "
              f"(bm25 {row['bm25_rank']}, hybrid {row['hybrid_rank']}, "
              f"bm25+rerank {'in' if row['bm25_rerank_hit'] else 'out'})")

    n = len(rows)
    def hit(key_rank): return sum(1 for r in rows if r[key_rank] is not None)
    def mrr(key_rank): return round(
        sum(1.0 / r[key_rank] for r in rows if r[key_rank]) / n, 3)
    metrics = {
        "n_questions": n,
        "bm25_top8_hit": hit("bm25_rank"),
        "bm25_top8_rate": round(hit("bm25_rank") / n, 3),
        "bm25_mrr_top8": mrr("bm25_rank"),
        "hybrid_top8_hit": hit("hybrid_rank"),
        "hybrid_top8_rate": round(hit("hybrid_rank") / n, 3),
        "hybrid_mrr_top8": mrr("hybrid_rank"),
        "rerank_top4_hit": sum(1 for r in rows if r["rerank_hit"]),
        "rerank_top4_rate": round(sum(1 for r in rows if r["rerank_hit"]) / n, 3),
        "bm25_rerank_top4_hit": sum(1 for r in rows if r["bm25_rerank_hit"]),
        "bm25_rerank_top4_rate": round(
            sum(1 for r in rows if r["bm25_rerank_hit"]) / n, 3),
        "cases": rows,
    }
    out = ROOT / args.out
    out.write_text(json.dumps(metrics, indent=1, ensure_ascii=False))
    print(f"\nBM25 top-8: {metrics['bm25_top8_hit']}/{n} "
          f"(MRR {metrics['bm25_mrr_top8']})"
          f"  |  hybrid top-8: {metrics['hybrid_top8_hit']}/{n} "
          f"(MRR {metrics['hybrid_mrr_top8']})"
          f"  |  rerank top-4 (hybrid): {metrics['rerank_top4_hit']}/{n}"
          f"  |  rerank top-4 (BM25): {metrics['bm25_rerank_top4_hit']}/{n}")
    print(f"wrote {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
