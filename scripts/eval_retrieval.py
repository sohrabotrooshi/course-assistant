#!/usr/bin/env python3
"""Evaluate retrieval quality on questions with known target slides.

Follows Week 5, slide 19: "Accurate RAG requires checking retrieval quality
... using questions relevant to your app." 15 hand-written questions, each
with the slide the answer must come from; measures whether BM25 finds the
target in its top 8 and whether reranking brings it into the top 4.

Usage: python3 scripts/eval_retrieval.py [-o results/retrieval_metrics.json]
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from llm import chat_json  # noqa: E402
from retrieve import load_index, retrieve  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

# (question, target chunk id) — targets chosen from the actual slide content
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
        bm25 = [c["id"] for c, _ in index.search(q, k=8)]
        ranked = [c["id"] for c in retrieve(q, index, top=8, rerank_top=4,
                                            use_rerank=True)]
        rows.append({
            "question": q,
            "target": target,
            "bm25_rank": rank_of(bm25, target),
            "bm25_top8": bm25,
            "rerank_top4": ranked,
            "rerank_hit": target in ranked,
        })
        mark = "HIT" if rows[-1]["rerank_hit"] else ("bm25" if rows[-1]["bm25_rank"] else "MISS")
        print(f"{mark:>5}  {q[:58]:<58} -> {target}  "
              f"(bm25 rank {rows[-1]['bm25_rank']}, rerank top4: "
              f"{'yes' if rows[-1]['rerank_hit'] else 'no'})")

    bm25_hit8 = sum(1 for r in rows if r["bm25_rank"] is not None)
    rerank_hit4 = sum(1 for r in rows if r["rerank_hit"])
    mrr = sum(1.0 / r["bm25_rank"] for r in rows if r["bm25_rank"]) / len(rows)

    metrics = {
        "n_questions": len(rows),
        "bm25_top8_hit": bm25_hit8,
        "bm25_top8_rate": round(bm25_hit8 / len(rows), 3),
        "rerank_top4_hit": rerank_hit4,
        "rerank_top4_rate": round(rerank_hit4 / len(rows), 3),
        "bm25_mrr_top8": round(mrr, 3),
        "cases": rows,
    }
    out = ROOT / args.out
    out.write_text(json.dumps(metrics, indent=1, ensure_ascii=False))
    print(f"\nBM25 top-8: {bm25_hit8}/{len(rows)}  |  rerank top-4: "
          f"{rerank_hit4}/{len(rows)}  |  BM25 MRR@8: {mrr:.3f}")
    print(f"wrote {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
