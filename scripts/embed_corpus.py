#!/usr/bin/env python3
"""Embed the corpus with the class text-embeddings endpoint (port 9002).

Calls http://dobolyi.com:9002/v1/embeddings (nvidia/Nemotron-3-Embed-1B-BF16,
2048 dims) in batches and writes data/embeddings.json:

    {"dim": 2048, "model": "...", "vectors": {"<chunk_id>": [float, ...]}}

Vectors are rounded to 4 decimals (cosine error ~1e-4) to keep the file
light. Only non-empty (searchable) chunks are embedded.

Usage: python3 scripts/embed_corpus.py [-o data/embeddings.json]
Env:  LLM_EMBED_URL / LLM_EMBED_KEY to override.
"""

import argparse
import json
import os
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

EMBED_URL = os.environ.get("LLM_EMBED_URL", "http://dobolyi.com:9002/v1/embeddings")
EMBED_KEY = os.environ.get("LLM_EMBED_KEY", "6418")
EMBED_MODEL = os.environ.get("LLM_EMBED_MODEL", "nvidia/Nemotron-3-Embed-1B-BF16")
BATCH = 8


def embed_batch(texts):
    body = json.dumps({"model": EMBED_MODEL, "input": texts}).encode()
    req = urllib.request.Request(
        EMBED_URL, data=body,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {EMBED_KEY}"},
        method="POST")
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                data = json.loads(resp.read().decode())
            return [d["embedding"] for d in data["data"]]
        except Exception as e:
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"embedding request failed for {len(texts)} texts")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("-o", "--out", default="data/embeddings.json")
    ap.add_argument("--dims", action="store_true",
                    help="just print the endpoint's embedding dimension and exit")
    args = ap.parse_args()

    chunks = json.loads((ROOT / "data" / "course_chunks.json").read_text())
    rows = [(c["id"], c["title"] + "\n" + c["text"])
            for c in chunks if c["text"].strip()]
    if args.dims:
        v = embed_batch(["probe"])[0]
        print(f"embedding dim = {len(v)}  ({EMBED_MODEL})")
        return

    vectors = {}
    for i in range(0, len(rows), BATCH):
        batch = rows[i:i + BATCH]
        vecs = embed_batch([t for _, t in batch])
        for (cid, _), v in zip(batch, vecs):
            vectors[cid] = [round(x, 4) for x in v]
        print(f"embedded {min(i + BATCH, len(rows))}/{len(rows)} "
              f"({len(vectors)} vectors)")
    out = {"dim": len(next(iter(vectors.values()))),
           "model": EMBED_MODEL, "vectors": vectors}
    dest = ROOT / args.out
    dest.write_text(json.dumps(out))
    print(f"wrote {dest.relative_to(ROOT)} — {len(vectors)} vectors, "
          f"dim {out['dim']}, {dest.stat().st_size/1e6:.1f}MB")


if __name__ == "__main__":
    main()
