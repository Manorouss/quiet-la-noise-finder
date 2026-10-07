#!/usr/bin/env python3
"""Cross-check the county model against the federal BTS National Transportation Noise Map (2020, 30 m, 24 h LAeq at
1.5 m, roads from interstates down to major collectors, no buildings, no local streets, blank below 45 dB).

Like for like: our open-ground points (1.5 m) with the freeway + arterial class contributions only (model v3 stores
per-class levels: df/da/.. per period), folded into a 24 h LAeq. Points are grouped by distance to the nearest
freeway (S1100) and by which class dominates, so the near-freeway bands compare the two road-noise models (BTS: FHWA
TNM with the US fleet; ours: CNOSSOS-EU) with little building shielding in play.

  compare_bts.py [--raster <bts tif>] [--max-tiles N]
"""
from __future__ import annotations

import argparse
import json
import math
import statistics as st
from pathlib import Path

import numpy as np
import rasterio
from pyproj import Transformer
from shapely.geometry import LineString
from shapely.strtree import STRtree

PROJECT = Path(__file__).resolve().parents[5]
WORK = PROJECT / "implementation/work"
ASSETS = WORK / "pipeline_release/county_all"
ATTEMPTS = WORK / "campaign/county_v1/attempts"
BANDS = [(0, 50), (50, 100), (100, 200), (200, 400), (400, 1000), (1000, 3000)]


def laeq24(d, e, n):
    return 10 * math.log10((12 * 10 ** (d / 10) + 3 * 10 ** (e / 10) + 9 * 10 ** (n / 10)) / 24)


def esum(*vals):
    vals = [v for v in vals if v is not None]
    return 10 * math.log10(sum(10 ** (v / 10) for v in vals)) if vals else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--raster", type=Path, default=WORK / "source_cache/bts_noise/bts_2020_all_la.tif",
                        help="BTS combined raster (rail + road + aviation)")
    parser.add_argument("--aviation", type=Path, default=WORK / "source_cache/bts_noise/bts_2020_a_la.tif",
                        help="BTS aviation raster: its energy is subtracted from the combined one, and points under aircraft noise are skipped")
    parser.add_argument("--max-tiles", type=int, default=0)
    parser.add_argument("--json", type=Path, default=None)
    args = parser.parse_args()
    src = rasterio.open(args.raster)
    avi = rasterio.open(args.aviation) if args.aviation and args.aviation.exists() else None
    to_albers = Transformer.from_crs("EPSG:4326", src.crs, always_xy=True)
    to_avi = Transformer.from_crs("EPSG:4326", avi.crs, always_xy=True) if avi else None
    to_utm = Transformer.from_crs("EPSG:4326", "EPSG:26911", always_xy=True)
    rows = []
    tiles = 0
    for tile_dir in sorted(ASSETS.glob("cty-e*-n*")):
        build = tile_dir / "build-manifest.json"
        if not build.exists():
            continue
        meta = json.loads(build.read_text())
        if meta.get("study_model") != "county-v3":
            continue
        attempt = ATTEMPTS / meta["attempt_id"]
        if not (attempt / "input/sources.geojson").exists():
            continue
        freeways = [LineString(f["geometry"]["coordinates"]) for f in json.loads((attempt / "input/sources.geojson").read_text())["features"]
                    if f["properties"].get("MTFCC") == "S1100"]
        tree = STRtree(freeways) if freeways else None
        points = json.loads((tile_dir / "benchmark.geojson").read_text())["features"]
        lons = [p["geometry"]["coordinates"][0] for p in points]
        lats = [p["geometry"]["coordinates"][1] for p in points]
        ax, ay = to_albers.transform(lons, lats)
        ux, uy = to_utm.transform(lons, lats)
        samples = [v[0] for v in src.sample(zip(ax, ay))]
        if avi:
            vx, vy = to_avi.transform(lons, lats)
            air = [v[0] for v in avi.sample(zip(vx, vy))]
        else:
            air = [None] * len(points)
        for p, bx, by, bts, x, y, a in zip(points, ax, ay, samples, ux, uy, air):
            if a is not None and np.isfinite(a) and a > 45.0:
                if bts is not None and np.isfinite(bts) and bts > a + 3:
                    bts = 10 * math.log10(10 ** (bts / 10) - 10 ** (a / 10))   # take the aircraft energy out
                else:
                    continue   # aircraft noise dominates here: no road comparison possible
            q = p["properties"]
            if q.get("masked") or q.get("receiver_family") != "open_space_metric_lattice" or q.get("df") is None and q.get("da") is None:
                continue
            if bts is None or not np.isfinite(bts) or bts <= 45.0:   # BTS publishes nothing below 45 dB (blank cells come back as 45.0)
                bts = None
            d = esum(q.get("df"), q.get("da")); e = esum(q.get("ef"), q.get("ea")); n = esum(q.get("nf"), q.get("na"))
            if d is None or e is None or n is None:
                continue
            ours = laeq24(d, e, n)
            dist = math.inf
            if tree is not None:
                from shapely.geometry import Point
                pt = Point(x, y)
                i = tree.nearest(pt)
                dist = freeways[int(i)].distance(pt)
            dominant = "freeway" if (q.get("df") is not None and (q.get("da") is None or q["df"] >= q["da"] + 3)) else "arterial"
            rows.append({"tile": tile_dir.name, "ours": ours, "bts": None if bts is None else float(bts), "dist": dist, "dominant": dominant, "local": q.get("dl")})
        tiles += 1
        if args.max_tiles and tiles >= args.max_tiles:
            break
    print(f"{tiles} v3 tiles, {len(rows)} open-ground points; BTS blank (<45 dB) at {sum(r['bts'] is None for r in rows)}")
    out = {"tiles": tiles, "points": len(rows), "bands": []}
    for dom in ("freeway", "arterial"):
        print(f"\n{dom}-dominated points (ours = freeway + arterial classes only, 24 h LAeq at 1.5 m)")
        print(f"{'distance to freeway':>22} {'n':>6} {'ours med':>9} {'BTS med':>8} {'diff med':>9} {'diff mean':>10} {'blank':>6}")
        for lo, hi in BANDS:
            sel = [r for r in rows if r["dominant"] == dom and lo <= r["dist"] < hi]
            both = [r for r in sel if r["bts"] is not None]
            if len(sel) < 20:
                continue
            diffs = [r["ours"] - r["bts"] for r in both]
            line = {"dominant": dom, "band_m": [lo, hi], "n": len(sel), "n_both": len(both),
                    "ours_median": round(st.median(r["ours"] for r in sel), 1),
                    "bts_median": round(st.median(r["bts"] for r in both), 1) if both else None,
                    "diff_median": round(st.median(diffs), 1) if diffs else None, "diff_mean": round(st.mean(diffs), 1) if diffs else None,
                    "bts_blank_share": round(1 - len(both) / len(sel), 2)}
            out["bands"].append(line)
            print(f"{lo:>8}-{hi:<13} {line['n']:>6} {line['ours_median']:>9} {str(line['bts_median']):>8} {str(line['diff_median']):>9} {str(line['diff_mean']):>10} {line['bts_blank_share']:>6.0%}")
    if args.json:
        args.json.write_text(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
