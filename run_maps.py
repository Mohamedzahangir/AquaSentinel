#!/usr/bin/env python3
"""
Shareable network maps as PNG — several variants, one style.

Plain cartography on purpose: ward outlines and a faint OSM road layer for
orientation, the network on top, flow arrows along the conduits, a legend and
the sources. No satellite imagery (licensing) and no per-element figures (they
belong in the viewer, not on a poster).

  .venv-linux/bin/python run_maps.py           -> maps/*.png
"""
import json, math, os
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.collections import LineCollection
from matplotlib.colors import LinearSegmentedColormap, ListedColormap, BoundaryNorm
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
from pyproj import Transformer

OUT = "maps"; os.makedirs(OUT, exist_ok=True)
TF = Transformer.from_crs("EPSG:4326", "EPSG:32644", always_xy=True)
net = json.load(open("web/data/network.json"))
nodes = {n["id"]: n for n in net["nodes"]}
for n in net["nodes"]: n["xy"] = TF.transform(n["lon"], n["lat"])
conds = net["conduits"]
for c in conds: c["xy"] = [TF.transform(*p) for p in c["g"]]
wards = json.load(open("web/data/wards.json"))
osm = json.load(open("derived/osm/roads.json"))
roads = [[TF.transform(g["lon"], g["lat"]) for g in w["geometry"]] for w in osm["elements"]
         if "geometry" in w and w.get("tags", {}).get("highway") in
         ("motorway", "trunk", "primary", "secondary", "tertiary", "residential", "unclassified")]
isDrawn = lambda c: "geometry_inferred" not in c["fg"] and "junction_link" not in c["fg"] and not c["x"]
isRouted = lambda c: "road_routed" in c["fg"]

xs = [n["xy"][0] for n in net["nodes"]]; ys = [n["xy"][1] for n in net["nodes"]]
PAD = 800
# frame on the 0.5–99.5 percentile so a handful of outlying manholes cannot stretch the sheet
X0, X1, Y0, Y1 = np.percentile(xs, 0.5) - PAD, np.percentile(xs, 99.5) + PAD, np.percentile(ys, 0.5) - PAD, np.percentile(ys, 99.5) + PAD

SOURCES = ("Sources: Greater Chennai Corporation / SECON-JBA “Base Map – Storm Water Drain” ward sheets, 2021 survey (107 of ~200 wards), "
           "digitised by georeferencing each sheet’s printed UTM grid and OCR of its schedule · flooded-street record: github.com/osm-in/flood-map · "
           "roads © OpenStreetMap contributors (ODbL) · ward boundaries: TNGIS · CRS EPSG:32644, levels m MSL.\n"
           "Map & extraction: @reclaimchennai · swd.reclaimchennai.city · The 2,157 schedule links between consecutive manholes are inferred, not surveyed; 857 that could not follow a road were dropped.")

BG, INK, DIM = "#0b1015", "#e8eef2", "#8a99a3"


