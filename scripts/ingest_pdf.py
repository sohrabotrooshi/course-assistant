#!/usr/bin/env python3
"""Ingest PDF course files (syllabus, handouts, guides) into chunks (v2).

Uses scripts/pdf_tool.swift (macOS PDFKit, compiled once into .tools/pdf_tool;
requires Xcode Command Line Tools — the one non-stdlib dependency, optional:
PDFs are simply skipped if the tool cannot be built). For each PDF:

- one chunk per page: text layer (page.string) + page render (JPEG) as the
  "original page image" evidence; sections come from the PDF's own outline
  (bookmarks), nearest preceding bookmark per page.
- scanned pages without a text layer are kept with an explicit "(scanned —
  no text layer)" marker: they are still browsable images, but not searchable.

Chunk schema: id (<doc_id>-p<N>), kind="page", doc, doc_id, loc, page,
section, title, text, images=[page render].

Usage:
    python3 scripts/ingest_pdf.py [PDF_PATH_OR_DIR ...] [-o OUT]
    (default: scan ~/CU Boulder/LLM and the repo's data/course_files/ for PDFs)
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOL = ROOT / ".tools" / "pdf_tool"
SWIFT_SRC = ROOT / "scripts" / "pdf_tool.swift"
MEDIA_DIR = ROOT / "data" / "media"
COURSE_FILES_DIR = ROOT / "data" / "course_files"

SCANNED_MARKER = "(scanned — no text layer)"

SLUG_RE = re.compile(r"[^a-z0-9]+")


def doc_slug(name):
    return SLUG_RE.sub("-", Path(name).stem.lower()).strip("-")


def build_tool():
    if TOOL.exists() and TOOL.stat().st_mtime >= SWIFT_SRC.stat().st_mtime:
        return True
    TOOL.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(["swiftc", "-O", str(SWIFT_SRC), "-o", str(TOOL)],
                       capture_output=True)
    if r.returncode != 0:
        print("swiftc failed:\n" + r.stderr.decode()[-800:], file=sys.stderr)
    return r.returncode == 0 and TOOL.exists()


def ingest_pdf(path):
    doc_id = doc_slug(path.name)
    doc = path.stem
    outdir = MEDIA_DIR / doc_id
    r = subprocess.run([str(TOOL), str(path), str(outdir)],
                       capture_output=True, text=True, timeout=600)
    if r.returncode != 0:
        raise RuntimeError(f"pdf_tool failed on {path}: {r.stderr[:300]}")
    data = json.loads(r.stdout)
    pages = data["pages"]
    sections = [(s["page"], s["label"]) for s in data.get("sections", [])]

    chunks = []
    for p in pages:
        n = p["page"]
        text = (p["text"] or "").strip()
        if not text:
            text = SCANNED_MARKER
        # nearest preceding section bookmark (in PDF page order)
        section = None
        for spage, label in sections:
            if spage <= n - 1:
                section = label
            else:
                break
        img = p.get("image")
        images = []
        if img and Path(img).exists():
            dest = MEDIA_DIR / doc_id / f"p{n:03d}.jpg"
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(img, dest)
            images.append(str(dest.relative_to(ROOT)))
        title = section or f"Page {n}"
        chunks.append({
            "id": f"{doc_id}-p{n}",
            "kind": "page",
            "doc": doc,
            "doc_id": doc_id,
            "loc": f"{doc}, page {n}",
            "week": None,
            "slide": None,
            "page": n,
            "section": section,
            "title": title,
            "text": text,
            "images": images,
        })
    return chunks


def find_pdfs(args):
    found = []
    for p in args:
        p = Path(p)
        if p.is_dir():
            found.extend(sorted(p.glob("*.pdf")))
        elif p.is_file():
            found.append(p)
    if not args:
        for d in (Path.home() / "CU Boulder" / "LLM", COURSE_FILES_DIR):
            if d.exists():
                found.extend(sorted(d.glob("*.pdf")))
    return [f for f in found if f.exists()]


def run(paths, out_path):
    if not build_tool():
        print("pdf_tool unavailable (needs Xcode Command Line Tools) — "
              "skipping PDF ingestion", file=sys.stderr)
        return []

    pdfs = find_pdfs(paths)
    if not pdfs:
        print("no PDFs found", file=sys.stderr)
        return []

    all_chunks = []
    for f in pdfs:
        chunks = ingest_pdf(f)
        size_mb = f.stat().st_size / 1e6
        searchable = sum(1 for c in chunks if SCANNED_MARKER not in c["text"])
        print(f"{f.name}: {len(chunks)} pages ({searchable} searchable), "
              f"{size_mb:.1f}MB source")
        all_chunks.extend(chunks)

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(all_chunks, indent=1, ensure_ascii=False))
    imgs = sum(len(c["images"]) for c in all_chunks)
    print(f"wrote {out} — {len(all_chunks)} chunks, {imgs} page renders")
    return all_chunks


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("paths", nargs="*", help="pdf files or directories")
    ap.add_argument("-o", "--out", default="data/pdf_chunks.json")
    args = ap.parse_args()
    run(args.paths, args.out)


if __name__ == "__main__":
    main()
