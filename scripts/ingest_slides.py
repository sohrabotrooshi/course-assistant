#!/usr/bin/env python3
"""Ingest MBAX 6418 lecture decks (.pptx) into a chunked JSON corpus (v2).

Stdlib only: pptx files are zips of XML. Each slide becomes one chunk tagged
with week / deck / slide / title / text, plus the images embedded in the
slide (extracted to data/media/ and recompressed to moderate JPEGs so the
repo stays small while keeping the original diagrams as visual evidence).

Slide order is resolved from presentation.xml (sldIdLst -> rels), not zip
order. Chunk schema (v2):

    id (w<N>-s<M>, backward compatible), kind="slide", doc (display name),
    doc_id, loc, week, slide, title, text, section=None, images=[...]

Usage:
    python3 scripts/ingest_slides.py [SLIDES_DIR] [-o OUT]
    (defaults: slides dir = ~/CU Boulder/LLM, out = data/slides_chunks.json)
"""

import argparse
import json
import os
import re
import subprocess
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

MAX_IMG_BYTES = 7 * 1024 * 1024   # skip giant images (>7MB) for evidence
COPY_MAX_BYTES = 350 * 1024       # smaller images are kept as-is
SIPS_MAX_PX = 1000
SIPS_QUALITY = 78

ROOT = Path(__file__).resolve().parent.parent
MEDIA_DIR = ROOT / "data" / "media"


def parse_week(name):
    m = re.search(r"Week\s+(\d+)", name)
    return int(m.group(1)) if m else None


def extract_paragraphs(sp_el):
    return ["".join(t.text or "" for t in p.iter(A_T)).strip()
            for p in sp_el.iter(A_P)]


def shape_texts(slide_el):
    return [para for sp in slide_el.iter(P_SP) for para in extract_paragraphs(sp)]


def slide_title(texts):
    for t in texts:
        if t and len(t) <= 120 and not re.search(r"@|http|www\.", t):
            return t
    return texts[0][:120] if texts else ""


def slide_order(zf):
    pres = ET.fromstring(zf.read("ppt/presentation.xml"))
    ids = [el.attrib.get(f"{{{R_NS}}}id") for el in pres.iter(f"{{{P_NS}}}sldId")]
    rels = ET.fromstring(zf.read("ppt/_rels/presentation.xml.rels"))
    rid_to_target = {rel.attrib["Id"]: rel.attrib["Target"]
                     for rel in rels if rel.attrib.get("Type", "").endswith("/slide")}
    order = []
    for rid in ids:
        target = rid_to_target.get(rid)
        if not target:
            continue
        for name in (os.path.basename(target), target.lstrip("/")):
            if name in zf.namelist():
                order.append(name)
                break
    return order


def slide_media(zf, slide_name):
    """[(media_zipname, rid)] for the images embedded in one slide."""
    rels_name = f"ppt/slides/_rels/{os.path.basename(slide_name)}.rels"
    if rels_name not in zf.namelist():
        return []
    rels = ET.fromstring(zf.read(rels_name))
    out = []
    for rel in rels:
        tgt = rel.attrib.get("Target", "")
        if rel.attrib.get("Type", "").endswith("/image"):
            name = os.path.normpath(os.path.join("ppt/slides", tgt))
            if name in zf.namelist():
                out.append((name, rel.attrib["Id"]))
    return out


def display_docname(filename):
    name = re.sub(r"^MBAX 6418\s*-\s*", "", filename)
    name = re.sub(r"\s+v\d+$", "", name)
    return name.strip()


def recompress(src_bytes, src_ext, dest_path):
    """Write a recompressed JPEG (sips) to dest_path. sips is a macOS built-in."""
    tmp = dest_path.with_name(dest_path.stem + "_src" + src_ext.lower())
    tmp.write_bytes(src_bytes)
    r = subprocess.run(
        ["sips", "-Z", str(SIPS_MAX_PX), "-s", "format", "jpeg",
         "-s", "formatOptions", str(SIPS_QUALITY), str(tmp), "--out", str(dest_path)],
        capture_output=True)
    tmp.unlink(missing_ok=True)
    return r.returncode == 0 and dest_path.exists()


