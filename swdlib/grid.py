"""
Exact georeferencing of a GCC SWD ward sheet.

The sheets carry a printed UTM grid (500 m spacing, zone 44N) with the eastings
and northings labelled along the map frame. Rather than guessing the transform
from ward centroids, we:

  1. find the regular grid lines in the page's vector content,
  2. OCR the "E4xxxxx" / "N14xxxxx" labels around the frame,
  3. attach each label to the grid line it sits on,
  4. least-squares fit  E = a*u + b   and   N = c*v + d

where (u, v) are page coordinates. Because we fit E and N independently against
whichever page axis actually carries them, the page's /Rotate flag is irrelevant
— the orientation falls out of the fit.
"""
from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field

import fitz

from .ocr import ocr_pdf_clip

GRID_PT = 283.46  # 500 m at 1:5000 on a 72 dpi page  (500/1.7637)
GRID_TOL = 2.5
GRID_METRES = 500.0

# Chennai sits in UTM 44N around E 400–440 km, N 1400–1480 km.
E_RE = re.compile(r"^E\s*(4[0-4]\d{4})$")
N_RE = re.compile(r"^N\s*(14[0-9]\d{4})$")
# Fallbacks used only when the leading letter was dropped by the recogniser.
E_RE_LOOSE = re.compile(r"^(4[0-4]\d{4})$")
N_RE_LOOSE = re.compile(r"^(14[0-9]\d{4})$")


@dataclass
class Affine:
    """page (u, v) -> UTM (E, N).  E = ae*u + be ; N = an*v + bn
    with `swap` meaning E is carried by the page's y axis instead of x."""
    ae: float
    be: float
    an: float
    bn: float
    swap: bool = False
    n_e_labels: int = 0
    n_n_labels: int = 0
    rms_e: float = 0.0
    rms_n: float = 0.0

    def to_utm(self, u: float, v: float) -> tuple[float, float]:
        if self.swap:
            return self.ae * v + self.be, self.an * u + self.bn
        return self.ae * u + self.be, self.an * v + self.bn

    @property
    def scale_e(self) -> float:
        return abs(self.ae)

    @property
    def scale_n(self) -> float:
        return abs(self.an)


@dataclass
class GridInfo:
    v_lines: list[float] = field(default_factory=list)   # page x of vertical lines
    h_lines: list[float] = field(default_factory=list)   # page y of horizontal lines
    frame: fitz.Rect | None = None
    page_w: float = 0.0
    page_h: float = 0.0


def _segments(page: fitz.Page, min_len: float):
    """Axis-aligned segments as {x: (len, lo, hi)} for verticals and horizontals.

    Includes the edges of rectangle ('re') primitives — the map frame border is
    drawn as a rectangle on many sheets, and labels sit on it.
    """
    vs: dict[float, tuple[float, float, float]] = {}
    hs: dict[float, tuple[float, float, float]] = {}

    def add(store, key, lo, hi):
        key = round(key, 2)
        ln = hi - lo
        if ln < min_len:
            return
        prev = store.get(key)
        if prev is None or ln > prev[0]:
            store[key] = (ln, lo, hi)
        else:
            store[key] = (prev[0], min(prev[1], lo), max(prev[2], hi))

    for dr in page.get_drawings():
        for item in dr.get("items", ()):
            op = item[0]
            if op == "l":
                p1, p2 = item[1], item[2]
                if abs(p2.x - p1.x) < 1.0:
                    add(vs, p1.x, min(p1.y, p2.y), max(p1.y, p2.y))
                elif abs(p2.y - p1.y) < 1.0:
                    add(hs, p1.y, min(p1.x, p2.x), max(p1.x, p2.x))
            elif op == "re":
                r = item[1]
                add(vs, r.x0, r.y0, r.y1)
                add(vs, r.x1, r.y0, r.y1)
                add(hs, r.y0, r.x0, r.x1)
                add(hs, r.y1, r.x0, r.x1)
    return vs, hs


