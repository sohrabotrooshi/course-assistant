#!/usr/bin/env python3
"""Ingest MBAX 6418 lecture decks (.pptx) into a chunked JSON corpus.

Stdlib only: pptx files are zips of XML. Each slide becomes one chunk
tagged with week / deck / slide number / title / text. Slide order is
resolved from presentation.xml (sldIdLst -> rels), not zip order.

Usage:
    python3 scripts/ingest_slides.py [SLIDES_DIR] [-o OUT]
    (defaults: slides dir = ~/CU Boulder/LLM, out = data/course_chunks.json)
"""

import argparse
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
P_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
A_T = f"{{{A_NS}}}t"
A_P = f"{{{A_NS}}}p"
P_SP = f"{{{P_NS}}}sp"
P_GRPSP = f"{{{P_NS}}}grpSp"
P_GRAPHICFRAME = f"{{{P_NS}}}graphicFrame"
P_CNVPR = f"{{{P_NS}}}cNvPr"


def extract_paragraphs(sp_el):
    """Return [para_text, ...] for one shape, preserving paragraph breaks."""
    paras = []
    for p in sp_el.iter(A_P):
        text = "".join(t.text or "" for t in p.iter(A_T)).strip()
        if text:
            paras.append(text)
    return paras


def shape_texts(slide_el):
    """All paragraph texts in a slide, in shape order (sp, grpSp, graphicFrame)."""
    texts = []
    for sp in slide_el.iter(P_SP):
        for para in extract_paragraphs(sp):
            texts.append(para)
    return texts


def slide_title(texts):
    """Heuristic: first short paragraph that looks like a slide title."""
    for t in texts:
        if t and len(t) <= 120 and not re.search(r"@|http|www\.", t):
            return t
    return texts[0][:120] if texts else ""


def slide_order(zf, slides_dir):
    """True slide order from presentation.xml sldIdLst + rels."""
    pres = ET.fromstring(zf.read("ppt/presentation.xml"))
    ids = [el.attrib.get(f"{{{R_NS}}}id") for el in pres.iter(f"{{{P_NS}}}sldId")]
    rels = ET.fromstring(zf.read("ppt/_rels/presentation.xml.rels"))
    rid_to_target = {}
    for rel in rels:
        if rel.attrib.get("Type", "").endswith("/slide"):
            rid_to_target[rel.attrib["Id"]] = rel.attrib["Target"]
    order = []
    for rid in ids:
        target = rid_to_target.get(rid)
        if not target:
            continue
        name = os.path.basename(target)
        if name in zf.namelist():
            order.append(name)
        else:  # target may be absolute-ish like /ppt/slides/slide1.xml
            fixed = target.lstrip("/")
            if fixed in zf.namelist():
                order.append(fixed)
    return order


def parse_week_from_filename(name):
    m = re.search(r"Week\s+(\d+)", name)
    return int(m.group(1)) if m else None


def ingest_deck(path):
    deck_name = os.path.basename(path)
    week = parse_week_from_filename(deck_name)
    chunks = []
    with zipfile.ZipFile(path) as zf:
        slides_dir = "ppt/slides"
        order = slide_order(zf, slides_dir)
        if not order:  # fallback: numeric filename sort
            order = sorted(
                n for n in zf.namelist()
                if re.match(rf"{re.escape(slides_dir)}/slide\d+\.xml$", n)
            )
        for n in order:
            m = re.search(r"slide(\d+)\.xml$", n)
            slide_num = int(m.group(1)) if m else 0
            root = ET.fromstring(zf.read(n))
            texts = shape_texts(root)
            text = "\n".join(texts)
            title = slide_title(texts)
            chunks.append({
                "id": f"w{week}-s{slide_num}",
                "week": week,
                "deck": deck_name,
                "slide": slide_num,
                "title": title,
                "text": text,
            })
    return chunks


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("slides_dir", nargs="?", default=str(Path.home() / "CU Boulder" / "LLM"))
    ap.add_argument("-o", "--out", default="data/course_chunks.json")
    args = ap.parse_args()

    decks = sorted(Path(args.slides_dir).glob("MBAX 6418 - Week *.pptx"))
    if not decks:
        print(f"no 'MBAX 6418 - Week *.pptx' decks in {args.slides_dir}", file=sys.stderr)
        sys.exit(1)

    all_chunks = []
    for d in decks:
        chunks = ingest_deck(d)
        nonempty = [c for c in chunks if c["text"].strip()]
        print(f"{d.name}: {len(chunks)} slides, {len(nonempty)} with text, "
              f"{sum(len(c['text'].split()) for c in nonempty)} words")
        all_chunks.extend(chunks)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(all_chunks, indent=1, ensure_ascii=False))
    n = len(all_chunks)
    kept = sum(1 for c in all_chunks if c["text"].strip())
    print(f"wrote {out} — {n} chunks, {kept} indexed (non-empty)")


if __name__ == "__main__":
    main()
