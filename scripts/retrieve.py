#!/usr/bin/env python3
"""Retrieval over the course corpus: BM25 (pure stdlib) + LLM reranking.

The class endpoint serves chat completions only (no /v1/embeddings — verified
in Assignment 2 setup), so retrieval is lexical BM25 over slide-level chunks,
followed by an LLM reranking pass that emulates the role of semantic/vector
search. This follows Week 5, slide 18: keyword retrieval -> candidate pool ->
rerank -> top context for the LLM.

The index is rebuilt from data/course_chunks.json when it changes (a tiny
corpus, so an in-memory rebuild per call is instant; no persisted index file).

Usage:
    python3 scripts/retrieve.py --query "what is quantization?" [--top 5]
        prints ranked chunk ids with scores and the chunk snippet
"""

import argparse
import json
import math
import re
import sys
from pathlib import Path

try:
    from llm import chat_json
except ImportError:
    from scripts.llm import chat_json  # allow running from repo root

CHUNKS_PATH = Path(__file__).resolve().parent.parent / "data" / "course_chunks.json"

_TOKEN_RE = re.compile(r"[a-z0-9]+")

STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with",
    "is", "are", "was", "be", "by", "at", "it", "its", "this", "that",
    "these", "those", "from", "as", "into", "how", "what", "why", "does",
    "do", "can", "you", "your", "we", "our", "i", "me", "us",
}


def tokenize(text):
    return [t for t in _TOKEN_RE.findall(text.lower()) if t not in STOPWORDS]


class BM25:
    """Okapi BM25 over pre-tokenized documents. k1=1.5, b=0.75 (Slides week 5
    only require lexical matching; these are standard defaults)."""

    def __init__(self, docs, k1=1.5, b=0.75):
        self.docs = docs
        self.k1, self.b = k1, b
        self.doc_freqs = []
        self.df = {}
        self.avgdl = 0.0
        total = 0
        for toks in docs:
            tfd = {}
            for t in toks:
                tfd[t] = tfd.get(t, 0) + 1
            self.doc_freqs.append(tfd)
            for t in tfd:
                self.df[t] = self.df.get(t, 0) + 1
            total += len(toks)
        self.N = len(docs)
        self.avgdl = total / max(1, self.N)

    def _idf(self, term):
        df = self.df.get(term, 0)
        return math.log(1 + (self.N - df + 0.5) / (df + 0.5))

    def score(self, q_toks, doc_idx):
        tfd, dl = self.doc_freqs[doc_idx], sum(self.doc_freqs[doc_idx].values())
        denom = self.k1 * (1 - self.b + self.b * dl / self.avgdl)
        return sum(
            self._idf(t) * tfd.get(t, 0) * (self.k1 + 1) / (tfd.get(t, 0) + denom)
            for t in q_toks
        )

    def search(self, query, k=10):
        q_toks = tokenize(query)
        if not q_toks:
            q_toks = tokenize(query) or [" "]
        scored = sorted(
            ((i, self.score(q_toks, i)) for i in range(self.N)),
            key=lambda x: x[1], reverse=True,
        )
        return [(i, s) for i, s in scored if s > 0][:k]


class CourseIndex:
    def __init__(self, chunks):
        self.chunks = [c for c in chunks if c["text"].strip()]
        corpus = [c["text"] + " " + c["title"] for c in self.chunks]
        self.bm25 = BM25([tokenize(d) for d in corpus])

    def search(self, query, k=10):
        return [(self.chunks[i], s) for i, s in self.bm25.search(query, k)]


def rerank(question, candidates, top=5):
    """LLM reranker: given the question and candidate chunks (id + title),
    return the ids of the most relevant `top` chunks, best first.
    Follows course Week 5 s18: reranking compares candidates to the question.
    """
    listing = "\n".join(
        f"- id={c['id']} | Week {c['week']}, slide {c['slide']} | "
        f"{c['title'][:90]}"
        for c in candidates
    )
    messages = [
        {"role": "system", "content":
         "You are a retrieval reranker for a course Q&A assistant. "
         "Given a student's question and a list of slide chunks, return the "
         "chunk ids of the MOST relevant chunks, most relevant first. "
         "Pick only ids that plausibly help answer the question; you may "
         "return an empty list if none do."},
        {"role": "user", "content":
         f"QUESTION: {question}\n\nCANDIDATE CHUNKS:\n{listing}\n\n"
         f"Return JSON: {{\"chunk_ids\": [\"id\", ...]}} with at most {top} ids."},
    ]
    obj = chat_json(messages, required_keys=["chunk_ids"], max_tokens=300)
    ids = [str(x) for x in obj.get("chunk_ids", [])][:top]
    by_id = {c["id"]: c for c in candidates}
    return [by_id[i] for i in ids if i in by_id]


def retrieve(question, index, top=8, rerank_top=5, use_rerank=True):
    """Full pipeline: BM25 top-`top` -> (optional) LLM rerank to
    `rerank_top` -> final ordered chunk list."""
    hits = index.search(question, k=top)
    if not hits:
        return []
    cands = [c for c, _ in hits]
    if use_rerank and len(cands) > rerank_top:
        try:
            return rerank(question, cands, top=rerank_top) or cands[:rerank_top]
        except Exception as e:
            print(f"[retrieve] rerank failed, using BM25 order: {e}",
                  file=sys.stderr)
            return cands[:rerank_top]
    return cands[:rerank_top]


def load_index(chunks_path=CHUNKS_PATH):
    chunks = json.loads(Path(chunks_path).read_text())
    return CourseIndex(chunks), chunks


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--query", required=True)
    ap.add_argument("--top", type=int, default=5)
    ap.add_argument("--no-rerank", action="store_true")
    args = ap.parse_args()

    index, _ = load_index()
    results = retrieve(args.query, index, top=8, rerank_top=args.top,
                       use_rerank=not args.no_rerank)
    if not results:
        print("no chunks matched")
        sys.exit(0)
    for i, c in enumerate(results, 1):
        snippet = c["text"].replace("\n", " ")[:160]
        print(f"{i}. {c['id']}  (Week {c['week']}, slide {c['slide']}) "
              f"{c['title'][:60]}\n   {snippet}")


if __name__ == "__main__":
    main()