def detect_grid(page: fitz.Page, min_len: float = 200.0) -> GridInfo:
    """Find the regular 283.46 pt UTM grid and the map frame it sits in."""
    vs, hs = _segments(page, min_len)

    def longest_chain(store) -> list[float]:
        ps = sorted(store)
        best: list[float] = []
        for start in ps:
            chain = [start]
            last = start
            while True:
                target = last + GRID_PT
                near = [(abs(p - target), p) for p in ps if abs(p - target) < GRID_TOL]
                if not near:
                    break
                near.sort()
                chain.append(near[0][1])
                last = near[0][1]
            # prefer longer chains; tie-break on total line length
            if len(chain) > len(best) or (
                len(chain) == len(best)
                and sum(store[p][0] for p in chain) > sum(store[p][0] for p in best)
            ):
                best = chain
        return best

    v = longest_chain(vs)
    h = longest_chain(hs)
    gi = GridInfo(v_lines=v, h_lines=h, page_w=page.rect.width, page_h=page.rect.height)
    if not v or not h:
        return gi

    # The frame is the union of the grid lines' own spans: vertical grid lines
    # run the full height of the map area, horizontals the full width.
    y0 = min(vs[p][1] for p in v)
    y1 = max(vs[p][2] for p in v)
    x0 = min(hs[p][1] for p in h)
    x1 = max(hs[p][2] for p in h)
    gi.frame = fitz.Rect(x0, y0, x1, y1)

    # Grid lines coincident with the frame border may not have been drawn as
    # separate strokes — extend each chain to cover the frame.
    def extend(chain: list[float], lo: float, hi: float) -> list[float]:
        out = list(chain)
        p = chain[0] - GRID_PT
        while p >= lo - GRID_TOL:
            out.insert(0, p)
            p -= GRID_PT
        p = chain[-1] + GRID_PT
        while p <= hi + GRID_TOL:
            out.append(p)
            p += GRID_PT
        return out

    gi.v_lines = extend(v, x0, x1)
    gi.h_lines = extend(h, y0, y1)
    return gi


def _harvest_labels(page: fitz.Page, clip: fitz.Rect, rotate: int, dpi: int):
    """OCR a strip and return [(kind, value, item)] for E/N labels found."""
    out = []
    if clip.width <= 1 or clip.height <= 1:
        return out
    # very long thin strips confuse Vision — cut them into squarish chunks
    chunks = []
    if clip.width > 4 * clip.height and clip.width > 300:
        step = max(200.0, clip.height * 4)
        x = clip.x0
        while x < clip.x1:
            chunks.append(fitz.Rect(x, clip.y0, min(x + step, clip.x1), clip.y1))
            x += step
    elif clip.height > 4 * clip.width and clip.height > 300:
        step = max(200.0, clip.width * 4)
        y = clip.y0
        while y < clip.y1:
            chunks.append(fitz.Rect(clip.x0, y, clip.x1, min(y + step, clip.y1)))
            y += step
    else:
        chunks = [clip]

    for ch in chunks:
        try:
            items = ocr_pdf_clip(page, ch, dpi=dpi, rotate=rotate, min_conf=0.25)
        except Exception:
            continue
        for it in items:
            t = it.text.strip().replace(" ", "").replace(",", "").replace(".", "").upper()
            # the recogniser sometimes returns O for 0 / I,l for 1 inside a number
            if re.fullmatch(r"[EN]?[OIL0-9]{6,8}", t):
                t = t[0] + t[1:].replace("O", "0").replace("I", "1").replace("L", "1") \
                    if t and t[0] in "EN" else t.replace("O", "0").replace("I", "1").replace("L", "1")
            for rx, kind in ((E_RE, "E"), (N_RE, "N"),
                             (E_RE_LOOSE, "E"), (N_RE_LOOSE, "N")):
                m = rx.match(t)
                if m:
                    out.append((kind, float(m.group(1)), it))
                    break
    return out


