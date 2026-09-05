"""
Read the annotation the sheets carry *on the map itself*.

AutoCAD exported the labels as glyph outlines on named layers, so each drawing
path is one character. Clustering the glyphs back into words gives an exact
position for every label; OCR is then only needed to read the few characters
inside each small box.

Roles of interest (see geom.LAYER_ROLES):
    node_label     manhole / node identifiers  -> joins the schedule to the map
    invert_label   MSL invert annotations
    size_label     conduit dimension annotations
"""
from __future__ import annotations

from dataclasses import dataclass

import fitz

from .geom import canonical_layer
from .ocr import ocr_pdf_clip


@dataclass
class Label:
    """A clustered run of glyphs, in PDF-point space (y from page top)."""
    x0: float
    y0: float
    x1: float
    y1: float
    n_glyphs: int = 0
    text: str = ""
    conf: float = 0.0
    role: str = ""
    layer: str = ""

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2

    @property
    def w(self) -> float:
        return self.x1 - self.x0

    @property
    def h(self) -> float:
        return self.y1 - self.y0


def glyph_boxes(page: fitz.Page, role: str) -> list[tuple[float, float, float, float]]:
    """Bounding boxes of every drawing path on layers matching `role`."""
    out = []
    for dr in page.get_drawings():
        if canonical_layer(dr.get("layer")) != role:
            continue
        r = dr.get("rect")
        if r is None or r.width <= 0 or r.height <= 0:
            continue
        out.append((r.x0, r.y0, r.x1, r.y1))
    return out


def cluster_glyphs(boxes, gap_factor: float = 1.15, max_size: float = 60.0) -> list[Label]:
    """Merge glyph boxes that nearly touch into word-level labels."""
    boxes = [b for b in boxes if (b[2] - b[0]) < max_size and (b[3] - b[1]) < max_size]
    if not boxes:
        return []
    n = len(boxes)
    # Text may run horizontally or vertically depending on how the sheet was
    # plotted, so key the gap off the glyphs' smaller dimension and pad evenly.
    sizes = sorted(min(b[2] - b[0], b[3] - b[1]) for b in boxes)
    med = sizes[len(sizes) // 2] or 1.0
    pad = med * gap_factor

    cell = max(pad * 2.0, 1.0)
    grid: dict[tuple[int, int], list[int]] = {}
    for i, (x0, y0, x1, y1) in enumerate(boxes):
        for gx in range(int((x0 - pad) // cell), int((x1 + pad) // cell) + 1):
            for gy in range(int((y0 - pad) // cell), int((y1 + pad) // cell) + 1):
                grid.setdefault((gx, gy), []).append(i)

    parent = list(range(n))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for idxs in grid.values():
        for ii in range(len(idxs)):
            ax0, ay0, ax1, ay1 = boxes[idxs[ii]]
            for jj in range(ii + 1, len(idxs)):
                bx0, by0, bx1, by1 = boxes[idxs[jj]]
                if ax0 - pad <= bx1 and bx0 <= ax1 + pad and \
                   ay0 - pad <= by1 and by0 <= ay1 + pad:
                    union(idxs[ii], idxs[jj])

    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)

    labels = []
    for members in groups.values():
        labels.append(Label(
            min(boxes[i][0] for i in members), min(boxes[i][1] for i in members),
            max(boxes[i][2] for i in members), max(boxes[i][3] for i in members),
            n_glyphs=len(members)))
    return labels


def _ocr_label(page, lb: Label, rot: int, dpi: int, pad: float, min_conf: float,
               custom_words) -> tuple[str, float]:
    clip = fitz.Rect(lb.x0 - pad, lb.y0 - pad, lb.x1 + pad, lb.y1 + pad) & page.rect
    if clip.width < 0.5 or clip.height < 0.5:
        return "", 0.0
    try:
        items = ocr_pdf_clip(page, clip, dpi=dpi, rotate=rot,
                             min_conf=min_conf, custom_words=custom_words)
    except Exception:
        return "", 0.0
    # Neighbouring annotation often intrudes into the padded crop, so keep only
    # what actually sits inside the glyph cluster's own box.
    tol = 0.9
    inside = [i for i in items
              if lb.x0 - tol <= i.cx <= lb.x1 + tol and lb.y0 - tol <= i.cy <= lb.y1 + tol]
    items = inside or items
    if not items:
        return "", 0.0
    if lb.h > lb.w * 1.6:          # text stacked vertically
        items.sort(key=lambda t: (t.y0, t.x0))
    else:
        items.sort(key=lambda t: (round(t.cy, 1), t.x0))
    txt = "".join(i.text for i in items).strip()
    conf = sum(i.conf for i in items) / len(items)
    return txt, conf


def best_rotation(page: fitz.Page, labels: list[Label], *, dpi: int, pad: float,
                  rotations=(0, 90, 270), sample: int = 12,
                  custom_words=()) -> int:
    """Probe a few labels to find the sheet's text orientation once."""
    if len(rotations) == 1:
        return rotations[0]
    probe = sorted(labels, key=lambda l: -l.n_glyphs)[:sample]
    scores = {}
    for rot in rotations:
        s = 0.0
        for lb in probe:
            txt, conf = _ocr_label(page, lb, rot, dpi, pad, 0.0, custom_words)
            s += conf + 0.08 * len(txt)
        scores[rot] = s
    return max(scores, key=scores.get)


def read_labels(page: fitz.Page, labels: list[Label], *, dpi: int = 1100,
                pad: float = 2.0, rotations=(0, 90, 270), min_conf: float = 0.0,
                custom_words=()) -> list[Label]:
    """OCR each label box using the sheet's dominant text orientation."""
    if not labels:
        return labels
    rot = best_rotation(page, labels, dpi=dpi, pad=pad, rotations=rotations,
                        custom_words=custom_words)
    for lb in labels:
        txt, conf = _ocr_label(page, lb, rot, dpi, pad, min_conf, custom_words)
        lb.text, lb.conf = txt, conf
    return labels


def read_role(pdf_path: str, role: str, *, dpi: int = 1100, pad: float = 2.5,
              rotations=(0, 90, 270), min_glyphs: int = 1,
              gap_factor: float = 1.15, custom_words=()) -> list[Label]:
    """Cluster and OCR one annotation role. Positions are PDF points."""
    doc = fitz.open(pdf_path)
    try:
        page = doc[0]
        if page.rotation:
            page.set_rotation(0)
        labels = [lb for lb in cluster_glyphs(glyph_boxes(page, role), gap_factor=gap_factor)
                  if lb.n_glyphs >= min_glyphs]
        for lb in labels:
            lb.role = role
        read_labels(page, labels, dpi=dpi, pad=pad, rotations=rotations,
                    custom_words=custom_words)
        return labels
    finally:
        doc.close()
