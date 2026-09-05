"""
Cut the drawn drain centrelines at the manholes that sit on them.

Every conduit is a slice of the map's own linework, so it follows the street
exactly as surveyed. Manholes are projected onto the centreline they annotate
(the identifier is drawn beside the structure, not on it) and the run is split
between consecutive ones.

Flow direction comes from the surveyed inverts (water runs downhill); where the
sheet also carries Drain_Flow arrows those decide when the inverts are equal or
missing.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass

SNAP_TOL = 12.0        # metres a manhole may sit off its drain centreline
MIN_LEN = 1.0          # metres; shorter pairs are treated as the same node


@dataclass
class Conduit:
    conduit_id: str
    from_node: str
    to_node: str
    length: float
    us_invert: float | None
    ds_invert: float | None
    width: float | None
    depth: float | None
    drain_type: str | None
    slope: float | None
    ward: int | None
    direction: str          # 'invert', 'arrow', 'assumed'
    geometry: list[tuple[float, float]]

    def as_record(self) -> dict:
        return {
            "cond_id": self.conduit_id, "from_node": self.from_node,
            "to_node": self.to_node, "ward": self.ward,
            "length_m": round(self.length, 3),
            "us_invert": self.us_invert, "ds_invert": self.ds_invert,
            "width_m": self.width, "depth_m": self.depth,
            "type": self.drain_type, "slope": self.slope,
            "dir_src": self.direction,
        }


def _seg_project(p, a, b):
    """Project p onto segment ab -> (distance, t in [0,1])."""
    ax, ay = a
    bx, by = b
    px, py = p
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    if L2 <= 1e-12:
        return math.dist(p, a), 0.0
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L2))
    qx, qy = ax + t * dx, ay + t * dy
    return math.hypot(px - qx, py - qy), t


def project_onto_line(pt, line):
    """Nearest point on a polyline -> (distance, chainage)."""
    best_d, best_s = float("inf"), 0.0
    s = 0.0
    for i in range(len(line) - 1):
        a, b = line[i], line[i + 1]
        seg = math.dist(a, b)
        d, t = _seg_project(pt, a, b)
        if d < best_d:
            best_d, best_s = d, s + t * seg
        s += seg
    return best_d, best_s


def slice_line(line, s0: float, s1: float):
    """Sub-polyline between two chainages."""
    if s1 < s0:
        s0, s1 = s1, s0
    out = []
    s = 0.0
    for i in range(len(line) - 1):
        a, b = line[i], line[i + 1]
        seg = math.dist(a, b)
        if seg <= 0:
            continue
        e = s + seg
        if e < s0 or s > s1:
            s = e
            continue
        t0 = max(0.0, (s0 - s) / seg)
        t1 = min(1.0, (s1 - s) / seg)
        p0 = (a[0] + (b[0] - a[0]) * t0, a[1] + (b[1] - a[1]) * t0)
        p1 = (a[0] + (b[0] - a[0]) * t1, a[1] + (b[1] - a[1]) * t1)
        if not out or math.dist(out[-1], p0) > 1e-9:
            out.append(p0)
        out.append(p1)
        s = e
    return out or [line[0], line[-1]]


def _heading(pts, at_end: bool, span: float = 6.0):
    """Unit direction of a polyline near one end, pointing outward."""
    seq = pts[::-1] if at_end else pts
    ax, ay = seq[0]
    for x, y in seq[1:]:
        d = math.hypot(x - ax, y - ay)
        if d >= span:
            return ((ax - x) / d, (ay - y) / d)
    bx, by = seq[-1]
    d = math.hypot(ax - bx, ay - by)
    return ((ax - bx) / d, (ay - by) / d) if d > 1e-9 else (0.0, 0.0)


def merge_lines(lines, tol: float = 8.0, min_cos: float = -0.5):
    """Stitch the drawn fragments back into continuous drain runs.

    A single drain is exported as many short dashes, so without joining them
    consecutive manholes land on different objects and no conduit can form.
    Candidates are scored on how well they continue the current heading as well
    as on gap size, so a run carries straight through a junction instead of
    turning down whichever side branch happened to be nearest.
    """
    segs = [list(ln) for ln in lines if len(ln) >= 2]
    if not segs:
        return []
    cell = max(tol, 1e-6)

    def key(p):
        return (int(math.floor(p[0] / cell)), int(math.floor(p[1] / cell)))

    ends: dict[tuple, list[int]] = {}
    for i, s in enumerate(segs):
        ends.setdefault(key(s[0]), []).append(i)
        ends.setdefault(key(s[-1]), []).append(i)

    def nearby(p):
        cx, cy = key(p)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                yield from ends.get((cx + dx, cy + dy), ())

    used = [False] * len(segs)
    out = []
    for i in range(len(segs)):
        if used[i]:
            continue
        used[i] = True
        chain = list(segs[i])
        # extend from both ends
        for _ in range(2):
            while True:
                tail = chain[-1]
                hx, hy = _heading(chain, at_end=True)
                best = None
                for j in nearby(tail):
                    if used[j]:
                        continue
                    cand = segs[j]
                    d0, d1 = math.dist(cand[0], tail), math.dist(cand[-1], tail)
                    fwd = d0 <= d1
                    gap = d0 if fwd else d1
                    if gap > tol:
                        continue
                    cx, cy = _heading(cand, at_end=not fwd)
                    # the candidate's outward heading points back at us
                    cos = -(hx * cx + hy * cy)
                    if cos < min_cos:
                        continue
                    score = gap - cos * tol      # prefer straight continuations
                    if best is None or score < best[0]:
                        best = (score, j, cand[1:] if fwd else cand[-2::-1])
                if best is None:
                    break
                used[best[1]] = True
                chain.extend(best[2])
            chain.reverse()
        out.append(chain)
    return out


def _arrow_direction(arrows, mid, radius: float = 25.0):
    """Unit vector of the nearest flow arrow to `mid`, if any."""
    best, best_d = None, radius
    for a in arrows:
        d = math.dist(mid, a["mid"])
        if d < best_d:
            best, best_d = a, d
    return best["vec"] if best else None


def flow_arrows(paths):
    """Reduce Drain_Flow paths to (midpoint, unit vector) pairs.

    An arrow head is drawn as a short path; its long axis gives the direction.
    """
    out = []
    for p in paths:
        for ln in p.lines:
            if len(ln) < 2:
                continue
            x0, y0 = ln[0]
            x1, y1 = ln[-1]
            dx, dy = x1 - x0, y1 - y0
            L = math.hypot(dx, dy)
            if L < 0.5:
                continue
            out.append({"mid": ((x0 + x1) / 2, (y0 + y1) / 2),
                        "vec": (dx / L, dy / L), "len": L})
    return out


def snap_nodes_to_drains(nodes, lines, snap_tol: float = SNAP_TOL):
    """Move each node onto the drain centreline its label annotates.

    The identifier is drawn beside the manhole, not on it, so the raw label
    position sits a few metres off the pipe. Returns {node_id: (line_index,
    chainage)} for those that could be placed.
    """
    placed: dict[str, tuple[int, float]] = {}
    for nd in nodes:
        pt = (nd.east, nd.north)
        best = (None, snap_tol, 0.0)
        for i, ln in enumerate(lines):
            d, s = project_onto_line(pt, ln)
            if d < best[1]:
                best = (i, d, s)
        if best[0] is None:
            nd.snap_dist = None
            continue
        nd.east, nd.north = slice_line(lines[best[0]], best[2], best[2])[0]
        nd.snap_dist = round(best[1], 2)
        placed[nd.node_id] = (best[0], best[2])
    return placed


def build_conduits(nodes, drain_paths, arrows=None, ward=None,
                   snap_tol: float = SNAP_TOL,
                   max_span: float = 600.0) -> tuple[list[Conduit], dict]:
    """Cut the drawn drain centrelines at the manholes that sit on them.

    Every conduit is a slice of real map linework — never a synthetic straight
    line between two manholes, which would cut across blocks instead of
    following the street. Consecutive manholes that do not share a drawn run
    simply do not produce a conduit; the gap is reported instead of invented.
    """
    arrows = arrows or []
    raw = [ln for p in drain_paths for ln in p.lines if len(ln) >= 2]
    lines = [ln for ln in merge_lines(raw)
             if sum(math.dist(ln[i], ln[i + 1]) for i in range(len(ln) - 1)) >= 2.0]

    placed = snap_nodes_to_drains(nodes, lines, snap_tol)
    by_id = {nd.node_id: nd for nd in nodes}

    # Group the manholes by the drawn run they sit on and order them along it,
    # so each conduit is a slice of that run.
    on_run: dict[int, list[tuple[float, object]]] = {}
    for nd in nodes:
        pl = placed.get(nd.node_id)
        if pl:
            on_run.setdefault(pl[0], []).append((pl[1], nd))

    conduits: list[Conduit] = []
    n_arrow = n_invert = n_assumed = 0
    n_geom = 0
    skipped_far = no_shared_run = 0

    for li, members in on_run.items():
        members.sort(key=lambda t: t[0])
        ln = lines[li]
        for (s0, a), (s1, b) in zip(members, members[1:]):
            length = s1 - s0
            if length < MIN_LEN:
                continue
            if length > max_span:
                skipped_far += 1
                continue
            geom = slice_line(ln, s0, s1)
            n_geom += 1

            ia, ib = a.invert, b.invert
            us, ds, src = a, b, "assumed"
            if ia is not None and ib is not None and abs(ia - ib) > 1e-6:
                if ib > ia:
                    us, ds = b, a
                    geom = geom[::-1]
                src = "invert"
                n_invert += 1
            else:
                mid = geom[len(geom) // 2]
                vec = _arrow_direction(arrows, mid)
                if vec is not None:
                    dx = geom[-1][0] - geom[0][0]
                    dy = geom[-1][1] - geom[0][1]
                    if dx * vec[0] + dy * vec[1] < 0:
                        us, ds = b, a
                        geom = geom[::-1]
                    src = "arrow"
                    n_arrow += 1
                else:
                    # numbering runs upstream from the outlet, so the higher
                    # index is the upstream end
                    us, ds = b, a
                    geom = geom[::-1]
                    n_assumed += 1

            ui, di = us.invert, ds.invert
            slope = round((ui - di) / length, 6) if (ui is not None and di is not None and length > 0) else None
            conduits.append(Conduit(
                conduit_id=f"{us.node_id}->{ds.node_id}",
                from_node=us.node_id, to_node=ds.node_id,
                length=length, us_invert=ui, ds_invert=di,
                width=us.width if us.width is not None else ds.width,
                depth=us.depth if us.depth is not None else ds.depth,
                drain_type=us.drain_type or ds.drain_type,
                slope=slope, ward=ward, direction=src, geometry=geom,
            ))

    stats = {
        "drain_lines": len(lines), "nodes_in": len(nodes),
        "nodes_snapped": len(placed), "nodes_unplaced": len(nodes) - len(placed),
        "conduits": len(conduits), "geom_from_drain": n_geom,
        "dir_from_invert": n_invert, "dir_from_arrow": n_arrow,
        "dir_assumed": n_assumed, "skipped_too_far": skipped_far,
        "adverse_slope": sum(1 for c in conduits if c.slope is not None and c.slope < 0),
    }
    return conduits, stats
