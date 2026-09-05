"""
Pull the map geometry off a ward sheet, per CAD layer, straight into UTM.

PyMuPDF reports every drawing path together with the optional-content group
(the original AutoCAD layer) it belongs to, in PDF-point space. That avoids the
coordinate-convention guesswork of going through OGR, and behaves identically
whether or not the sheet carries a /Rotate flag.
"""
from __future__ import annotations

import math
import re
from collections import defaultdict
from dataclasses import dataclass, field

import fitz

from .grid import Affine

# CAD layer name -> canonical role. Names are matched case/space/underscore
# insensitively; the first pattern that matches wins.
LAYER_ROLES: list[tuple[str, str]] = [
    (r"^drain[\s_]*nos?$", "node_label"),
    (r"^drain[\s_]*size$", "size_label"),
    (r"^drain[\s_]*flow$", "flow_arrow"),
    (r"^msl[\s_]*bottom$", "invert_label"),
    (r"^invert[\s_]*level$", "invert_label"),
    (r"^outlet[\s_]*hatch$", "outfall"),
    (r"^open[\s_]*drain$", "drain_open"),
    (r"^drain[\s_]*pipe$", "drain_pipe"),
    (r"^(drain|.*[\s_]drain)$", "drain"),
    (r"^(canal|canal[\s_]*drain|channel)$", "canal"),
    (r"^river$", "river"),
    (r"^sea$", "sea"),
    (r"^pond$", "pond"),
    (r"^inundated[\s_]*area$", "inundated"),
    (r"^pumping[\s_]*station$", "pump"),
    (r"^(carriage[\s_-]*way|asphalted[\s_]*road)$", "road"),
    (r"^miss.*road.*name.*$", "road_name"),
    (r"^miss.*road.*$", "road_missing"),
    (r"^road[\s_]*names?$", "road_name"),
    (r"^divider?l?$", "divider"),
    (r"^ward[\s_]*boundary$", "ward_boundary"),
    (r"^zone[\s_]*boundary$", "zone_boundary"),
    (r"^bridge$", "bridge"),
    (r"^fly[\s_]*over$", "flyover"),
    (r"^(railway|metro[\s_]*railway)$", "railway"),
    (r"^(grid|lat[\s_]*long)$", "graticule"),
    (r"^manhole(_1)?$", "manhole_symbol"),
]


def canonical_layer(name: str | None) -> str:
    if not name:
        return "unassigned"
    key = name.strip().lower()
    for pat, role in LAYER_ROLES:
        if re.match(pat, key):
            return role
    return re.sub(r"[^a-z0-9]+", "_", key).strip("_") or "unassigned"


@dataclass
class Path:
    """One drawing path, as polylines in UTM metres."""
    layer: str
    role: str
    lines: list[list[tuple[float, float]]] = field(default_factory=list)
    closed: bool = False
    stroke: tuple | None = None
    fill: tuple | None = None
    width: float = 0.0

    @property
    def length(self) -> float:
        tot = 0.0
        for ln in self.lines:
            for i in range(len(ln) - 1):
                tot += math.dist(ln[i], ln[i + 1])
        return tot


def _bezier(p0, p1, p2, p3, n: int = 8):
    pts = []
    for i in range(1, n + 1):
        t = i / n
        mt = 1 - t
        x = (mt ** 3 * p0.x + 3 * mt * mt * t * p1.x + 3 * mt * t * t * p2.x + t ** 3 * p3.x)
        y = (mt ** 3 * p0.y + 3 * mt * mt * t * p1.y + 3 * mt * t * t * p2.y + t ** 3 * p3.y)
        pts.append((x, y))
    return pts


def path_polylines(drawing) -> list[list[tuple[float, float]]]:
    """Flatten one PyMuPDF drawing into polylines in PDF points."""
    out: list[list[tuple[float, float]]] = []
    cur: list[tuple[float, float]] = []

    def flush():
        nonlocal cur
        if len(cur) >= 2:
            out.append(cur)
        cur = []

    for item in drawing.get("items", ()):
        op = item[0]
        if op == "l":
            p1, p2 = item[1], item[2]
            a, b = (p1.x, p1.y), (p2.x, p2.y)
            if cur and math.dist(cur[-1], a) < 1e-6:
                cur.append(b)
            else:
                flush()
                cur = [a, b]
        elif op == "c":
            p0, p1, p2, p3 = item[1], item[2], item[3], item[4]
            a = (p0.x, p0.y)
            if not (cur and math.dist(cur[-1], a) < 1e-6):
                flush()
                cur = [a]
            cur.extend(_bezier(p0, p1, p2, p3))
        elif op == "re":
            flush()
            r = item[1]
            out.append([(r.x0, r.y0), (r.x1, r.y0), (r.x1, r.y1), (r.x0, r.y1), (r.x0, r.y0)])
        elif op == "qu":
            flush()
            q = item[1]
            out.append([(q.ul.x, q.ul.y), (q.ur.x, q.ur.y),
                        (q.lr.x, q.lr.y), (q.ll.x, q.ll.y), (q.ul.x, q.ul.y)])
    flush()
    return out


def extract_paths(pdf_path: str, affine: Affine | None = None,
                  clip: fitz.Rect | None = None,
                  roles: set[str] | None = None) -> list[Path]:
    """All drawing paths, grouped by CAD layer, optionally transformed to UTM."""
    doc = fitz.open(pdf_path)
    try:
        page = doc[0]
        if page.rotation:
            page.set_rotation(0)
        out: list[Path] = []
        for dr in page.get_drawings():
            layer = dr.get("layer") or ""
            role = canonical_layer(layer)
            if roles is not None and role not in roles:
                continue
            r = dr.get("rect")
            if clip is not None and r is not None and not (r & clip).is_valid:
                continue
            polys = path_polylines(dr)
            if not polys:
                continue
            if affine is not None:
                polys = [[affine.to_utm(x, y) for x, y in ln] for ln in polys]
            out.append(Path(layer=layer, role=role, lines=polys,
                            closed=bool(dr.get("closePath")),
                            stroke=dr.get("color"), fill=dr.get("fill"),
                            width=float(dr.get("width") or 0.0)))
        return out
    finally:
        doc.close()


def layer_summary(pdf_path: str) -> dict[str, dict]:
    """Path/length counts per canonical role — useful for auditing a sheet."""
    doc = fitz.open(pdf_path)
    try:
        page = doc[0]
        if page.rotation:
            page.set_rotation(0)
        agg: dict[str, dict] = defaultdict(lambda: {"paths": 0, "layers": set()})
        for dr in page.get_drawings():
            layer = dr.get("layer") or ""
            role = canonical_layer(layer)
            agg[role]["paths"] += 1
            agg[role]["layers"].add(layer)
        return {k: {"paths": v["paths"], "layers": sorted(v["layers"])} for k, v in agg.items()}
    finally:
        doc.close()
