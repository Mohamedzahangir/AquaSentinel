"""
Build the node table: manhole identifier -> map position + surveyed levels.

The schedule gives every manhole its levels but no location; the map gives
every manhole a location but only a tiny OCR'd label. Joining the two on the
identifier produces the nodes a hydraulic model needs.

OCR of 4 pt text is imperfect, so map identifiers are snapped onto the set of
identifiers the schedule actually contains, using a cheap edit distance with
confusable-character costs. That turns "C8VU" into C8 and Cyrillic "Сб" into C6.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# Characters the recogniser routinely swaps at this size.
CONFUSABLE = {
    "O": "0", "О": "0", "Q": "0", "D": "0",
    "I": "1", "L": "1", "l": "1", "|": "1", "İ": "1",
    "Z": "2", "S": "5", "B": "8", "G": "6", "б": "6",
    "С": "C", "А": "A", "В": "B", "Е": "E", "Р": "P", "М": "M",
    "Т": "T", "Н": "H", "К": "K", "Х": "X", "У": "Y", "О": "O",
}

ID_RE = re.compile(r"^([A-Z]{1,3})[\s\-_.]*([0-9]{1,3})$")
OUTLET_RE = re.compile(r"\(?\s*O\s*[L1I]\s*\)?", re.I)


def _latinise(s: str) -> str:
    return "".join(CONFUSABLE.get(ch, ch) for ch in s)


def normalise_id(raw: str) -> tuple[str | None, bool]:
    """Return (canonical id, is_outlet) from a raw OCR string."""
    if not raw:
        return None, False
    s = raw.strip().upper()
    outlet = bool(OUTLET_RE.search(s))
    s = OUTLET_RE.sub("", s)
    s = re.sub(r"[^0-9A-Za-zА-Яа-я]", "", s)
    s = _latinise(s).upper()
    m = ID_RE.match(s)
    if m:
        return f"{m.group(1)}{int(m.group(2))}", outlet
    # Fall back to the first letter-run followed by a digit-run anywhere.
    m = re.search(r"([A-Z]{1,3})([0-9]{1,3})", s)
    if m:
        return f"{m.group(1)}{int(m.group(2))}", outlet
    return None, outlet


def _cost(a: str, b: str) -> float:
    if a == b:
        return 0.0
    if CONFUSABLE.get(a) == b or CONFUSABLE.get(b) == a:
        return 0.35
    if a.isdigit() and b.isdigit():
        return 1.0
    return 1.0


def _distance(a: str, b: str) -> float:
    """Levenshtein with reduced cost for confusable substitutions."""
    m, n = len(a), len(b)
    prev = [float(j) for j in range(n + 1)]
    for i in range(1, m + 1):
        cur = [float(i)] + [0.0] * n
        for j in range(1, n + 1):
            cur[j] = min(prev[j] + 1.0, cur[j - 1] + 1.0,
                         prev[j - 1] + _cost(a[i - 1], b[j - 1]))
        prev = cur
    return prev[n]


def _split_id(s: str) -> tuple[str, str]:
    m = re.match(r"^([A-Z]+)([0-9]+)$", s)
    return (m.group(1), m.group(2)) if m else (s, "")


def snap_id(raw_id: str, known: list[str], max_dist: float = 1.2) -> str | None:
    """Snap an OCR'd identifier onto the schedule's identifier set.

    The letter prefix denotes a distinct drain run, so it is never edited —
    correcting "F11" to "A11" would silently move a node onto another line.
    Only the numeric part is allowed to differ.
    """
    if not raw_id:
        return None
    if raw_id in known:
        return raw_id
    pre, num = _split_id(raw_id)
    if not num:
        return None
    best, best_d = None, 1e9
    for k in known:
        kpre, knum = _split_id(k)
        if kpre != pre or not knum:
            continue
        if abs(len(knum) - len(num)) > max_dist:
            continue
        d = _distance(num, knum)
        if d < best_d:
            best, best_d = k, d
    return best if best_d <= max_dist else None


@dataclass
class Node:
    node_id: str
    east: float
    north: float
    ward: int | None = None
    drain_top: float | None = None
    invert: float | None = None
    road_edge: float | None = None
    width: float | None = None
    depth: float | None = None
    drain_type: str | None = None
    is_outlet: bool = False
    si: int | None = None
    label_conf: float = 0.0
    raw_label: str = ""
    matched: bool = False
    snap_dist: float | None = None
    locator: str = "id"          # 'id' | 'invert' — how the position was found

    def as_record(self) -> dict:
        return {
            "node_id": self.node_id, "ward": self.ward,
            "east": round(self.east, 3), "north": round(self.north, 3),
            "drain_top": self.drain_top, "invert": self.invert,
            "road_edge": self.road_edge, "width": self.width, "depth": self.depth,
            "drain_type": self.drain_type, "is_outlet": self.is_outlet,
            "si": self.si, "conf": round(self.label_conf, 3),
            "raw_label": self.raw_label, "matched": self.matched,
            "snap_m": self.snap_dist, "locator": self.locator,
        }


_LEVEL_RE = re.compile(r"\d{1,2}\.\d{3}")


def _invert_index(invert_labels) -> dict[float, object]:
    """Map an invert value to its unique annotation on the map.

    Sheets annotate each manhole with its MSL invert. Where a value occurs only
    once it pins that manhole's position even if its identifier was unreadable —
    or, on sheets that carry no identifier layer at all, it is the only link.
    """
    seen: dict[float, list] = {}
    for lb in invert_labels or ():
        for m in _LEVEL_RE.finditer(lb.text or ""):
            seen.setdefault(float(m.group()), []).append(lb)
    return {v: lbs[0] for v, lbs in seen.items() if len(lbs) == 1}


def build_nodes(labels, schedule: list[dict], affine, ward: int | None = None,
                max_dist: float = 1.2, invert_labels=None) -> tuple[list[Node], dict]:
    """Join OCR'd map labels to schedule rows. Returns (nodes, stats)."""
    LEVELS = ("drain_top", "invert", "road_edge", "width", "depth")
    by_id: dict[str, dict] = {}
    for row in schedule:
        rid, outlet = normalise_id(row.get("manhole_id") or "")
        if not rid:
            continue
        # A real schedule row carries at least one surveyed value; this drops
        # header remnants and any unrelated table that looked schedule-shaped.
        if not any(row.get(c) is not None for c in LEVELS):
            continue
        row = dict(row)
        row["_outlet"] = outlet
        # keep the first occurrence; duplicates in the schedule are rare
        by_id.setdefault(rid, row)
    known = list(by_id)

    nodes: list[Node] = []
    n_snap = n_exact = n_unmatched = 0
    for lb in labels:
        rid, outlet = normalise_id(lb.text)
        if not rid:
            continue
        if rid in by_id:
            key = rid
            n_exact += 1
        else:
            key = snap_id(rid, known, max_dist=max_dist)
            if key:
                n_snap += 1
            else:
                n_unmatched += 1
        e, n = affine.to_utm(lb.cx, lb.cy)
        row = by_id.get(key or "", {})
        nodes.append(Node(
            node_id=key or rid, east=e, north=n, ward=ward,
            drain_top=row.get("drain_top"), invert=row.get("invert"),
            road_edge=row.get("road_edge"), width=row.get("width"),
            depth=row.get("depth"), drain_type=row.get("drain_type"),
            is_outlet=bool(outlet or row.get("_outlet")),
            si=row.get("si"), label_conf=lb.conf, raw_label=lb.text,
            matched=bool(key),
        ))

    # Collapse duplicate identifiers (a label may be read twice) keeping the
    # highest-confidence position.
    best: dict[str, Node] = {}
    for nd in nodes:
        cur = best.get(nd.node_id)
        if cur is None or (nd.matched, nd.label_conf) > (cur.matched, cur.label_conf):
            best[nd.node_id] = nd

    # Fall back to the invert annotation for schedule rows the identifier OCR
    # could not place (and for sheets that carry no identifier layer at all).
    n_by_invert = 0
    if invert_labels:
        idx = _invert_index(invert_labels)
        counts: dict[float, int] = {}
        for rid, row in by_id.items():
            v = row.get("invert")
            if v is not None:
                counts[v] = counts.get(v, 0) + 1
        for rid, row in by_id.items():
            if rid in best:
                continue
            v = row.get("invert")
            if v is None or counts.get(v) != 1:
                continue
            lb = idx.get(v)
            if lb is None:
                continue
            e, n = affine.to_utm(lb.cx, lb.cy)
            best[rid] = Node(
                node_id=rid, east=e, north=n, ward=ward,
                drain_top=row.get("drain_top"), invert=row.get("invert"),
                road_edge=row.get("road_edge"), width=row.get("width"),
                depth=row.get("depth"), drain_type=row.get("drain_type"),
                is_outlet=bool(row.get("_outlet")), si=row.get("si"),
                label_conf=lb.conf, raw_label=lb.text, matched=True,
                locator="invert",
            )
            n_by_invert += 1

    out = sorted(best.values(), key=lambda n: (n.node_id[:2], n.si or 0, n.node_id))

    stats = {
        "located_by_invert": n_by_invert,
        "schedule_rows": len(schedule),
        "schedule_ids": len(known),
        "map_labels": len(labels),
        "exact": n_exact, "snapped": n_snap, "unmatched": n_unmatched,
        "nodes": len(out),
        "with_invert": sum(1 for n in out if n.invert is not None),
        "located_of_schedule": round(
            100.0 * sum(1 for n in out if n.matched) / max(len(known), 1), 1),
    }
    return out, stats
