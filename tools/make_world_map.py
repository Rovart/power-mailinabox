#!/usr/bin/env python3
# Usage: tools/make_world_map.py ne_110m_admin_0_countries.geojson conf/panel/world.js
# (the GeoJSON is in https://github.com/nvkelso/natural-earth-vector, geojson/, v5.1.2)
#
# Builds the dashboard's world map from Natural Earth's 1:110m countries (public domain):
# the Equal Earth projection (areas to scale, a natural 2:1 shape), without Antarctica,
# cropped to the land so the map is centered, keyed by ISO 3166-1 alpha-2 code, with
# points closer than a pixel dropped.
import json, math, sys
src, out = sys.argv[1], sys.argv[2]
FIX = {"N. Cyprus": "CY", "Somaliland": "SO"}
WIDTH = 1000.0

A1, A2, A3, A4 = 1.340264, -0.081106, 0.000893, 0.003796
M = math.sqrt(3) / 2

def equal_earth(lon, lat):
    t = math.asin(M * math.sin(math.radians(lat)))
    t2 = t * t
    t6 = t2 * t2 * t2
    x = math.radians(lon) * math.cos(t) / (M * (A1 + 3 * A2 * t2 + t6 * (7 * A3 + 9 * A4 * t2)))
    y = t * (A1 + A2 * t2 + t6 * (A3 + A4 * t2))
    return x, -y

d = json.load(open(src))
rings = {}
for f in d["features"]:
    p = f["properties"]
    code = FIX.get(p["NAME"]) or p["ISO_A2_EH"]
    if code in ("-99", "AQ") or not code:
        continue
    g = f["geometry"]
    polys = g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]
    for poly in polys:
        for ring in poly:
            rings.setdefault(code, []).append([equal_earth(lon, lat) for lon, lat in ring])

xs = [x for rs in rings.values() for r in rs for x, _ in r]
ys = [y for rs in rings.values() for r in rs for _, y in r]
x0, y0 = min(xs), min(ys)
scale = WIDTH / (max(xs) - x0)
height = (max(ys) - y0) * scale

def path(ring):
    pts, last = [], None
    for x, y in ring:
        q = ((x - x0) * scale, (y - y0) * scale)
        if last is None or abs(q[0] - last[0]) + abs(q[1] - last[1]) >= 0.9:
            pts.append(q)
            last = q
    if len(pts) < 3:
        return ""
    def num(v):
        s = "%.1f" % v
        s = s[:-2] if s.endswith(".0") else s
        return "0" if s == "-0" else s
    return "M%s %s" % (num(pts[0][0]), num(pts[0][1])) + "".join(
        "l%s %s" % (num(b[0] - a[0]), num(b[1] - a[1])) for a, b in zip(pts, pts[1:])) + "z"

paths = {c: "".join(path(r) for r in rs) for c, rs in sorted(rings.items())}
body = ",\n".join('\t\t"%s": "%s"' % (c, v) for c, v in paths.items() if v)
open(out, "w").write(
    "/*\n * World map for the dashboard's \"Where the attacks come from\" (PanelCharts.map).\n"
    " * Country outlines from Natural Earth's 1:110m Admin 0 countries (public domain,\n"
    " * https://www.naturalearthdata.com/), in the Equal Earth projection, without Antarctica,\n"
    " * keyed by ISO 3166-1 alpha-2 code. Made with tools/make_world_map.py.\n */\n"
    "window.PanelWorld = {\n\twidth: %d,\n\theight: %d,\n\tcountries: {\n%s\n\t}\n};\n" % (WIDTH, math.ceil(height), body))
print(len(paths), "countries", WIDTH, "x", math.ceil(height))
