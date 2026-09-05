#!/usr/bin/env python3
"""
Read the map's own text annotation into a point layer.

The sheets carry street names, locality names and drain-size callouts as glyph
outlines. Clustering them gives an exact position; OCR gives the text. These are
the local names the survey used, which often differ from — or are missing in —
commercial basemaps.
"""
import json
import os
import re
import sys
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

ROOT = "/Users/admin/Desktop/projects/swd"
PDFS_DIR = f"{ROOT}/GeoPDF"
WARDS_DIR = f"{ROOT}/derived/wards"
AFFINES = f"{ROOT}/derived/affines.json"

# Street/place names run several words, so the glyph gap has to be wide
# enough to bridge word spaces; the short dimension callouts do not.
ROLES = {"road_name": 3.2, "location_names": 3.2, "size_label": 1.6}
MIN_GLYPHS = 2


def work(args):
    fn, rec = args
    from swdlib.grid import Affine
    from swdlib.maplabels import read_role
    ward = rec.get("ward")
    out_dir = os.path.join(WARDS_DIR, f"ward_{ward:03d}")
    if not os.path.isdir(out_dir):
        return fn, ward, 0, 0.0
    t0 = time.time()
    aff = Affine(rec["ae"], rec["be"], rec["an"], rec["bn"], rec["swap"])
    path = os.path.join(PDFS_DIR, fn)
    out = []
    for role, gap in ROLES.items():
        try:
            labels = read_role(path, role, dpi=900, min_glyphs=MIN_GLYPHS, gap_factor=gap)
        except Exception:
            continue
        for lb in labels:
            txt = re.sub(r"\s+", " ", (lb.text or "")).strip()
            if len(txt) < 2:
                continue
            e, n = aff.to_utm(lb.cx, lb.cy)
            out.append({"ward": ward, "role": role, "text": txt,
                        "east": round(e, 2), "north": round(n, 2),
                        "conf": round(lb.conf, 3),
                        "size_pt": round(max(lb.w, lb.h), 2)})
    with open(os.path.join(out_dir, "map_text.json"), "w") as fh:
        json.dump(out, fh, indent=1)
    return fn, ward, len(out), time.time() - t0


def main():
    affines = json.load(open(AFFINES))
    todo = [(fn, r) for fn, r in sorted(affines.items()) if r.get("ok")]
    only = sys.argv[1:] or None
    if only:
        todo = [(f, r) for f, r in todo if any(o in f for o in only)]
    print(f"{len(todo)} sheets, roles={ROLES}", flush=True)
    total = 0
    done = 0
    with ProcessPoolExecutor(max_workers=6) as ex:
        for fut in as_completed([ex.submit(work, t) for t in todo]):
            fn, ward, n, dt = fut.result()
            total += n
            done += 1
            print(f"[{done:3d}/{len(todo)}] ward {str(ward):>4s} {n:5d} labels {dt:5.0f}s "
                  f"(running {total})", flush=True)
    print(f"DONE  {total} map text labels")


if __name__ == "__main__":
    main()