def read_grid_labels(page: fitz.Page, gi: GridInfo, dpi: int = 600, band: float = 46.0):
    """OCR all four frame margins, in both text orientations, collecting E/N labels."""
    if gi.frame is None:
        return []
    f = gi.frame
    strips = [
        # (clip, rotations to try)
        (fitz.Rect(f.x0 - band, f.y0 - band, f.x1 + band, f.y0 + band), (0, 90, 270)),
        (fitz.Rect(f.x0 - band, f.y1 - band, f.x1 + band, f.y1 + band), (0, 90, 270)),
        (fitz.Rect(f.x0 - band, f.y0 - band, f.x0 + band, f.y1 + band), (0, 90, 270)),
        (fitz.Rect(f.x1 - band, f.y0 - band, f.x1 + band, f.y1 + band), (0, 90, 270)),
    ]
    page_rect = page.rect
    found = []
    for clip, rots in strips:
        clip = clip & page_rect
        for rot in rots:
            found.extend(_harvest_labels(page, clip, rot, dpi))
    return found


def _fit_axis(pairs: list[tuple[float, float]]):
    """Least squares  value = a*pos + b .  Returns (a, b, rms)."""
    n = len(pairs)
    if n < 2:
        return None
    sx = sum(p for p, _ in pairs)
    sy = sum(v for _, v in pairs)
    sxx = sum(p * p for p, _ in pairs)
    sxy = sum(p * v for p, v in pairs)
    den = n * sxx - sx * sx
    if abs(den) < 1e-9:
        return None
    a = (n * sxy - sx * sy) / den
    b = (sy - a * sx) / n
    rms = math.sqrt(sum((a * p + b - v) ** 2 for p, v in pairs) / n)
    return a, b, rms


def _consensus(lines: list[float], obs: list[tuple[float, float]]):
    """Pick the label->line assignment that most observations agree with.

    The grid is exactly GRID_METRES apart every GRID_PT points, so one correct
    (line, value) pair fixes the whole axis. We try each observation as the
    anchor and keep whichever predicts the most of the others. This rejects OCR
    misreads (a single wrong digit puts the value 100s of km away).

    `obs` is [(page_pos, value)]; returns the inlier list.
    """
    if not obs or not lines:
        return []
    best: list[tuple[float, float]] = []
    for sign in (+1.0, -1.0):
        a = sign * GRID_METRES / GRID_PT
        for pos0, val0 in obs:
            b = val0 - a * pos0
            inliers = [(p, v) for p, v in obs if abs(a * p + b - v) <= 60.0]
            # de-duplicate: one value per grid line
            seen: dict[float, float] = {}
            ok = True
            for p, v in inliers:
                L = min(lines, key=lambda L: abs(L - p))
                if L in seen and seen[L] != v:
                    ok = False
                    break
                seen[L] = v
            if ok and len(inliers) > len(best):
                best = inliers
    return best


def _axis_fit(labels, kind: str, use_x: bool, grid_lines: list[float]):
    """Fit one UTM axis from its labels.

    A label is drawn a fixed distance from the grid line it annotates, but that
    offset differs between sheets (6 pt on some, 40+ pt on others). So we first
    fit the labels on their own — which pins the scale — then recover the single
    label-to-line offset that best explains where the real grid lines are, and
    re-reference the intercept to the lines themselves.
    """
    obs = [((it.cx if use_x else it.cy), val) for k, val, it in labels if k == kind]
    if len(obs) < 2:
        return None
    inliers = _consensus_free(obs)
    if len(inliers) < 2:
        return None
    rough = _fit_axis(sorted({p: v for p, v in inliers}.items()))
    if rough is None:
        return None
    a_r, b_r, _ = rough

    if not grid_lines:
        return None

    # Labels are drawn a fixed distance from the line they annotate; find that
    # offset from the labels that agree, then read the value onto the exact
    # vector line position so the final fit keeps the drawing's precision.
    deltas = []
    for p, _ in inliers:
        near = [L - p for L in grid_lines if abs(L - p) <= GRID_PT * 0.5]
        if near:
            deltas.append(min(near, key=abs))
    if not deltas:
        return None
    delta = Counter(round(d / 2.0) for d in deltas).most_common(1)[0][0] * 2.0

    votes: dict[float, Counter] = defaultdict(Counter)
    for p, v in inliers:
        cands = [L for L in grid_lines if abs(L - (p + delta)) <= 14.0]
        if not cands:
            continue
        L = min(cands, key=lambda L: abs(L - (p + delta)))
        votes[L][v] += 1
    # one value per line, by majority; drop lines where the vote is tied
    pairs: dict[float, float] = {}
    for L, c in votes.items():
        top = c.most_common(2)
        if len(top) == 1 or top[0][1] > top[1][1]:
            pairs[L] = top[0][0]
    if len(pairs) < 2:
        return None
    # keep only lines whose values sit on the same 500 m / 283.46 pt ladder
    pairs = dict(_consensus_free(sorted(pairs.items())))
    if len(pairs) < 2:
        return None
    fit = _fit_axis(sorted(pairs.items()))
    if fit is None:
        return None
    a, b, rms = fit
    if a_r * a < 0:  # direction flipped — something is wrong
        return None
    return a, b, rms, len(pairs), delta


