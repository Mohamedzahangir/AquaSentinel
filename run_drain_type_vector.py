#!/usr/bin/env python3
"""
Re-read the drain-type column of every LIST OF POINTS table from vector
geometry instead of OCR.

The OCR (macOS Vision) never once returned an 'O' across 9,324 rows, and
Ward-70 row 15 ("B1 (OL)") visibly is one. The glyphs are AutoCAD SHX
outlines, and the two letters are trivially separable as paths: 'C' is an
open polyline (17 segments on these sheets), 'O' a closed loop (20). So for
every row whose type cell OCR left blank, look at the drawing inside the cell
and classify it. Rows OCR read as 'C' are re-checked the same way as a
control — the agreement rate is printed.

Writes the result back into derived/wards/ward_XXX/schedule.json and nodes.json
(`drain_type`, with `drain_type_raw` = 'vector:O' / 'vector:C'), then into
derived/network/nodes.json and conduits.json. Runs on Linux: it needs no OCR.
"""
import glob, json, os, re, sys, types
import fitz
for m in ("Quartz", "Vision", "Foundation"):
    sys.modules[m] = types.ModuleType(m)
sys.modules["Foundation"].NSData = object
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from swdlib.tables import find_table_blocks


def ward_no(pdf):
    m = re.search(r"(\d+)", os.path.basename(pdf))
    return int(m.group(1)) if m else None


def glyph_in(drawings, cell):
    """The single letter drawn inside a type cell, or None."""
    hits = [d for d in drawings if cell.contains(d["rect"])
            and 2.0 < d["rect"].width < 8 and 2.0 < d["rect"].height < 9]
    if len(hits) != 1:
        return None
    d = hits[0]
    pts = []
    for it in d["items"]:
        if it[0] == "l": pts += [it[1], it[2]]
        elif it[0] == "c": pts += [it[1], it[4]]
    if len(pts) < 6:
        return None
    closed = abs(pts[0] - pts[-1]) < 0.05
    return "O" if closed else "C"


def classify_sheet(pdf):
    doc = fitz.open(pdf); page = doc[0]
    if page.rotation: page.set_rotation(0)
    blocks = find_table_blocks(page)
    if not blocks:
        return {}
    # a sheet carries ~200k paths; only letter-sized ones can be a type glyph
    drawings = [d for d in page.get_drawings()
                if 2.0 < d["rect"].width < 8 and 2.0 < d["rect"].height < 9]
    per_row = []     # one letter (or None) per table row, block by block, top to bottom
    for b in blocks:
        ce = b.col_edges
        for r in range(b.n_rows):
            g = None
            for ci in (2, 5):          # column 2, or its mirror when columns are flipped
                if b.orient == "h":
                    cell = fitz.Rect(ce[ci], b.row_edges[r], ce[ci+1], b.row_edges[r+1])
                else:
                    cell = fitz.Rect(b.row_edges[r], ce[ci], b.row_edges[r+1], ce[ci+1])
                g = glyph_in(drawings, cell)
                if g: break
            per_row.append(g)
    doc.close()
    return per_row


def _work(pdf):
    return pdf, classify_sheet(pdf)


def main():
    from multiprocessing import Pool
    agree = disagree = filled_O = filled_C = still_blank = 0
    changed = {}       # (ward, si) -> letter
    pdfs = [p for p in sorted(glob.glob("GeoPDF/*.pdf"))
            if ward_no(p) is not None and os.path.exists(f"derived/wards/ward_{ward_no(p):03d}/schedule.json")]
    with Pool(6) as pool:
        results = pool.imap_unordered(_work, pdfs)
        for pdf, per_row in results:
            w = ward_no(pdf)
            sp = f"derived/wards/ward_{w:03d}/schedule.json"
            if not per_row:
                print(f"ward {w:3d}: no table found"); continue
            rows = json.load(open(sp))
            # Align on `si`: serial numbers are 1-based table row indices, shifted
            # by however many header rows sit inside the block. Pick the shift that
            # best reproduces the OCR's own 'C' readings, then apply it.
            by_si = {r.get("si"): r for r in rows if r.get("si") is not None}
            n_rows = len(per_row)
            # find the offset that best matches OCR 'C's (header rows shift the index)
            best_off, best_hit = 0, -1
            for off in range(-3, 4):
                hit = sum(1 for si, r in by_si.items()
                          if r.get("drain_type") == "C" and 0 <= si - 1 + off < n_rows
                          and per_row[si - 1 + off] == "C")
                if hit > best_hit: best_hit, best_off = hit, off
            for si, r in by_si.items():
                k = si - 1 + best_off
                if not (0 <= k < n_rows):
                    continue
                v = per_row[k]
                if r.get("drain_type") == "C":
                    if v == "C": agree += 1
                    elif v is not None: disagree += 1
                if r.get("drain_type") is None:
                    if v is None: still_blank += 1; continue
                    r["drain_type"] = v; r["drain_type_raw"] = f"vector:{v}"
                    changed[(w, si)] = v
                    if v == "O": filled_O += 1
                    else: filled_C += 1
            json.dump(rows, open(sp, "w"), indent=1)
            # propagate into the per-ward nodes
            np_ = f"derived/wards/ward_{w:03d}/nodes.json"
            if os.path.exists(np_):
                nd = json.load(open(np_))
                for n in nd:
                    v = changed.get((w, n.get("si")))
                    if v and not n.get("drain_type"):
                        n["drain_type"] = v
                json.dump(nd, open(np_, "w"), indent=1)
            print(f"ward {w:3d}: {len(rows)} rows, offset {best_off:+d}, control hits {best_hit}", flush=True)

    # city-wide network
    nodes = json.load(open("derived/network/nodes.json"))
    by_uid = {}
    for n in nodes:
        v = changed.get((n.get("ward"), n.get("si")))
        if v and not n.get("drain_type"):
            n["drain_type"] = v
        by_uid[n["uid"]] = n
    json.dump(nodes, open("derived/network/nodes.json", "w"))
    for cp in ("derived/network/conduits.json", "derived/network/conduits.pre_route.json"):
        if not os.path.exists(cp): continue
        cs = json.load(open(cp)); nfix = 0
        for c in cs:
            us, ds = by_uid.get(c["from_uid"], {}), by_uid.get(c["to_uid"], {})
            t = us.get("drain_type") or ds.get("drain_type")
            if t and c.get("type") != t and (c.get("type") is None or t == "O"):
                c["type"] = t; nfix += 1
        json.dump(cs, open(cp, "w"))
        print(f"{cp}: {nfix} conduit types updated")
    print(f"\ncontrol: OCR 'C' rows re-read by geometry — {agree} agree, {disagree} disagree")
    print(f"blank type cells filled: {filled_O} 'O' (open channel), {filled_C} 'C'; {still_blank} still blank")
    typ = {}
    for n in nodes: typ[n.get("drain_type")] = typ.get(n.get("drain_type"), 0) + 1
    print("network nodes by drain_type:", typ)


if __name__ == "__main__":
    main()