def store_media(zf, zipname, dest_rel):
    dest = MEDIA_DIR / dest_rel
    if dest.exists():
        return str(dest.relative_to(ROOT))
    info = zf.getinfo(zipname)
    if info.file_size > MAX_IMG_BYTES:
        return None  # too big to serve as evidence
    ext = Path(zipname).suffix.lower()
    if ext not in (".png", ".jpg", ".jpeg", ".gif"):
        return None
    dest.parent.mkdir(parents=True, exist_ok=True)
    data = zf.read(zipname)
    if info.file_size <= COPY_MAX_BYTES and ext in (".jpg", ".jpeg", ".png"):
        dest.write_bytes(data)
    else:
        jpg_dest = dest.with_suffix(".jpg")
        if not recompress(data, ext, jpg_dest):
            return None
        dest = jpg_dest
    return str(dest.relative_to(ROOT))


def ingest_deck(path):
    week = parse_week(os.path.basename(path))
    doc_id = f"week{week:02d}" if week else "deck"
    doc = display_docname(os.path.basename(path))
    chunks = []
    with zipfile.ZipFile(path) as zf:
        order = slide_order(zf)
        if not order:
            order = sorted(n for n in zf.namelist()
                           if re.match(r"ppt/slides/slide\d+\.xml$", n))
        for n in order:
            m = re.search(r"slide(\d+)\.xml$", n)
            slide_num = int(m.group(1)) if m else 0
            root = ET.fromstring(zf.read(n))
            texts = shape_texts(root)
            text = "\n".join(t for t in texts if t)
            images = []
            for zipname, _rid in slide_media(zf, n):
                dest_rel = f"{doc_id}/s{slide_num:02d}/{Path(zipname).name}"
                rel = store_media(zf, zipname, dest_rel)
                if rel:
                    images.append(rel)
            chunks.append({
                "id": f"w{week}-s{slide_num}" if week else f"{doc_id}-s{slide_num}",
                "kind": "slide",
                "doc": doc,
                "doc_id": doc_id,
                "loc": f"Week {week}, slide {slide_num}" if week else doc,
                "week": week,
                "slide": slide_num,
                "section": None,
                "title": slide_title(texts),
                "text": text,
                "images": sorted(set(images)),
            })
    return chunks, doc, week


def run(slides_dir, out_path):
    decks = sorted(Path(slides_dir).glob("MBAX 6418 - Week *.pptx"))
    if not decks:
        print(f"no 'MBAX 6418 - Week *.pptx' decks in {slides_dir}",
              file=sys.stderr)
        return []

    all_chunks, media_count = [], 0
    for d in decks:
        chunks, doc, week = ingest_deck(d)
        n = len([c for c in chunks if c["text"].strip()])
        imgs = sum(len(c["images"]) for c in chunks)
        media_count += imgs
        print(f"{d.name}: {len(chunks)} slides, {n} with text, {imgs} images")
        all_chunks.extend(chunks)

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(all_chunks, indent=1, ensure_ascii=False))
    media_bytes = sum(f.stat().st_size for f in MEDIA_DIR.rglob("*") if f.is_file())
    print(f"wrote {out} — {len(all_chunks)} chunks, {media_count} media files, "
          f"{media_bytes/1e6:.1f}MB in {MEDIA_DIR.relative_to(ROOT)}")
    return all_chunks


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("slides_dir", nargs="?",
                    default=str(Path.home() / "CU Boulder" / "LLM"))
    ap.add_argument("-o", "--out", default="data/slides_chunks.json")
    args = ap.parse_args()
    run(args.slides_dir, args.out)


if __name__ == "__main__":
    main()
