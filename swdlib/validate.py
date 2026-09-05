"""
Physical-plausibility checks and OCR repair for the surveyed attributes.

Two failure modes are worth separating:

* recoverable — the recogniser dropped a decimal point, so "180" stands for
  0.180 m. The schedule always prints three decimals, so a bare integer in a
  dimension column is unambiguous and can be repaired.
* unrecoverable — a value that is simply impossible (a negative depth, an
  invert above the drain top). Those are nulled and flagged rather than guessed
  at, so a modeller can see what was dropped.
"""
from __future__ import annotations

import re

# Chennai storm water drains: the largest surveyed sections are canal-sized.
WIDTH_RANGE = (0.05, 12.0)
DEPTH_RANGE = (0.05, 8.0)
# Ground surface across the GCC area, m MSL.
LEVEL_RANGE = (-3.0, 60.0)

_BARE_INT = re.compile(r"^\d{1,4}$")


def repair_dimension(value, raw: str | None):
    """Restore a decimal point the recogniser dropped. Returns (value, flag)."""
    if value is None:
        return None, None
    if raw and _BARE_INT.match(raw.strip()):
        # The column is always printed to three decimals, so a bare integer is
        # a lost point: "180" -> 0.180, "1300" -> 1.300.
        digits = raw.strip()
        fixed = int(digits) / (10 ** min(len(digits), 3))
        if fixed != value:
            return fixed, "decimal_repaired"
    return value, None


def check_node(rec: dict) -> tuple[dict, list[str]]:
    """Repair what can be repaired, null what cannot. Returns (record, flags)."""
    out = dict(rec)
    flags: list[str] = []

    for col, rng in (("width", WIDTH_RANGE), ("depth", DEPTH_RANGE)):
        v, flag = repair_dimension(out.get(col), rec.get(f"{col}_raw"))
        if flag:
            flags.append(f"{col}_{flag}")
        if v is not None and not (rng[0] <= v <= rng[1]):
            flags.append(f"{col}_out_of_range")
            v = None
        out[col] = v

    for col in ("invert", "drain_top", "road_edge"):
        v = out.get(col)
        if v is not None and not (LEVEL_RANGE[0] <= v <= LEVEL_RANGE[1]):
            flags.append(f"{col}_out_of_range")
            out[col] = None

    inv, top, road = out.get("invert"), out.get("drain_top"), out.get("road_edge")
    # The invert is the bed of the drain; it cannot sit above the drain top or
    # above the road. A conflict means one of the two was misread, and there is
    # no way to tell which — so keep both and flag the node.
    if inv is not None and top is not None and inv > top + 0.02:
        flags.append("invert_above_drain_top")
    if inv is not None and road is not None and inv > road + 0.02:
        flags.append("invert_above_road")
    if top is not None and road is not None and abs(top - road) > 6.0:
        flags.append("drain_top_road_mismatch")

    return out, flags


def check_conduit(us: dict, ds: dict, length: float, slope: float | None) -> list[str]:
    flags: list[str] = []
    if slope is not None:
        if slope < 0:
            flags.append("adverse_slope")
        elif slope == 0:
            flags.append("flat")
        elif slope > 0.08:
            flags.append("steep")
    if length > 400:
        flags.append("long_span")
    return flags
