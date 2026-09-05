"""Write the extracted model out as ESRI Shapefiles / GeoPackage in EPSG:32644."""
from __future__ import annotations

import os

from osgeo import gdal, ogr, osr

gdal.UseExceptions()
ogr.UseExceptions()

UTM_EPSG = 32644

# Shapefile field names are capped at 10 characters.
NODE_FIELDS = [
    ("node_id", ogr.OFTString, 24), ("ward", ogr.OFTInteger, 0),
    ("si", ogr.OFTInteger, 0), ("drain_top", ogr.OFTReal, 0),
    ("invert", ogr.OFTReal, 0), ("road_edge", ogr.OFTReal, 0),
    ("width_m", ogr.OFTReal, 0), ("depth_m", ogr.OFTReal, 0),
    ("type", ogr.OFTString, 4), ("is_outlet", ogr.OFTInteger, 0),
    ("conf", ogr.OFTReal, 0), ("snap_m", ogr.OFTReal, 0),
    ("raw_label", ogr.OFTString, 32),
]

COND_FIELDS = [
    ("cond_id", ogr.OFTString, 40), ("from_node", ogr.OFTString, 24),
    ("to_node", ogr.OFTString, 24), ("ward", ogr.OFTInteger, 0),
    ("length_m", ogr.OFTReal, 0), ("us_invert", ogr.OFTReal, 0),
    ("ds_invert", ogr.OFTReal, 0), ("width_m", ogr.OFTReal, 0),
    ("depth_m", ogr.OFTReal, 0), ("type", ogr.OFTString, 4),
    ("slope", ogr.OFTReal, 0), ("dir_src", ogr.OFTString, 10),
]

LINE_FIELDS = [
    ("ward", ogr.OFTInteger, 0), ("role", ogr.OFTString, 24),
    ("cad_layer", ogr.OFTString, 40),
]


def _srs():
    s = osr.SpatialReference()
    s.ImportFromEPSG(UTM_EPSG)
    return s


def _make_layer(ds, name, geom_type, fields):
    lyr = ds.CreateLayer(name, _srs(), geom_type)
    for fname, ftype, width in fields:
        fd = ogr.FieldDefn(fname, ftype)
        if width:
            fd.SetWidth(width)
        if ftype == ogr.OFTReal:
            fd.SetWidth(19)
            fd.SetPrecision(6)
        lyr.CreateField(fd)
    return lyr


def _set(feat, name, value):
    if value is None:
        feat.SetFieldNull(name)
    elif isinstance(value, bool):
        feat.SetField(name, int(value))
    else:
        feat.SetField(name, value)


def _open(path: str, driver: str):
    drv = ogr.GetDriverByName(driver)
    if driver == "ESRI Shapefile":
        for ext in (".shp", ".shx", ".dbf", ".prj", ".cpg"):
            p = os.path.splitext(path)[0] + ext
            if os.path.exists(p):
                os.remove(p)
    elif os.path.exists(path):
        os.remove(path)
    return drv.CreateDataSource(path)


def write_nodes(path: str, nodes, driver="ESRI Shapefile", layer_name="swd_nodes"):
    ds = _open(path, driver)
    lyr = _make_layer(ds, layer_name, ogr.wkbPoint, NODE_FIELDS)
    defn = lyr.GetLayerDefn()
    for nd in nodes:
        f = ogr.Feature(defn)
        _set(f, "node_id", nd.node_id); _set(f, "ward", nd.ward)
        _set(f, "si", nd.si); _set(f, "drain_top", nd.drain_top)
        _set(f, "invert", nd.invert); _set(f, "road_edge", nd.road_edge)
        _set(f, "width_m", nd.width); _set(f, "depth_m", nd.depth)
        _set(f, "type", nd.drain_type); _set(f, "is_outlet", nd.is_outlet)
        _set(f, "conf", round(nd.label_conf, 3))
        _set(f, "snap_m", nd.snap_dist); _set(f, "raw_label", nd.raw_label[:32])
        g = ogr.Geometry(ogr.wkbPoint)
        g.AddPoint(nd.east, nd.north)
        f.SetGeometry(g)
        lyr.CreateFeature(f)
        f = None
    ds = None
    return len(nodes)


def write_conduits(path: str, conduits, driver="ESRI Shapefile", layer_name="swd_conduits"):
    ds = _open(path, driver)
    lyr = _make_layer(ds, layer_name, ogr.wkbLineString, COND_FIELDS)
    defn = lyr.GetLayerDefn()
    for c in conduits:
        f = ogr.Feature(defn)
        for k, v in c.as_record().items():
            _set(f, k, v)
        g = ogr.Geometry(ogr.wkbLineString)
        for x, y in c.geometry:
            g.AddPoint(x, y)
        f.SetGeometry(g)
        lyr.CreateFeature(f)
        f = None
    ds = None
    return len(conduits)


def write_lines(path: str, paths, ward=None, driver="ESRI Shapefile", layer_name="lines"):
    """Generic writer for the map's linework (drains, roads, boundaries, ...)."""
    ds = _open(path, driver)
    lyr = _make_layer(ds, layer_name, ogr.wkbLineString, LINE_FIELDS)
    defn = lyr.GetLayerDefn()
    n = 0
    for p in paths:
        for ln in p.lines:
            if len(ln) < 2:
                continue
            f = ogr.Feature(defn)
            _set(f, "ward", ward); _set(f, "role", p.role)
            _set(f, "cad_layer", (p.layer or "")[:40])
            g = ogr.Geometry(ogr.wkbLineString)
            for x, y in ln:
                g.AddPoint(x, y)
            f.SetGeometry(g)
            lyr.CreateFeature(f)
            f = None
            n += 1
    ds = None
    return n


def write_merged_lines(path: str, lines, ward=None, role="drain",
                       driver="ESRI Shapefile", layer_name="drains"):
    ds = _open(path, driver)
    lyr = _make_layer(ds, layer_name, ogr.wkbLineString, LINE_FIELDS)
    defn = lyr.GetLayerDefn()
    for ln in lines:
        if len(ln) < 2:
            continue
        f = ogr.Feature(defn)
        _set(f, "ward", ward); _set(f, "role", role); _set(f, "cad_layer", "")
        g = ogr.Geometry(ogr.wkbLineString)
        for x, y in ln:
            g.AddPoint(x, y)
        f.SetGeometry(g)
        lyr.CreateFeature(f)
        f = None
    ds = None
    return len(lines)