def _consensus_free(obs: list[tuple[float, float]]):
    """Largest subset of (position, value) consistent with the 500 m / 283.46 pt grid."""
    if not obs:
        return []
    best: list[tuple[float, float]] = []
    for sign in (+1.0, -1.0):
        a = sign * GRID_METRES / GRID_PT
        for pos0, val0 in obs:
            b = val0 - a * pos0
            inl = [(p, v) for p, v in obs if abs(a * p + b - v) <= 60.0]
            # One position cannot carry two different values. Rather than
            # discarding the whole hypothesis over a single misread digit,
            # keep the majority value at each position.
            byp: dict[int, Counter] = defaultdict(Counter)
            for p, v in inl:
                byp[int(round(p / 8.0))][v] += 1
            keep = set()
            for k, c in byp.items():
                top = c.most_common(2)
                if len(top) == 1 or top[0][1] > top[1][1]:
                    keep.add((k, top[0][0]))
            inl = [(p, v) for p, v in inl if (int(round(p / 8.0)), v) in keep]
            if len(inl) > len(best):
                best = inl
    return best


def solve_affine(page: fitz.Page, gi: GridInfo, labels, snap: float = 16.0) -> Affine | None:
    """Attach OCR'd labels to grid lines and fit the page->UTM transform."""
    if gi.frame is None or not labels:
        return None

    e_on_x = _axis_fit(labels, "E", True, gi.v_lines)
    e_on_y = _axis_fit(labels, "E", False, gi.h_lines)
    n_on_x = _axis_fit(labels, "N", True, gi.v_lines)
    n_on_y = _axis_fit(labels, "N", False, gi.h_lines)

    def score(f):
        return f[3] if f else 0

    # Choose the orientation that explains the most labels overall.
    if score(e_on_x) + score(n_on_y) >= score(e_on_y) + score(n_on_x):
        swap, fe, fn = False, e_on_x, n_on_y
    else:
        swap, fe, fn = True, e_on_y, n_on_x
    if fe is None or fn is None:
        return None
    ae, be, rmse, ne, _ = fe
    an, bn, rmsn, nn, _ = fn

    # sanity: scale must be ~1.7637 m per pt (1:5000) and residuals tiny
    for s in (abs(ae), abs(an)):
        if not (1.70 < s < 1.83):
            return None
    if rmse > 20.0 or rmsn > 20.0:
        return None

    return Affine(ae, be, an, bn, swap, ne, nn, rmse, rmsn)


def georeference(pdf_path: str, dpi: int = 600):
    """Full pipeline for one sheet. Returns (Affine|None, GridInfo, labels)."""
    doc = fitz.open(pdf_path)
    try:
        page = doc[0]
        # Normalise away /Rotate so page geometry, rendering and OGR's vector
        # coordinates all live in the same (mediabox) space. The true map
        # orientation is recovered from the labels instead.
        if page.rotation:
            page.set_rotation(0)
        gi = detect_grid(page)
        if gi.frame is None or len(gi.v_lines) < 2 or len(gi.h_lines) < 2:
            return None, gi, []
        labels = read_grid_labels(page, gi, dpi=dpi)
        aff = solve_affine(page, gi, labels)
        return aff, gi, labels
    finally:
        doc.close()
