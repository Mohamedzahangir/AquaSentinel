"""
Load the extracted Chennai SWD model into QGIS, styled.

Run from QGIS:  Plugins -> Python Console -> Show Editor -> open this file -> Run.
Everything is EPSG:32644 (WGS 84 / UTM zone 44N).
"""
import os

from qgis.core import (
    QgsProject, QgsVectorLayer, QgsRasterLayer, QgsCoordinateReferenceSystem,
    QgsSymbol, QgsSingleSymbolRenderer, QgsGraduatedSymbolRenderer,
    QgsRendererRange, QgsSimpleLineSymbolLayer, QgsMarkerSymbol,
    QgsLineSymbol, QgsProperty, QgsSymbolLayer, QgsArrowSymbolLayer,
)
from qgis.PyQt.QtGui import QColor

ROOT = "/Users/admin/Desktop/projects/swd/derived"
GPKG = os.path.join(ROOT, "chennai_swd.gpkg")

project = QgsProject.instance()
project.setCrs(QgsCoordinateReferenceSystem("EPSG:32644"))
root = project.layerTreeRoot()

group = root.findGroup("Chennai SWD")
if group is not None:
    root.removeChildNode(group)
group = root.insertGroup(0, "Chennai SWD")


def add(uri, name, provider="ogr"):
    lyr = QgsVectorLayer(uri, name, provider)
    if not lyr.isValid():
        print(f"  !! invalid: {name}")
        return None
    project.addMapLayer(lyr, False)
    group.addLayer(lyr)
    return lyr


# ---- context first so it draws underneath ----
ctx = add(f"{GPKG}|layername=map_context", "Map context (roads, boundaries, water)")
if ctx:
    sym = QgsLineSymbol.createSimple({"color": "180,180,180,140", "width": "0.2"})
    ctx.setRenderer(QgsSingleSymbolRenderer(sym))
    ctx.setOpacity(0.7)

# ---- the complete drain network, exactly as drawn on the sheets ----
drains = add(f"{GPKG}|layername=swd_drains", "SWD drains (full network, as drawn)")
if drains:
    sym = QgsLineSymbol.createSimple({"color": "0,229,255", "width": "0.5"})
    drains.setRenderer(QgsSingleSymbolRenderer(sym))

# ---- the attributed subset, with flow direction ----
cond = add(f"{GPKG}|layername=swd_conduits", "SWD conduits (attributed, flow direction)")
if cond:
    arrow = QgsArrowSymbolLayer.create({
        "arrow_width": "0.9", "arrow_start_width": "0.9",
        "head_length": "2.4", "head_thickness": "2.2",
        "is_curved": "0", "arrow_type": "0",
    })
    sym = QgsLineSymbol()
    sym.changeSymbolLayer(0, arrow)
    sym.setColor(QColor(255, 214, 0))
    cond.setRenderer(QgsSingleSymbolRenderer(sym))
    cond.setLabelsEnabled(False)

# ---- nodes graduated by invert level (the elevation profile) ----
nodes = add(f"{GPKG}|layername=swd_nodes", "Manholes (invert level, m MSL)")
if nodes:
    ramp = [
        (-99, 2, "#08306b"), (2, 4, "#2171b5"), (4, 6, "#6baed6"),
        (6, 8, "#c6dbef"), (8, 11, "#fdd0a2"), (11, 15, "#fd8d3c"),
        (15, 999, "#a63603"),
    ]
    ranges = []
    for lo, hi, colour in ramp:
        s = QgsMarkerSymbol.createSimple(
            {"name": "circle", "color": colour, "size": "2.2",
             "outline_color": "40,40,40", "outline_width": "0.2"})
        ranges.append(QgsRendererRange(lo, hi, s, f"{lo} – {hi} m"))
    nodes.setRenderer(QgsGraduatedSymbolRenderer("invert", ranges))

# ---- satellite backdrop for alignment checking ----
if not any("google" in l.source().lower() or "arcgisonline" in l.source().lower()
           for l in project.mapLayers().values()):
    sat = QgsRasterLayer(
        "type=xyz&zmin=0&zmax=20&url="
        "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/"
        "tile/%7Bz%7D/%7By%7D/%7Bx%7D",
        "Esri World Imagery", "wms")
    if sat.isValid():
        project.addMapLayer(sat, False)
        group.addLayer(sat)

# zoom to the network
if nodes and nodes.featureCount():
    from qgis.utils import iface
    canvas = iface.mapCanvas()
    canvas.setDestinationCrs(project.crs())
    canvas.setExtent(nodes.extent().buffered(500))
    canvas.refresh()

print("Loaded Chennai SWD model.")
for lyr in group.findLayers():
    l = lyr.layer()
    try:
        print(f"  {l.name():48s} {l.featureCount():7d} features")
    except Exception:
        print(f"  {l.name()}")