def base(title, subtitle):
    H = 20; W = max(14, H * 0.88 * (X1 - X0) / (Y1 - Y0) / 0.96 + 1.5)     # sheet follows the network's aspect
    fig = plt.figure(figsize=(W, H), dpi=200, facecolor=BG)
    ax = fig.add_axes([0.02, 0.06, 0.96, 0.88]); ax.set_facecolor(BG)
    ax.set_xlim(X0, X1); ax.set_ylim(Y0, Y1); ax.set_aspect("equal"); ax.axis("off")
    ax.add_collection(LineCollection(roads, colors="#1e2a33", linewidths=0.35, zorder=1))
    for f in wards["features"]:
        g = f["geometry"]; rings = g["coordinates"] if g["type"] == "Polygon" else [r for p in g["coordinates"] for r in p]
        for r in rings:
            r = np.array([TF.transform(*p) for p in r])
            ax.plot(r[:, 0], r[:, 1], color="#ffb454" if f["properties"]["have"] else "#3a4750",
                    lw=0.5 if f["properties"]["have"] else 0.35, ls=(0, (3, 2)), zorder=2, alpha=0.9)
        if f["properties"]["have"]:
            c = np.mean(np.array([TF.transform(*p) for p in rings[0]]), axis=0)
            ax.text(c[0], c[1], str(f["properties"]["ward"]), color="#c58b3a", fontsize=5, ha="center", va="center", zorder=3, alpha=0.9)
    fig.text(0.02, 0.965, title, color=INK, fontsize=26, fontweight="bold", family="DejaVu Sans")
    fig.text(0.02, 0.948, subtitle, color=DIM, fontsize=11)
    fig.text(0.02, 0.012, SOURCES, color=DIM, fontsize=6.4, va="bottom", linespacing=1.5)
    # scale bar: 2 km
    sx, sy = X1 - 3200, Y0 + 500
    ax.plot([sx, sx + 2000], [sy, sy], color=INK, lw=2, zorder=9); ax.text(sx + 1000, sy + 120, "2 km", color=INK, fontsize=8, ha="center")
    # north arrow
    ax.annotate("N", xy=(X1 - 700, Y1 - 600), xytext=(X1 - 700, Y1 - 1600), color=INK, fontsize=11, ha="center",
                arrowprops=dict(arrowstyle="-|>", color=INK, lw=1.5), zorder=9)
    return fig, ax


def draw(ax, sel, colour, width=0.9, arrows=True, arrow_col=None, zorder=5):
    segs, cols, lws = [], [], []
    for c in sel:
        segs.append(c["xy"]); cols.append(colour(c)); lws.append(width(c) if callable(width) else width)
    ax.add_collection(LineCollection(segs, colors=cols, linewidths=lws, zorder=zorder, capstyle="round"))
    if arrows:
        ax.add_collection(flow_arrows(sel, arrow_col or colour))


def flow_arrows(sel, colour, min_len=60, size=45):
    """One arrowhead per conduit at its midpoint, pointing from_uid -> to_uid."""
    segs, cols = [], []
    for c in sel:
        P = c["xy"]; L = sum(math.hypot(P[i+1][0]-P[i][0], P[i+1][1]-P[i][1]) for i in range(len(P)-1))
        if L < min_len: continue
        half, acc = L/2, 0
        for i in range(len(P)-1):
            s = math.hypot(P[i+1][0]-P[i][0], P[i+1][1]-P[i][1])
            if acc + s >= half:
                t = (half-acc)/s if s else 0; mx, my = P[i][0]+(P[i+1][0]-P[i][0])*t, P[i][1]+(P[i+1][1]-P[i][1])*t
                ux, uy = (P[i+1][0]-P[i][0])/s, (P[i+1][1]-P[i][1])/s; break
            acc += s
        else: continue
        # a chevron: two short strokes behind the tip
        tip = (mx + ux*size*0.5, my + uy*size*0.5)
        for sgn in (1, -1):
            wing = (tip[0] - ux*size + sgn*(-uy)*size*0.55, tip[1] - uy*size + sgn*ux*size*0.55)
            segs.append([wing, tip]); cols.append(colour(c) if callable(colour) else colour)
    return LineCollection(segs, colors=cols, linewidths=0.9, zorder=7, capstyle="round")


def legend(ax, handles, title, loc="lower left"):
    lg = ax.legend(handles=handles, title=title, loc=loc, fontsize=8.5, title_fontsize=9.5, frameon=True,
                   facecolor="#111820", edgecolor="#2a3742", labelcolor=INK, borderpad=1, labelspacing=0.7, bbox_to_anchor=(0.005, 0.01))
    lg.get_title().set_color(INK)
    return lg


N_DRAWN = sum(1 for c in conds if isDrawn(c))
ARROW_NOTE = "Arrowheads show the direction of flow (from_uid → to_uid), one per conduit over 60 m"

