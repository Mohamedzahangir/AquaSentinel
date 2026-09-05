#!/usr/bin/env python3
"""Render the drain-type column of the LIST OF POINTS table from a sample of
sheets so the 'no open channels' reading can be checked by eye."""
import glob, os, sys
import fitz
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# swdlib.ocr binds the macOS Vision framework at import; table *finding* is
# pure vector geometry and needs none of it, so stub the Apple modules out.
import types
for m in ("Quartz", "Vision", "Foundation"):
    sys.modules[m] = types.ModuleType(m)
sys.modules["Foundation"].NSData = object
from swdlib.tables import find_table_blocks

OUT = sys.argv[1]
os.makedirs(OUT, exist_ok=True)
pdfs = sorted(glob.glob("GeoPDF/*.pdf"))
step = max(1, len(pdfs) // 6)
for pdf in pdfs[::step][:6]:
    doc = fitz.open(pdf); page = doc[0]
    if page.rotation: page.set_rotation(0)
    blocks = find_table_blocks(page)
    if not blocks:
        print(pdf, "no table"); continue
    b = blocks[0]
    n = min(b.n_rows, 28)
    clip = b.rect(0, n)
    pix = page.get_pixmap(dpi=110, clip=clip)
    name = os.path.splitext(os.path.basename(pdf))[0]
    pix.save(f"{OUT}/{name}.png")
    print(pdf, "orient", b.orient, "rows", b.n_rows, "clip", clip, "->", pix.width, pix.height)
