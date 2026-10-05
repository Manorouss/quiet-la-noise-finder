#!/usr/bin/env python3
"""Compare the modeled map with measured noise at public permanent monitors (science/qa/data/noise_monitors.json).

For each monitor: the modeled aircraft CNEL (aircraft.py, the 24 h map's aircraft term) at the estimated position
and its range within the position uncertainty, the modeled road CNEL at the nearest open-ground receivers of the
released tile (when the tile is computed), and the total. Prints a table and the aircraft error statistics, and
writes the same as JSON.

  geo-python science/qa/validate_monitors.py [--year 2025] [--json out.json]
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "pipeline"))
from aircraft import aircraft_cnel, energy_sum, load_airports, road_cnel  # noqa: E402
from build_county_layers import AIRPORT_CONTOURS, TO_UTM, laeq  # noqa: E402

PROJECT = HERE.parents[4]
ASSETS = PROJECT / "implementation/work/pipeline_release/county_all"
NEAREST = 3          # open-ground receivers averaged for the road term
ROAD_RADIUS_M = 60   # ... within this distance of the monitor


def ring(x: float, y: float, r: float, n: int = 16) -> tuple[np.ndarray, np.ndarray]:
    a = np.linspace(0, 2 * math.pi, n, endpoint=False)
    return np.concatenate([[x], x + r * np.cos(a)]), np.concatenate([[y], y + r * np.sin(a)])


def road_at(x: float, y: float) -> tuple[float | None, float | None]:
    """Energy mean of road CNEL at the nearest open-ground receivers, and their mean distance (m)."""
    tile = ASSETS / f"cty-e{int(x // 1000)}-n{int(y // 1000)}" / "benchmark.geojson"
    if not tile.exists():
        return None, None
    found = []
    for f in json.loads(tile.read_text())["features"]:
        p = f["properties"]
        if p.get("receiver_family") == "building_facade_exterior":
            continue
        values = [laeq(p, period) for period in "DEN"]
        if any(v is None for v in values):
            continue
        rx, ry = TO_UTM.transform(*f["geometry"]["coordinates"][:2])
        d = math.hypot(rx - x, ry - y)
        if d <= ROAD_RADIUS_M:
            found.append((d, road_cnel(*values)))
    if not found:
        return None, None
    found.sort()
    near = found[:NEAREST]
    return 10 * math.log10(sum(10 ** (c / 10) for _, c in near) / len(near)), sum(d for d, _ in near) / len(near)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--sites", type=Path, default=HERE / "data/noise_monitors.json")
    parser.add_argument("--year", default="2025")
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()
    sites = json.loads(args.sites.read_text())["sites"]
    airports = load_airports(AIRPORT_CONTOURS)
    rows = []
    for s in sites:
        x, y = TO_UTM.transform(s["lon"], s["lat"])
        xs, ys = ring(x, y, s.get("position_uncertainty_m", 25))
        values = aircraft_cnel(airports, xs, ys)
        road, road_d = road_at(x, y)
        air = float(values[0]) if math.isfinite(values[0]) else None
        rows.append({
            "id": s["id"], "airport": s["airport"], "measured_aircraft": s["measured"].get(args.year),
            "model_aircraft": None if air is None else round(air, 1),
            "model_aircraft_range": [round(float(np.nanmin(values)), 1), round(float(np.nanmax(values)), 1)] if np.isfinite(values).any() else None,
            "model_road": None if road is None else round(road, 1), "road_receivers_mean_m": None if road_d is None else round(road_d),
            "model_total": None if air is None and road is None else round(energy_sum(air if air is not None else -99, road if road is not None else -99), 1),
        })
    errors = [r["model_aircraft"] - r["measured_aircraft"] for r in rows if r["model_aircraft"] is not None and r["measured_aircraft"] is not None]
    print(f"{'site':7} {'measured':>8} {'model':>6} {'range':>12} {'error':>6}   {'road':>5} {'total':>6}")
    for r in rows:
        err = "" if r["model_aircraft"] is None or r["measured_aircraft"] is None else f"{r['model_aircraft'] - r['measured_aircraft']:+.1f}"
        rng = "" if not r["model_aircraft_range"] else f"{r['model_aircraft_range'][0]:.1f}-{r['model_aircraft_range'][1]:.1f}"
        fmt = lambda v: "—" if v is None else f"{v:.1f}"
        print(f"{r['id']:7} {fmt(r['measured_aircraft']):>8} {fmt(r['model_aircraft']):>6} {rng:>12} {err:>6}   {fmt(r['model_road']):>5} {fmt(r['model_total']):>6}")
    summary = {}
    if errors:
        e = np.array(errors)
        summary = {"n": len(e), "mean_error_db": round(float(e.mean()), 1), "rms_error_db": round(float(np.sqrt((e ** 2).mean())), 1),
                   "max_abs_error_db": round(float(np.abs(e).max()), 1)}
        print(f"\naircraft CNEL, model - measured ({args.year}): mean {summary['mean_error_db']:+.1f} dB, RMS {summary['rms_error_db']:.1f} dB, "
              f"max |error| {summary['max_abs_error_db']:.1f} dB over {summary['n']} monitors")
    if args.json:
        args.json.write_text(json.dumps({"year": args.year, "summary": summary, "sites": rows}, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