# ---- 1. invert levels --------------------------------------------------------
inv = np.array([c["ui"] if c["ui"] is not None else (c["di"] if c["di"] is not None else np.nan) for c in conds])
lo, hi = np.nanpercentile(inv, 2), np.nanpercentile(inv, 98)
cmap = LinearSegmentedColormap.from_list("lvl", ["#0a2f6b", "#1665b0", "#0fa3b1", "#3fbf7f", "#b8cf3c", "#f0a02a", "#d9451f"])
def lvl_col(c):
    z = c["ui"] if c["ui"] is not None else c["di"]
    return "#465660" if z is None else cmap((z-lo)/(hi-lo))
fig, ax = base("Chennai storm water drains — invert levels and direction of flow",
               f"{len(net['nodes']):,} manholes · {len(conds):,} conduits (connected build) · colour = upstream invert level, m above mean sea level")
draw(ax, conds, lvl_col)
sm = plt.cm.ScalarMappable(cmap=cmap, norm=plt.Normalize(lo, hi)); cb = fig.colorbar(sm, ax=ax, fraction=0.018, pad=0.005, shrink=0.3, anchor=(1.0, 0.55))
cb.set_label("invert level, m MSL", color=INK); cb.ax.yaxis.set_tick_params(color=INK, labelcolor=INK); cb.outline.set_edgecolor("#2a3742")
legend(ax, [Line2D([], [], color="#465660", lw=2, label="no surveyed level"), Line2D([], [], color=INK, lw=0, marker=">", label=ARROW_NOTE),
            Line2D([], [], color="#ffb454", lw=1, ls="--", label="ward with a survey sheet"), Line2D([], [], color="#3a4750", lw=1, ls="--", label="ward without")], "Legend")
fig.savefig(f"{OUT}/swd_network_invert_levels.png", facecolor=BG); plt.close(fig)

# ---- 2. gradient / uphill --------------------------------------------------------
def grad_col(c):
    s = c["s"]
    if s is None: return "#465660"
    if s < 0: return "#ff3b4f"
    t = min(1, s/0.008); return LinearSegmentedColormap.from_list("g", ["#276b48", "#4fb277", "#a9ecbd"])(t)
fig, ax = base("Chennai storm water drains — surveyed gradient",
               "fall between surveyed inverts / run length · red runs fall UPHILL in the direction of flow: silted sumps or survey noise")
draw(ax, conds, grad_col)
legend(ax, [Line2D([], [], color="#ff3b4f", lw=2.5, label=f"falls uphill — {sum(1 for c in conds if c['s'] is not None and c['s']<0):,} conduits"),
            Line2D([], [], color="#276b48", lw=2.5, label="0 – 0.1 %"), Line2D([], [], color="#4fb277", lw=2.5, label="0.4 %"),
            Line2D([], [], color="#a9ecbd", lw=2.5, label="0.8 % or steeper"), Line2D([], [], color="#465660", lw=2.5, label="no surveyed inverts"),
            Line2D([], [], color=INK, lw=0, marker=">", label=ARROW_NOTE)], "Gradient")
fig.savefig(f"{OUT}/swd_network_gradient.png", facecolor=BG); plt.close(fig)

# ---- 3. provenance ---------------------------------------------------------
def prov_col(c):
    return "#25c8bd" if isDrawn(c) else "#e0b84a" if isRouted(c) else "#8f7bd6" if c["x"] else "#d05ae0"
fig, ax = base("Chennai storm water drains — what was drawn, what was inferred",
               f"{N_DRAWN:,} conduits traced off the sheets; the rest were added so that water can route")
draw(ax, sorted(conds, key=isDrawn), prov_col, arrows=False)
legend(ax, [Line2D([], [], color="#25c8bd", lw=2.5, label=f"traced from a sheet — {N_DRAWN:,}"),
            Line2D([], [], color="#e0b84a", lw=2.5, label=f"schedule link routed along OSM roads — {sum(1 for c in conds if isRouted(c)):,}"),
            Line2D([], [], color="#d05ae0", lw=2.5, label=f"schedule link, straight chord — {sum(1 for c in conds if not isDrawn(c) and not isRouted(c) and not c['x']):,}"),
            Line2D([], [], color="#8f7bd6", lw=2.5, label=f"cross-ward join — {sum(1 for c in conds if c['x']):,}")], "Provenance")
