#!/usr/bin/env python3
"""Retrieval over the course corpus: hybrid BM25 + embeddings + reranker.

Follows Week 5, slide 18's hybrid-RAG pipeline literally, using the class's
own endpoints:

  1. KEYWORD leg — pure-stdlib BM25 over slide/page chunks.
  2. VECTOR leg — cosine search over Nemotron-3-Embed-1B embeddings
     (class port 9002), produced by scripts/embed_corpus.py ->
     data/embeddings.json.
  3. FUSION — reciprocal rank fusion of the two candidate lists.
  4. RERANK — the dedicated Qwen3-VL reranker (class port 9004 /v1/rerank)
     reorders the fused candidates; falls back to an LLM-chat reranker if
     the rerank endpoint is unreachable.

The index is rebuilt in memory per call (tiny corpus; instant).

Usage:
    python3 scripts/retrieve.py --query "what is quantization?" [--top 5]
        [--docs week04,week05] [--topic quantization] [--no-vector] [--no-rerank]
        prints ranked chunk ids with locations and snippets
"""

import argparse
import json
import math
import os
import re
import sys
import time
import urllib.request
from pathlib import Path

try:
    from llm import chat_json
except ImportError:
    from scripts.llm import chat_json  # allow running from repo root

CHUNKS_PATH = Path(__file__).resolve().parent.parent / "data" / "course_chunks.json"
EMBEDDINGS_PATH = Path(__file__).resolve().parent.parent / "data" / "embeddings.json"

RERANK_URL = os.environ.get("LLM_RERANK_URL", "http://dobolyi.com:9004/v1/rerank")
RERANK_KEY = os.environ.get("LLM_RERANK_KEY", "6418")
RERANK_MODEL = os.environ.get("LLM_RERANK_MODEL", "Qwen/Qwen3-VL-Reranker-2B")
EMBED_URL = os.environ.get("LLM_EMBED_URL", "http://dobolyi.com:9002/v1/embeddings")
EMBED_KEY = os.environ.get("LLM_EMBED_KEY", "6418")
EMBED_MODEL = os.environ.get("LLM_EMBED_MODEL", "nvidia/Nemotron-3-Embed-1B-BF16")

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


def embed_texts(texts):
    """One-shot embedding call (class port 9002). Returns list of vectors."""
    body = json.dumps({"model": EMBED_MODEL, "input": texts}).encode()
    req = urllib.request.Request(
        EMBED_URL, data=body,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {EMBED_KEY}"},
        method="POST")
    last = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                data = json.loads(resp.read().decode())
            return [d["embedding"] for d in data["data"]]
        except Exception as e:
            last = e
            time.sleep(1.5 * (attempt + 1))
    print(f"[retrieve] embedding query failed: {last}", file=sys.stderr)
    return None


def cosine(a, b):
    num = denom_a = denom_b = 0.0
    for x, y in zip(a, b):
        num += x * y
        denom_a += x * x
        denom_b += y * y
    return num / ((denom_a * denom_b) ** 0.5 or 1.0)


class VectorIndex:
    """Cosine search over precomputed embeddings (data/embeddings.json)."""

    def __init__(self, chunks, vectors, dim):
        by_id = {c["id"]: c for c in chunks}
        self.ids, self.chunks, self.vecs = [], [], []
        for cid, vec in vectors.items():
            if cid in by_id:
                self.ids.append(cid)
                self.chunks.append(by_id[cid])
                self.vecs.append(vec)
        self.dim = dim

    def search(self, query, k=10):
        qv = embed_texts([query])
        if not qv:
            return []
        scored = sorted(
            ((i, cosine(qv[0], self.vecs[i])) for i in range(len(self.ids))),
            key=lambda x: x[1], reverse=True)
        return [(self.chunks[i], s) for i, s in scored[:k]]


def rrf(bm25_chunks, vec_chunks, k=60, top=10):
    """Reciprocal rank fusion of two chunk-ranked lists (Cormack et al. 2009).
    Returns the top fused chunk objects, best first."""
    scores = {}
    for rank, chunks in enumerate((bm25_chunks, vec_chunks)):
        for pos, c in enumerate(chunks):
            scores[c["id"]] = scores.get(c["id"], 0.0) + 1.0 / (k + pos + 1)
    order = [cid for cid, _ in sorted(scores.items(), key=lambda x: x[1],
                                      reverse=True)][:top]
    by_id = {}
    for c in bm25_chunks + vec_chunks:
        by_id.setdefault(c["id"], c)
    return [by_id[cid] for cid in order if cid in by_id]


class HybridIndex:
    """Keyword + vector index fused by reciprocal rank fusion."""

    def __init__(self, chunks, vectors, dim):
        self.chunks = [c for c in chunks if c["text"].strip()]
        self.bm25 = BM25([tokenize(c["text"] + " " + c["title"])
                          for c in self.chunks])
        self.vec = VectorIndex(self.chunks, vectors, dim) if vectors else None

    def search(self, query, k=10, use_vector=True):
        bm25_hits = self.bm25.search(query, 30)
        bm25 = [self.chunks[i] for i, _ in bm25_hits]
        if use_vector and self.vec:
            vec = [c for c, _ in self.vec.search(query, 30)]
            if vec:
                return rrf(bm25, vec, top=k)
        return bm25[:k]


