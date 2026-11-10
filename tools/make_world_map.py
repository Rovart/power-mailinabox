#!/usr/bin/env python3
# Usage: tools/make_world_map.py ne_110m_admin_0_countries.geojson conf/panel/world.js
# (the GeoJSON is in https://github.com/nvkelso/natural-earth-vector, geojson/, v5.1.2)
# Builds conf/panel/world.js from Natural Earth's 1:110m countries (public domain).
import json, sys
src, out = sys.argv[1], sys.argv[2]
W, TOP, BOTTOM = 1000.0, 84.0, -56.0
K = W / 360.0
H = (TOP - BOTTOM) * K
FIX = {"N. Cyprus": "CY", "Somaliland": "SO"}
def xy(lon, lat):
    return ((lon + 180) * K, (TOP - lat) * K)
def ring_path(ring):
    pts, last = [], None
    for lon, lat in ring:
        lat = max(min(lat, TOP), BOTTOM)
        p = xy(lon, lat)
        if last is None or abs(p[0] - last[0]) + abs(p[1] - last[1]) >= 0.9:
            pts.append(p); last = p
    if len(pts) < 3:
        return ""
    s = "M%.1f %.1f" % pts[0] + "".join("l%.1f %.1f" % (b[0] - a[0], b[1] - a[1]) for a, b in zip(pts, pts[1:]))
    return s.replace(".0 ", " ").replace(".0l", "l").replace("-0.0", "0").replace(" -0 ", " 0 ") + "z"
d = json.load(open(src))
paths = {}
for f in d["features"]:
    p = f["properties"]
    code = FIX.get(p["NAME"]) or p["ISO_A2_EH"]
    if code in ("-99", "AQ") or not code:
        continue
    g = f["geometry"]
    polys = g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]
    parts = [ring_path(r) for poly in polys for r in poly]
    paths.setdefault(code, []).extend(x for x in parts if x)
body = ",\n".join('\t"%s": "%s"' % (c, "".join(v)) for c, v in sorted(paths.items()) if v)
open(out, "w").write(
    "/*\n * World map for the dashboard's \"Where the attacks come from\" (PanelCharts.map).\n"
    " * Country outlines from Natural Earth's 1:110m Admin 0 countries (public domain,\n"
    " * https://www.naturalearthdata.com/), in an equirectangular projection from 84°N to 56°S\n"
    " * (no Antarctica), keyed by ISO 3166-1 alpha-2 code, simplified to whole pixels.\n */\n"
    "window.PanelWorld = {\n\twidth: %d,\n\theight: %d,\n\tcountries: {\n%s\n\t}\n};\n" % (W, round(H), body.replace("\n\t", "\n\t\t").replace('\t"', '\t\t"', 1)))
print(len(paths), "countries", round(H))
