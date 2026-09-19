#!/usr/bin/env python3
"""Convert a camp .docx into markdown plus right-sized images for the app.

Written for "Build (2026).docx", the camp's build guide: 241 paragraphs of
instructions around 15 figures, of which the camp layout plan is the one
somebody standing in the dust actually needs. The figures are 28 MB as Word
saved them, which is more than the whole app -- so they are downscaled to
something a phone screen can use and no larger.

Headings come from Word's style names, not from guessing at font sizes, and
list paragraphs become bullets. Images are emitted in document order, at the
paragraph where they appear, so a figure stays attached to the step it
illustrates.

Usage:
    python docx_to_markdown.py <in.docx> <out.md> <image-dir> [--prefix build]
"""
import argparse
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

# defusedxml when it is here, stdlib when it is not. A .docx is a file
# somebody chose to put in the repo, not something fetched -- but a document
# is still the classic carrier for an entity-expansion bomb, and the whole
# guard is one import.
try:  # pragma: no cover - depends on the environment
    from defusedxml import ElementTree as ET
except ImportError:  # pragma: no cover
    from xml.etree import ElementTree as ET

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"

# Wide enough for a phone at 3x without being a photograph library.
MAX_WIDTH = 1400
JPEG_QUALITY = 72


def style_of(p):
    pr = p.find(f"{W}pPr")
    if pr is None:
        return ""
    st = pr.find(f"{W}pStyle")
    return (st.get(f"{W}val") or "") if st is not None else ""


def is_list(p):
    pr = p.find(f"{W}pPr")
    return pr is not None and pr.find(f"{W}numPr") is not None


def text_of(p):
    out = []
    for node in p.iter():
        if node.tag == f"{W}t":
            out.append(node.text or "")
        elif node.tag == f"{W}tab":
            out.append(" ")
        elif node.tag == f"{W}br":
            out.append("\n")
    return "".join(out).strip()


def images_in(p):
    return [b.get(f"{R}embed")
            for b in p.iter(f"{A}blip") if b.get(f"{R}embed")]


def heading_level(style):
    m = re.match(r"Heading(\d)", style, re.IGNORECASE)
    if m:
        return min(int(m.group(1)), 3)
    if style.lower() in ("title",):
        return 1
    return 0


# A downscaled diagram lands well under this; a downscaled photograph does
# not. The camp plan is 280 KB of line art at 1400px, the shade-bracket photos
# were 2-3.6 MB each -- Word had saved every phone photo as PNG.
_PHOTO_IF_OVER = 400_000


def shrink(src: Path, dst_dir: Path, stem: str) -> str:
    """Downscale, and re-encode photographs as JPEG. Returns the filename.

    Format is chosen by what the image turns out to BE, not by what Word
    happened to save it as. A diagram stays PNG: the camp plan's thin lines
    and small labels are exactly what somebody squints at, and JPEG makes
    them fuzzy. A photograph becomes JPEG, because 3.6 MB of lossless
    shade-bracket is larger than the entire app.
    """
    work = dst_dir / f"{stem}{src.suffix.lower()}"
    shutil.copy(src, work)
    subprocess.run(["sips", "--resampleWidth", str(MAX_WIDTH), str(work)],
                   capture_output=True)

    # Already a JPEG: squeeze it in place and stop. Going down the convert
    # branch would give `work` and `jpg` the same path, and the "keep the
    # smaller one" test then compares a file with itself, fails, and unlinks
    # it -- which silently dropped four of the fifteen figures.
    if work.suffix.lower() in (".jpg", ".jpeg"):
        subprocess.run(["sips", "-s", "formatOptions", str(JPEG_QUALITY),
                        str(work)], capture_output=True)
        return work.name

    if work.stat().st_size <= _PHOTO_IF_OVER:
        return work.name  # a diagram, and small enough to stay lossless

    jpg = dst_dir / f"{stem}.jpg"
    subprocess.run(["sips", "-s", "format", "jpeg",
                    "-s", "formatOptions", str(JPEG_QUALITY),
                    str(work), "--out", str(jpg)], capture_output=True)
    if jpg.exists() and jpg.stat().st_size < work.stat().st_size:
        work.unlink(missing_ok=True)
        return jpg.name
    jpg.unlink(missing_ok=True)
    return work.name


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("docx")
    ap.add_argument("out_md")
    ap.add_argument("image_dir")
    ap.add_argument("--prefix", default="build")
    args = ap.parse_args()

    src = Path(args.docx)
    out_md = Path(args.out_md)
    img_dir = Path(args.image_dir)
    img_dir.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(src) as z:
        doc = ET.fromstring(z.read("word/document.xml"))
        rels = ET.fromstring(z.read("word/_rels/document.xml.rels"))
        rel_target = {
            r.get("Id"): r.get("Target")
            for r in rels
            if r.get("Type", "").endswith("/image")
        }
        media = {name: z.read(name) for name in z.namelist()
                 if name.startswith("word/media/")}

    body = doc.find(f"{W}body")
    lines: list[str] = []
    seen: dict[str, str] = {}
    n = 0
    tmp = img_dir / "_tmp_extract"
    tmp.mkdir(exist_ok=True)

    for p in body.iter(f"{W}p"):
        for rid in images_in(p):
            target = rel_target.get(rid)
            if not target:
                continue
            if rid in seen:
                lines += ["", f"![figure]({seen[rid]})", ""]
                continue
            n += 1
            raw = media.get(f"word/{target}")
            if raw is None:
                continue
            staging = tmp / f"src{n}{Path(target).suffix.lower()}"
            staging.write_bytes(raw)
            name = shrink(staging, img_dir, f"{args.prefix}-fig{n}")
            seen[rid] = name
            lines += ["", f"![figure]({name})", ""]

        t = text_of(p)
        if not t:
            continue
        level = heading_level(style_of(p))
        if level:
            lines += ["", "#" * level + " " + t, ""]
        elif is_list(p):
            lines.append(f"- {t}")
        else:
            lines += ["", t, ""]

    shutil.rmtree(tmp, ignore_errors=True)

    text = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip() + "\n"
    out_md.write_text(text, encoding="utf-8")

    total = sum(f.stat().st_size for f in img_dir.glob(f"{args.prefix}-fig*"))
    print(f"{out_md}  {len(text):,} chars")
    print(f"{n} figures -> {img_dir}  {total / 1_048_576:.1f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