def filter_chunks(chunks, doc_ids=None, topic=None):
    """Restrict the corpus by document(s) and/or a topic keyword filter.
    - doc_ids: set/list of doc_id values (e.g. {"week04", "syllabus"}).
    - topic: string; every non-stopword token must appear in a chunk's
      title+text tokens (a plain keyword intersection, honest and fast)."""
    if doc_ids:
        doc_ids = set(doc_ids)
        chunks = [c for c in chunks if c.get("doc_id") in doc_ids]
    if topic and topic.strip():
        need = tokenize(topic)
        if need:
            chunks = [c for c in chunks
                      if all(t in set(tokenize(c["title"] + " " + c["text"]))
                             for t in need)]
    return chunks


def rerank_via_endpoint(question, candidates, top=5):
    """Dedicated reranker (class port 9004, Qwen3-VL-Reranker-2B):
    relevance scores per candidate; returns the top `top` chunks, best
    first. Fallback: LLM-chat rerank (rerank()) if the endpoint fails."""
    body = json.dumps({"model": RERANK_MODEL, "query": question,
                       "documents": [c["text"][:900] for c in candidates]}).encode()
    req = urllib.request.Request(
        RERANK_URL, data=body,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {RERANK_KEY}"},
        method="POST")
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read().decode())
    scored = sorted(data.get("results", []),
                    key=lambda r: r.get("relevance_score", 0.0), reverse=True)
    picked = [r["index"] for r in scored][:top]
    return [candidates[i] for i in picked if 0 <= i < len(candidates)]


def rerank(question, candidates, top=5):
    """LLM reranker: given the question and candidate chunks (id + loc),
    return the ids of the most relevant `top` chunks, best first.
    Follows course Week 5 s18: reranking compares candidates to the question.
    """
    listing = "\n".join(
        f"- id={c['id']} | {c['loc']} | {c['title'][:90]}"
        for c in candidates
    )
    messages = [
        {"role": "system", "content":
         "You are a retrieval reranker for a course Q&A assistant. "
         "Given a student's question and a list of chunks (slides/pages), "
         "return the chunk ids of the MOST relevant chunks, most relevant "
         "first. Pick only ids that plausibly help answer the question; you "
         "may return an empty list if none do."},
        {"role": "user", "content":
         f"QUESTION: {question}\n\nCANDIDATE CHUNKS:\n{listing}\n\n"
         f"Return JSON: {{\"chunk_ids\": [\"id\", ...]}} with at most {top} ids."},
    ]
    obj = chat_json(messages, required_keys=["chunk_ids"], max_tokens=300)
    ids = [str(x) for x in obj.get("chunk_ids", [])][:top]
    by_id = {c["id"]: c for c in candidates}
    return [by_id[i] for i in ids if i in by_id]


def retrieve(question, index, top=8, rerank_top=5, use_rerank=True,
             use_vector=False):
    """Full pipeline: search top-`top` -> rerank to `rerank_top`.

    Measured default (eval_retrieval.py, 19 target questions): BM25-only
    recall is 19/19 top-8; fusing the embedding leg drops it to 16/19 on
    this small corpus, so the vector leg is opt-in (use_vector=True)
    rather than the default. The reranker (class port 9004) is applied on
    top of the chosen candidate list."""
    cands = index.search(question, k=top, use_vector=use_vector)
    if not cands:
        return []
    if use_rerank and len(cands) > rerank_top:
        try:
            return (rerank_via_endpoint(question, cands, top=rerank_top)
                    or cands[:rerank_top])
        except Exception as e:
            print(f"[retrieve] rerank endpoint failed ({e}); "
                  f"trying LLM rerank", file=sys.stderr)
            try:
                return rerank(question, cands, top=rerank_top) or cands[:rerank_top]
            except Exception as e2:
                print(f"[retrieve] LLM rerank failed too: {e2}",
                      file=sys.stderr)
                return cands[:rerank_top]
    return cands[:rerank_top]


def load_index(chunks_path=CHUNKS_PATH, doc_ids=None, topic=None,
               with_vectors=True):
    chunks = json.loads(Path(chunks_path).read_text())
    chunks = filter_chunks(chunks, doc_ids=doc_ids, topic=topic)
    vectors, dim = {}, None
    if with_vectors and EMBEDDINGS_PATH.exists():
        try:
            emb = json.loads(EMBEDDINGS_PATH.read_text())
            vectors, dim = emb.get("vectors", {}), emb.get("dim")
        except json.JSONDecodeError:
            pass
    return HybridIndex(chunks, vectors, dim), chunks


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--query", required=True)
    ap.add_argument("--top", type=int, default=5)
    ap.add_argument("--no-rerank", action="store_true")
    ap.add_argument("--vector", action="store_true",
                    help="enable the embedding leg (BM25+vector fusion; "
                         "off by default — see eval_retrieval.py)")
    ap.add_argument("--docs", help="comma-separated doc_ids to restrict to")
    ap.add_argument("--topic", help="topic keyword filter")
    args = ap.parse_args()

    docs = [d.strip() for d in args.docs.split(",")] if args.docs else None
    index, _ = load_index(doc_ids=docs, topic=args.topic,
                          with_vectors=args.vector)
    results = retrieve(args.query, index, top=8, rerank_top=args.top,
                       use_rerank=not args.no_rerank,
                       use_vector=args.vector)
    if not results:
        print("no chunks matched")
        sys.exit(0)
    for i, c in enumerate(results, 1):
        snippet = c["text"].replace("\n", " ")[:160]
        print(f"{i}. {c['id']}  ({c['loc']}) "
              f"{c['title'][:60]}\n   {snippet}")


if __name__ == "__main__":
    main()
