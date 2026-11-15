"""Export map data for the video (READ-ONLY): county outlines from the twin API, East Finland demo hospitals and the
eight story patients from backend/ops/world.py, plus colonoscopy hospitals from /twin/network.

    .venv/bin/python video/scripts/export_map.py            # API at http://localhost:8000 (env API=...)

Writes video/src/data/map.json in a projected pixel space (equirectangular, x scaled by cos(64°)), 1000 px = full Finland height.
"""
from __future__ import annotations

import json
import math
import os
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from backend.ops.world import FAC_COORD, FAC_SHORT, PERSONAS  # noqa: E402

API = os.environ.get("API", "http://localhost:8000")
OUT = ROOT / "video" / "src" / "data" / "map.json"
LAT0 = 64.0
K = math.cos(math.radians(LAT0))
# Finland bounding box
LON_MIN, LON_MAX, LAT_MIN, LAT_MAX = 19.0, 31.8, 59.6, 70.2
SCALE = 1000 / (LAT_MAX - LAT_MIN)


def proj(lon, lat):
    return round((lon - LON_MIN) * K * SCALE, 1), round((LAT_MAX - lat) * SCALE, 1)


def simplify(pts, tol):
    """Douglas-Peucker on projected points."""
    if len(pts) < 4:
        return pts
    (x1, y1), (x2, y2) = pts[0], pts[-1]
    dx, dy = x2 - x1, y2 - y1
    n = math.hypot(dx, dy) or 1e-9
    i, dmax = 0, 0.0
    for k in range(1, len(pts) - 1):
        d = abs(dy * pts[k][0] - dx * pts[k][1] + x2 * y1 - y2 * x1) / n
        if d > dmax:
            i, dmax = k, d
    if dmax <= tol:
        return [pts[0], pts[-1]]
    return simplify(pts[: i + 1], tol)[:-1] + simplify(pts[i:], tol)


def get(path):
    with urllib.request.urlopen(API + path, timeout=60) as r:
        return json.load(r)


def main():
    gj = get("/twin/geo/counties")
    counties = []
    for f in gj["features"]:
        g = f["geometry"]
        polys = g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]
        d = []
        for poly in polys:
            ring = [proj(x, y) for x, y in poly[0]]
            mid = len(ring) // 2              # closed ring: simplify two open halves (DP collapses first==last)
            ring = simplify(ring[: mid + 1], 0.35)[:-1] + simplify(ring[mid:], 0.35)
            if len(ring) < 4:
                continue
            d.append("M" + " L".join(f"{x},{y}" for x, y in ring) + "Z")
        counties.append({"id": f["properties"]["id"], "name": f["properties"]["name"].replace(" wellbeing services county", ""), "d": " ".join(d)})
    net = get("/twin/network")
    hosp = []
    for h in net.get("facilities", net.get("hospitals", [])):
        lon, lat = h.get("lon"), h.get("lat")
        if lon is None or lat is None:
            continue
        x, y = proj(lon, lat)
        hosp.append({"id": h.get("id"), "name": h.get("name"), "x": x, "y": y})
    demo = [{"id": fid, "name": FAC_SHORT[fid], "x": proj(*c)[0], "y": proj(*c)[1]} for fid, c in FAC_COORD.items()]
    pats = [{"name": p["name"], "muni": p["muni"], "priority": p["priority"], "emma": p["pid"] == "OPS-EMMA",
             "x": proj(*p["lonlat"])[0], "y": proj(*p["lonlat"])[1]} for p in PERSONAS]
    w, h = proj(LON_MAX, LAT_MIN)
    OUT.write_text(json.dumps({"width": w, "height": h, "counties": counties, "hospitals": hosp, "demo_hospitals": demo, "patients": pats,
                               "source": f"{API}/twin/geo/counties, /twin/network; backend/ops/world.py"}, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}: {len(counties)} counties, {len(hosp)} network hospitals, {len(demo)} demo hospitals, "
          f"{len(pats)} patients, {OUT.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