fig.savefig(f"{OUT}/swd_network_provenance.png", facecolor=BG); plt.close(fig)

# ---- 4. as drawn only, outfalls -------------------------------------------------
drawn = [c for c in conds if isDrawn(c)]
fig, ax = base("Chennai storm water drains — as drawn on the ward sheets",
               f"{len(drawn):,} conduits exactly as traced, nothing joined or inferred · outfalls as marked (OL) on the sheets")
draw(ax, drawn, lambda c: "#25c8bd", width=0.8)
ol = [n for n in net["nodes"] if n["out"] and not n["sy"]]
ax.scatter([n["xy"][0] for n in ol], [n["xy"][1] for n in ol], s=9, c="#ffb02e", edgecolors="none", zorder=8)
legend(ax, [Line2D([], [], color="#25c8bd", lw=2.5, label="drain as traced"), Line2D([], [], color="#ffb02e", lw=0, marker="o", markersize=6, label=f"outfall marked (OL) — {len(ol)}"),
            Line2D([], [], color=INK, lw=0, marker=">", label=ARROW_NOTE)], "As drawn")
fig.savefig(f"{OUT}/swd_network_as_drawn.png", facecolor=BG); plt.close(fig)

# ---- 5. section width -----------------------------------------------------------
def w_lw(c): return 0.4 if c["wd"] is None else 0.3 + min(c["wd"], 4.0) * 0.55
def w_col(c): return "#465660" if c["wd"] is None else LinearSegmentedColormap.from_list("w", ["#5b8fb9", "#9ad0ff", "#ffffff"])(min(c["wd"], 3.0)/3.0)
fig, ax = base("Chennai storm water drains — surveyed section width",
               "line weight and tint = drain width from the schedule (0.12 – 10.2 m) · grey = no surveyed section")
draw(ax, sorted(conds, key=lambda c: c["wd"] or 0), w_col, width=w_lw, arrows=False)
legend(ax, [Line2D([], [], color="#5b8fb9", lw=0.6, label="0.5 m wide"), Line2D([], [], color="#9ad0ff", lw=1.2, label="1.5 m"),
            Line2D([], [], color="#ffffff", lw=2.0, label="3 m and wider"), Line2D([], [], color="#465660", lw=0.6, label="not surveyed")], "Width")
fig.savefig(f"{OUT}/swd_network_section_width.png", facecolor=BG); plt.close(fig)

# ---- 6. 2015 flood record ------------------------------------------------------
fig, ax = base("Chennai storm water drains — manholes against the December 2015 flooded-street record",
               "distance from each surveyed manhole to the nearest street recorded as flooded in 2015 (osm-in/flood-map)")
draw(ax, conds, lambda c: "#2a3a44", width=0.6, arrows=False, zorder=4)
def fl_col(n):
    return "#465660" if n["fl"] is None else "#ff3b4f" if n["fl"] < 15 else "#c4564f" if n["fl"] < 40 else "#7d5a52"
ax.scatter([n["xy"][0] for n in net["nodes"]], [n["xy"][1] for n in net["nodes"]], s=3.5, c=[fl_col(n) for n in net["nodes"]], edgecolors="none", zorder=8)
legend(ax, [Line2D([], [], color="#ff3b4f", lw=0, marker="o", markersize=6, label="manhole on a street recorded as flooded"),
            Line2D([], [], color="#c4564f", lw=0, marker="o", markersize=6, label="within 40 m"), Line2D([], [], color="#7d5a52", lw=0, marker="o", markersize=6, label="within 200 m"),
            Line2D([], [], color="#465660", lw=0, marker="o", markersize=6, label="no flooded street within 200 m"), Line2D([], [], color="#2a3a44", lw=2, label="drain network")], "2015 flood record")
fig.savefig(f"{OUT}/swd_network_flood_record_2015.png", facecolor=BG); plt.close(fig)
print("maps written:", sorted(os.listdir(OUT)))
