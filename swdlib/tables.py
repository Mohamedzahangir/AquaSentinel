"""
Read the "LIST OF POINTS" manhole schedules off a GCC SWD ward sheet.

Each sheet carries one or more table blocks with eight columns:

    SI No. | MANHOLE ID | DRAIN Closed(C)/Open(O) |
    MSL LEVEL: Drain Top(m) | Invert(m) | Road Edge(m) |
    DRAIN DIMENSIONS: Width(m) | Depth(m)

The cell grid is real vector line work, so the row and column boundaries come
from the geometry and OCR is only used to fill cells — far more reliable than
segmenting a picture of a table.

Sheets plotted in landscape carry the schedule rotated 90 degrees on the page,
so detection runs in both orientations.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass

import fitz

from .grid import _segments
from .ocr import ocr_pdf_clip

COLUMNS = ["si", "manhole_id", "drain_type", "drain_top", "invert", "road_edge", "width", "depth"]
NUMERIC = {"drain_top", "invert", "road_edge", "width", "depth"}
DASHES = {"-", "--", "---", "—", "–", "‒", "―", "_", "~", "="}


@dataclass
class TableBlock:
    row_edges: list[float]      # boundaries along the row axis
    col_edges: list[float]      # boundaries along the column axis
    orient: str                 # 'h' = rows are horizontal lines; 'v' = rotated
    rotate: int = 0             # render rotation that makes the text upright
    flip_cols: bool = False     # column order runs backwards on the page

    @property
    def n_rows(self) -> int:
        return len(self.row_edges) - 1

    @property
    def n_cols(self) -> int:
        return len(self.col_edges) - 1

    def rect(self, r0: int, r1: int) -> fitz.Rect:
        a, b = self.row_edges[r0] - 0.6, self.row_edges[r1] + 0.6
        c, d = self.col_edges[0] - 1, self.col_edges[-1] + 1
        if self.orient == "h":
            return fitz.Rect(c, a, d, b)
        return fitz.Rect(a, c, b, d)

    def token_pos(self, item):
        """(row_axis_pos, col_axis_pos) for an OCR item."""
        return (item.cy, item.cx) if self.orient == "h" else (item.cx, item.cy)


def _cluster(vals: list[float], tol: float) -> list[float]:
    out: list[float] = []
    for v in sorted(vals):
        if out and abs(v - out[-1]) <= tol:
            continue
        out.append(v)
    return out


def _fill_missing_edges(edges: list[float]) -> list[float]:
    """Insert boundaries the drawing omitted (a gap ~k x the median column)."""
    if len(edges) < 4:
        return edges
    diffs = [edges[i + 1] - edges[i] for i in range(len(edges) - 1)]
    med = sorted(diffs)[len(diffs) // 2]
    if med <= 0:
        return edges
    out = [edges[0]]
    for i, d in enumerate(diffs):
        k = round(d / med)
        if k >= 2 and abs(d - k * med) < med * 0.4:
            for j in range(1, k):
                out.append(edges[i] + d * j / k)
        out.append(edges[i + 1])
    return out


def _longest_run(edges: list[float], pitch: float) -> list[float]:
    runs, cur = [], [edges[0]]
    for i in range(1, len(edges)):
        if abs(edges[i] - edges[i - 1] - pitch) < pitch * 0.4:
            cur.append(edges[i])
        else:
            runs.append(cur)
            cur = [edges[i]]
    runs.append(cur)
    return max(runs, key=len)


def _blocks_for_orientation(rows_store, cols_store, orient: str, min_rows: int):
    """rows_store: segments whose position is the row axis. cols_store: the other."""
    items = [(p, l, lo, hi) for p, (l, lo, hi) in rows_store.items() if l > 60]
    if not items:
        return []

    groups: dict[tuple[int, int], list[tuple[float, float, float]]] = defaultdict(list)
    for p, l, lo, hi in items:
        groups[(int(round(lo / 8.0)), int(round(hi / 8.0)))].append((p, lo, hi))

    regions = []
    for _, recs in groups.items():
        if len(recs) < min_rows:
            continue
        ps = sorted(r[0] for r in recs)
        diffs = [round(ps[i + 1] - ps[i], 2) for i in range(len(ps) - 1)]
        if not diffs:
            continue
        pitch, cnt = Counter(diffs).most_common(1)[0]
        if not (7.0 < pitch < 22.0) or cnt < min_rows - 4:
            continue
        # true extent of the row rules == the table's outer column boundaries
        regions.append({"lo": min(r[1] for r in recs), "hi": max(r[2] for r in recs),
                        "top": ps[0], "bot": ps[-1], "pitch": pitch})
    if not regions:
        return []

    # merge overlapping regions (halves of one schedule)
    regions.sort(key=lambda r: (r["lo"], r["top"]))
    merged = []
    for r in regions:
        for m in merged:
            if not (r["hi"] < m["lo"] - 6 or r["lo"] > m["hi"] + 6) and \
               not (r["bot"] < m["top"] - 6 or r["top"] > m["bot"] + 6):
                m["lo"] = min(m["lo"], r["lo"]); m["hi"] = max(m["hi"], r["hi"])
                m["top"] = min(m["top"], r["top"]); m["bot"] = max(m["bot"], r["bot"])
                break
        else:
            merged.append(dict(r))

    out: list[TableBlock] = []
    for reg in merged:
        lo, hi, top, bot = reg["lo"], reg["hi"], reg["top"], reg["bot"]
        span = bot - top
        if span <= 0:
            continue
        cand = [
            p for p, (l, clo, chi) in cols_store.items()
            if lo - 6 <= p <= hi + 6 and l > span * 0.25
            and clo <= top + span * 0.6 and chi >= top + span * 0.4
        ]
        # The outermost boundaries are often only implied by how far the row
        # rules run, so seed them from the region extent.
        cand += [lo, hi]
        col_edges = _fill_missing_edges(_cluster(cand, 4.0))
        if len(col_edges) < 9:
            continue
        for k in range((len(col_edges) - 1) // 8):
            sub = col_edges[k * 8: k * 8 + 9]
            if len(sub) != 9:
                continue
            c0, c1 = sub[0], sub[-1]
            ps = sorted(
                p for p, (l, plo, phi) in rows_store.items()
                if plo <= c0 + 8 and phi >= c1 - 8 and top - 6 <= p <= bot + 600
            )
            if len(ps) < min_rows:
                continue
            pitch = Counter(round(ps[i + 1] - ps[i], 2)
                            for i in range(len(ps) - 1)).most_common(1)[0][0]
            if not (7.0 < pitch < 22.0):
                continue
            edges = _longest_run(_cluster(ps, pitch * 0.4), pitch)
            if len(edges) < min_rows:
                continue
            out.append(TableBlock(row_edges=edges, col_edges=sub, orient=orient))
    return out


def find_table_blocks(page: fitz.Page, min_rows: int = 12) -> list[TableBlock]:
    vs, hs = _segments(page, min_len=20.0)
    blocks = _blocks_for_orientation(hs, vs, "h", min_rows)
    if not blocks:
        blocks = _blocks_for_orientation(vs, hs, "v", min_rows)
    return blocks


_num_re = re.compile(r"^-?\d{1,3}(?:\.\d{1,3})?$")


def _clean(text: str, col: str):
    t = text.strip()
    if not t:
        return None
    if t in DASHES or (set(t) <= set("-—–_= ") and t.strip()):
        return None
    if col == "drain_type":
        u = t.upper().strip("() ")
        return u[0] if u and u[0] in "CO" else None
    if col == "si":
        d = re.sub(r"\D", "", t)
        return int(d) if d else None
    if col == "manhole_id":
        u = re.sub(r"\s+", " ", t.upper().replace("（", "(").replace("）", ")")).strip()
        return u or None
    u = t.replace(",", ".").replace(" ", "")
    u = u.replace("O", "0").replace("o", "0").replace("l", "1").replace("I", "1")
    u = u.replace("S", "5").replace("B", "8")
    if not _num_re.match(u):
        digits = re.sub(r"\D", "", u)
        if 4 <= len(digits) <= 5 and "." not in u:
            u = f"{digits[:-3]}.{digits[-3:]}"
        else:
            return None
    try:
        return float(u)
    except ValueError:
        return None


def _score_rows(rows: list[dict]) -> int:
    """How table-like a parse is: count of usable numeric cells + valid ids."""
    s = 0
    for r in rows:
        if r.get("manhole_id"):
            s += 1
        s += sum(1 for c in NUMERIC if r.get(c) is not None)
    return s


def _read_pass(page, block: TableBlock, rotate: int, flip: bool,
               dpi: int, rows_per_pass: int, min_conf: float) -> list[dict]:
    ce = block.col_edges
    mids = [(ce[i] + ce[i + 1]) / 2 for i in range(8)]
    rows: list[dict] = []
    r = 0
    while r < block.n_rows:
        r_end = min(r + rows_per_pass, block.n_rows)
        clip = block.rect(r, r_end)
        try:
            items = ocr_pdf_clip(page, clip, dpi=dpi, rotate=rotate, min_conf=min_conf)
        except Exception:
            items = []
        grid: dict[tuple[int, int], list] = defaultdict(list)
        for it in items:
            rp, cp = block.token_pos(it)
            ri = None
            for k in range(r, r_end):
                if block.row_edges[k] - 1.2 <= rp <= block.row_edges[k + 1] + 1.2:
                    ri = k
                    break
            if ri is None:
                continue
            ci = min(range(8), key=lambda c: abs(mids[c] - cp))
            if not (ce[ci] - 2.5 <= cp <= ce[ci + 1] + 2.5):
                continue
            grid[(ri, ci)].append(it)

        for k in range(r, r_end):
            rec = {"_row": k}
            any_val = False
            for ci, name in enumerate(COLUMNS):
                src = (7 - ci) if flip else ci
                toks = sorted(grid.get((k, src), []), key=lambda t: (t.x0 if block.orient == "h" else t.y0))
                raw = " ".join(t.text for t in toks).strip()
                rec[f"{name}_raw"] = raw
                v = _clean(raw, name) if raw else None
                rec[name] = v
                if v is not None:
                    any_val = True
            if any_val:
                rows.append(rec)
        r = r_end
    return rows


def _fill_single_char_column(page, block: TableBlock, rows: list[dict],
                             col_index: int, field: str, dpi: int, min_conf: float):
    """Re-read a column of lone characters.

    Vision often skips an isolated 'C' sitting alone in a wide cell, but reads
    the same letters reliably when the whole column is given as one tall strip,
    where they form an obvious vertical list.
    """
    src = (7 - col_index) if block.flip_cols else col_index
    ce = block.col_edges
    lo, hi = ce[src], ce[src + 1]
    # Give the recogniser a column of real text either side to anchor on.
    wlo, whi = ce[max(0, src - 1)], ce[min(8, src + 2)]
    by_row = {r["_row"]: r for r in rows}
    step = 10
    r = 0
    while r < block.n_rows:
        r_end = min(r + step, block.n_rows)
        a, b = block.row_edges[r] - 0.5, block.row_edges[r_end] + 0.5
        clip = fitz.Rect(wlo, a, whi, b) if block.orient == "h" else fitz.Rect(a, wlo, b, whi)
        try:
            items = ocr_pdf_clip(page, clip, dpi=dpi, rotate=block.rotate,
                                 min_conf=min_conf, custom_words=("C", "O"))
        except Exception:
            items = []
        for it in items:
            rp, cp = block.token_pos(it)
            if not (lo - 2 <= cp <= hi + 2):
                continue
            for k in range(r, r_end):
                if block.row_edges[k] - 1.2 <= rp <= block.row_edges[k + 1] + 1.2:
                    rec = by_row.get(k)
                    if rec is not None and rec.get(field) is None:
                        v = _clean(it.text, field)
                        if v is not None:
                            rec[field] = v
                            rec[f"{field}_raw"] = it.text
                    break
        r = r_end


def read_block(page: fitz.Page, block: TableBlock, *, dpi: int = 700,
               rows_per_pass: int = 8, min_conf: float = 0.10) -> list[dict]:
    """OCR a block, choosing the render rotation and column order that fit best."""
    if block.n_cols != 8:
        return []
    # Probe a few rows to settle rotation / column order, then commit.
    probe = TableBlock(row_edges=block.row_edges[:min(9, len(block.row_edges))],
                       col_edges=block.col_edges, orient=block.orient)
    cands = [(0, False), (0, True)] if block.orient == "h" else \
            [(90, False), (90, True), (270, False), (270, True)]
    best, best_score = (0, False), -1
    for rot, flip in cands:
        try:
            rows = _read_pass(page, probe, rot, flip, dpi, rows_per_pass, min_conf)
        except Exception:
            continue
        sc = _score_rows(rows)
        # a real schedule has ascending SI numbers
        sis = [r["si"] for r in rows if r.get("si") is not None]
        if len(sis) >= 3 and all(sis[i] <= sis[i + 1] for i in range(len(sis) - 1)):
            sc += 20
        if sc > best_score:
            best, best_score = (rot, flip), sc
    block.rotate, block.flip_cols = best
    rows = _read_pass(page, block, block.rotate, block.flip_cols, dpi, rows_per_pass, min_conf)
    if any(r.get("drain_type") is None for r in rows):
        _fill_single_char_column(page, block, rows, 2, "drain_type", dpi, 0.0)
    return rows


def read_sheet_tables(pdf_path: str, *, dpi: int = 700) -> list[dict]:
    doc = fitz.open(pdf_path)
    try:
        page = doc[0]
        if page.rotation:
            page.set_rotation(0)
        out: list[dict] = []
        for bi, b in enumerate(find_table_blocks(page)):
            for rec in read_block(page, b, dpi=dpi):
                rec["_block"] = bi
                out.append(rec)
        return out
    finally:
        doc.close()
