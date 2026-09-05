"""
Subcatchments for the SWMM model.

The published microwatershed layer only covers about 58% of the GCC area, so it
cannot be the sole basis for runoff. Instead each manhole is given the area that
is closer to it than to any other manhole — a Thiessen/Voronoi cell — clipped to
the city boundary. This is the usual fallback for urban drainage models where a
surveyed catchment delineation does not exist, and it guarantees the whole
serviced area drains somewhere.

Where a published microwatershed *is* available the cell is tagged with it, so
runs can be grouped or compared against the official delineation.
"""
from __future__ import annotations

import math

import numpy as np
from osgeo import ogr
from scipy.spatial import Voronoi


def _ring(coords):
    r = ogr.Geometry(ogr.wkbLinearRing)
    for x, y in coords:
        r.AddPoint(float(x), float(y))
    if coords[0] != coords[-1]:
        r.AddPoint(float(coords[0][0]), float(coords[0][1]))
    return r


def _poly(coords):
    p = ogr.Geometry(ogr.wkbPolygon)
    p.AddGeometry(_ring(coords))
    return p


def voronoi_cells(points, boundary, margin: float = 5000.0):
    """Thiessen polygon per point, clipped to `boundary`.

    Returns a list the same length as `points`; an entry is None where the cell
    fell entirely outside the boundary.
    """
    if len(points) < 4:
        return [None] * len(points)
    pts = np.asarray(points, dtype=float)

    # Distant sentinels close the unbounded outer cells so every input point
    # gets a finite polygon.
    x0, y0 = pts.min(axis=0) - margin
    x1, y1 = pts.max(axis=0) + margin
    far = np.array([[x0, y0], [x0, y1], [x1, y0], [x1, y1],
                    [(x0 + x1) / 2, y0 - margin], [(x0 + x1) / 2, y1 + margin],
                    [x0 - margin, (y0 + y1) / 2], [x1 + margin, (y0 + y1) / 2]])
    vor = Voronoi(np.vstack([pts, far]))

    out: list = []
    for i in range(len(pts)):
        region = vor.regions[vor.point_region[i]]
        if not region or -1 in region:
            out.append(None)
            continue
        coords = [tuple(vor.vertices[v]) for v in region]
        if len(coords) < 3:
            out.append(None)
            continue
        cell = _poly(coords)
        if not cell.IsValid():
            cell = cell.Buffer(0)
        try:
            cell = cell.Intersection(boundary)
        except Exception:
            out.append(None)
            continue
        out.append(None if (cell is None or cell.IsEmpty()) else cell)
    return out


def swmm_subcatchment_params(area_m2: float, imperv_pct: float = 75.0):
    """Width, slope and imperviousness for a SWMM subcatchment.

    Width is the standard `area / flow-length` estimate, taking the flow length
    as the square root of the area — a square cell — which is the usual default
    when overland flow paths have not been surveyed.
    """
    area_ha = area_m2 / 10000.0
    width = math.sqrt(area_m2) if area_m2 > 0 else 1.0
    return {
        "area_ha": round(area_ha, 4),
        "width_m": round(width, 2),
        "imperv_pct": imperv_pct,
        "slope_pct": 0.5,
    }
