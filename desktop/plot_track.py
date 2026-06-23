#!/usr/bin/env python3
"""Plot a georeferenced MARWIS run on an OpenStreetMap base map.

Reads the wide, georeferenced CSV produced by merge_gps.py and writes a
self-contained interactive HTML map: the GPS track as coloured points over OSM
tiles (Leaflet), coloured by a chosen measurement, with a hover/click popup of
the readings and a legend. Stdlib only — no folium/matplotlib; Leaflet + tiles
load from CDN/OSM, so viewing needs an internet connection.

Usage:
    python plot_track.py --geo data/marwis_..._geo.csv
    python plot_track.py --geo run_geo.csv --color friction --out map.html

    --color COL   column to colour by (default road_surface_temp). A numeric
                  column gets a cold->hot gradient; road_condition is categorical.
    --out PATH    output HTML (default: <geo>_map.html next to the input)
"""

import argparse
import csv
import json
import math
import os
import sys

# Popup fields: CSV column -> short label (only those present are shown).
POPUP_FIELDS = [
    ("road_surface_temp", "Road"), ("ambient_temp", "Air"),
    ("dew_point_temp", "Dew"), ("rel_humidity_at_road_temp", "RH"),
    ("friction", "Friction"), ("water_film_height", "Water film"),
    ("speed_mps", "Speed"),
]

# Road-condition code -> (label, colour); mirrors the manual's channel-900 codes.
CONDITION = {
    0: ("dry", "#2e7d32"), 1: ("damp", "#827717"), 2: ("wet", "#1565c0"),
    3: ("ice", "#00838f"), 4: ("snow/ice", "#00838f"), 5: ("chemically wet", "#6a1b9a"),
    6: ("water+ice", "#1565c0"), 8: ("snow", "#455a64"), 99: ("undef", "#9e9e9e"),
}

# Cold -> hot gradient stops for numeric colouring (blue->cyan->green->yellow->red).
GRADIENT = [(0.0, (49, 54, 149)), (0.25, (69, 174, 209)), (0.5, (171, 221, 164)),
            (0.75, (253, 174, 97)), (1.0, (215, 48, 39))]


def _num(s):
    s = (s or "").strip()
    if not s or s.lower() == "nan":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def gradient_color(frac):
    """Interpolate the GRADIENT stops at frac in [0,1] -> '#rrggbb'."""
    frac = max(0.0, min(1.0, frac))
    for (f0, c0), (f1, c1) in zip(GRADIENT, GRADIENT[1:]):
        if frac <= f1:
            t = 0.0 if f1 == f0 else (frac - f0) / (f1 - f0)
            rgb = tuple(round(a + t * (b - a)) for a, b in zip(c0, c1))
            return "#%02x%02x%02x" % rgb
    return "#%02x%02x%02x" % GRADIENT[-1][1]


def main():
    ap = argparse.ArgumentParser(description="Plot a georeferenced MARWIS run on OpenStreetMap.")
    ap.add_argument("--geo", required=True, help="georeferenced CSV from merge_gps.py")
    ap.add_argument("--color", default="road_surface_temp", help="column to colour by")
    ap.add_argument("--out", help="output HTML (default: <geo>_map.html)")
    args = ap.parse_args()

    out_path = args.out or os.path.splitext(args.geo)[0] + "_map.html"

    with open(args.geo, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        sys.exit("geo CSV has no rows")
    if args.color not in rows[0]:
        sys.exit(f"--color {args.color!r} not a column; available: {list(rows[0])}")

    pts = [r for r in rows if _num(r["lat"]) is not None and _num(r["lon"]) is not None]
    if not pts:
        sys.exit("no rows have lat/lon")

    categorical = args.color == "road_condition"
    cvals = [_num(r[args.color]) for r in pts]
    nums = [v for v in cvals if v is not None]
    lo, hi = (min(nums), max(nums)) if nums else (0.0, 1.0)
    span = (hi - lo) or 1.0

    data = []
    for r, cv in zip(pts, cvals):
        if cv is None:
            color = "#9e9e9e"
        elif categorical:
            color = CONDITION.get(int(cv), ("?", "#9e9e9e"))[1]
        else:
            color = gradient_color((cv - lo) / span)
        popup = {lbl: r[col] for col, lbl in POPUP_FIELDS if col in r and _num(r[col]) is not None}
        if categorical and cv is not None:
            popup["Condition"] = CONDITION.get(int(cv), ("?", ""))[0]
        data.append([round(_num(r["lat"]), 6), round(_num(r["lon"]), 6), color,
                     r["ts_utc"], popup])

    if categorical:
        present = sorted({int(v) for v in nums})
        legend = "".join(
            f'<div><span style="background:{CONDITION.get(c, ("?", "#9e9e9e"))[1]}"></span>'
            f'{CONDITION.get(c, ("?", ""))[0]}</div>' for c in present)
        legend_title = "Road condition"
    else:
        bar = ",".join(c for _, c in [(0, gradient_color(i / 6)) for i in range(7)])
        legend = (f'<div class="bar" style="background:linear-gradient(to right,{bar})"></div>'
                  f'<div class="ends"><span>{lo:.2f}</span><span>{hi:.2f}</span></div>')
        legend_title = args.color

    html = _TEMPLATE.format(
        title=os.path.basename(args.geo),
        color_col=args.color,
        n=len(data),
        data=json.dumps(data, separators=(",", ":")),
        legend=legend,
        legend_title=legend_title,
    )
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"wrote {out_path}")
    print(f"points: {len(data)}  coloured by: {args.color}"
          + (f"  range {lo:.2f}..{hi:.2f}" if not categorical else ""))


_TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>MARWIS track — {title}</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
  html,body{{margin:0;height:100%}} #map{{height:100%}}
  .legend{{background:#fff;padding:8px 10px;border-radius:6px;box-shadow:0 1px 5px rgba(0,0,0,.4);
    font:13px/1.4 system-ui,sans-serif}}
  .legend b{{display:block;margin-bottom:4px}}
  .legend .bar{{width:160px;height:12px;border-radius:3px}}
  .legend .ends{{display:flex;justify-content:space-between;font-size:11px}}
  .legend div span{{display:inline-block;width:12px;height:12px;border-radius:50%;
    margin-right:6px;vertical-align:middle}}
  .leaflet-popup-content{{font:13px/1.5 system-ui,sans-serif}}
  .leaflet-popup-content b{{font-size:12px;color:#555}}
</style></head><body><div id="map"></div><script>
const DATA={data};
const map=L.map('map');
L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png',{{
  maxZoom:19, attribution:'&copy; OpenStreetMap contributors'}}).addTo(map);
const latlngs=DATA.map(d=>[d[0],d[1]]);
L.polyline(latlngs,{{color:'#444',weight:2,opacity:.5}}).addTo(map);
for(const d of DATA){{
  const [lat,lon,color,ts,popup]=d;
  let html='<b>'+ts+'</b>';
  for(const k in popup) html+='<br>'+k+': '+popup[k];
  L.circleMarker([lat,lon],{{radius:5,color:'#222',weight:.5,
    fillColor:color,fillOpacity:.9}}).bindPopup(html).addTo(map);
}}
map.fitBounds(latlngs);
const lg=L.control({{position:'bottomright'}});
lg.onAdd=function(){{const d=L.DomUtil.create('div','legend');
  d.innerHTML='<b>{legend_title}</b>{legend}';return d;}};
lg.addTo(map);
</script></body></html>
"""


if __name__ == "__main__":
    main()
