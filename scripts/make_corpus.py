#!/usr/bin/env python3
"""Build the full course corpus: merge slide chunks + PDF chunks.

Reads data/slides_chunks.json + data/pdf_chunks.json and writes
data/course_chunks.json (the single corpus every tool reads), plus a
data/corpus_meta.json with per-document stats.

Usage: python3 scripts/make_corpus.py [--slides-dir DIR] [--pdf ...]
   --pdf accepts files/dirs (extra), default: ~/CU Boulder/LLM + data/course_files/
   (same defaults as the two ingest scripts)
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(Path(__file__).resolve().parent))

import ingest_slides, ingest_pdf  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--slides-dir", default=None,
                    help="pptx dir (default: ~/CU Boulder/LLM)")
    ap.add_argument("--pdf", nargs="*", default=[],
                    help="extra pdf files/dirs (default: ~/CU Boulder/LLM + data/course_files)")
    args = ap.parse_args()

    slides_dir = args.slides_dir or str(Path.home() / "CU Boulder" / "LLM")
    slides_json = ROOT / "data" / "slides_chunks.json"
    pdf_json = ROOT / "data" / "pdf_chunks.json"

    ingest_slides.run(slides_dir, slides_json)
    print()
    ingest_pdf.run(args.pdf, pdf_json)
    print()

    chunks = (json.loads(slides_json.read_text())
              + json.loads(pdf_json.read_text()))
    dest = ROOT / "data" / "course_chunks.json"
    dest.write_text(json.dumps(chunks, indent=1, ensure_ascii=False))

    docs = {}
    for c in chunks:
        docs.setdefault(c["doc_id"], {"doc": c["doc"], "kind": [], "chunks": 0,
                                      "images": 0, "searchable": 0})
        d = docs[c["doc_id"]]
        d["kind"].append(c["kind"])
        d["chunks"] += 1
        d["images"] += len(c["images"])
        if "scanned —" not in c["text"]:
            d["searchable"] += 1
    for d in docs.values():
        d["kind"] = sorted(set(d["kind"]))

    meta = {
        "documents": docs,
        "total_chunks": len(chunks),
        "total_images": sum(d["images"] for d in docs.values()),
        "media_dir": "data/media",
    }
    (ROOT / "data" / "corpus_meta.json").write_text(
        json.dumps(meta, indent=1, ensure_ascii=False))

    print(f"=== corpus: {len(chunks)} chunks across {len(docs)} docs ===")
    for doc_id, d in docs.items():
        print(f"  {doc_id:14} {d['kind'][0]:5} chunks={d['chunks']:4} "
              f"images={d['images']:4} searchable={d['searchable']}")


if __name__ == "__main__":
    main()
