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
ROAD_RADIUS_M = 60   # ... within this distance of the site (at least)


def ring(x: float, y: float, r: float, n: int = 16) -> tuple[np.ndarray, np.ndarray]:
    a = np.linspace(0, 2 * math.pi, n, endpoint=False)
    return np.concatenate([[x], x + r * np.cos(a)]), np.concatenate([[y], y + r * np.sin(a)])


def open_ground(x: float, y: float, radius: float) -> list[tuple[float, list[float]]]:
    """(distance, [D, E, N] LAeq) of open-ground receivers of the released tiles within `radius` of (x, y)."""
    found = []
    for tx in {int((x - radius) // 1000), int((x + radius) // 1000)}:
        for ty in {int((y - radius) // 1000), int((y + radius) // 1000)}:
            tile = ASSETS / f"cty-e{tx}-n{ty}" / "benchmark.geojson"
            if not tile.exists():
                continue
            for f in json.loads(tile.read_text())["features"]:
                p = f["properties"]
                if p.get("receiver_family") == "building_facade_exterior" or p.get("on_road"):
                    continue  # (on-road points: a geocoded street address sits on the centerline, the microphone does not)
                values = [laeq(p, period) for period in "DEN"]
                if any(v is None for v in values):
                    continue
                rx, ry = TO_UTM.transform(*f["geometry"]["coordinates"][:2])
                d = math.hypot(rx - x, ry - y)
                if d <= radius:
                    found.append((d, values))
    return sorted(found)


def emean(values: list[float]) -> float:
    return 10 * math.log10(sum(10 ** (v / 10) for v in values) / len(values))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--sites", type=Path, default=HERE / "data/noise_monitors.json")
    parser.add_argument("--year", default="2025")
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()
    sites = json.loads(args.sites.read_text())["sites"]
    airports = load_airports(AIRPORT_CONTOURS)
    fmt = lambda v: "—" if v is None else f"{v:.1f}"
    air_rows, total_rows = [], []
    for s in sites:
        x, y = TO_UTM.transform(s["lon"], s["lat"])
        r_unc = s.get("position_uncertainty_m", 25)
        xs, ys = ring(x, y, r_unc)
        air = aircraft_cnel(airports, xs, ys)
        air0 = float(air[0]) if math.isfinite(air[0]) else None
        near = open_ground(x, y, max(r_unc, ROAD_RADIUS_M))
        road = None
        if near:
            top = near[:NEAREST]
            periods = [emean([v[i] for _, v in top]) for i in range(3)]
            road = {"D": round(periods[0], 1), "E": round(periods[1], 1), "N": round(periods[2], 1), "CNEL": round(road_cnel(*periods), 1),
                    "range_CNEL": [round(min(road_cnel(*v) for d, v in near if d <= r_unc), 1), round(max(road_cnel(*v) for d, v in near if d <= r_unc), 1)]
                    if any(d <= r_unc for d, _ in near) else None, "mean_distance_m": round(sum(d for d, _ in top) / len(top))}
        total = None if road is None else round(energy_sum(road["CNEL"], air0 if air0 is not None else -99), 1)
        if "CNEL" in s["measured"]:
            total_rows.append({"id": s["id"], "location": s["location"], "excluded": s.get("exclude"), "measured": s["measured"],
                               "model_road": road, "model_aircraft": None if air0 is None else round(air0, 1), "model_total_cnel": total})
        else:
            air_rows.append({"id": s["id"], "airport": s["airport"], "excluded": s.get("exclude"), "measured_aircraft": s["measured"].get(args.year),
                             "model_aircraft": None if air0 is None else round(air0, 1),
                             "model_aircraft_range": [round(float(np.nanmin(air)), 1), round(float(np.nanmax(air)), 1)] if np.isfinite(air).any() else None,
                             "model_road_cnel": None if road is None else road["CNEL"], "model_total_cnel": total})
    summary = {}
    print("AIRCRAFT MONITORS (aircraft CNEL)")
    print(f"{'site':7} {'measured':>8} {'model':>6} {'range':>12} {'error':>6}   {'road':>5} {'total':>6}")
    for r in air_rows:
        err = "" if r["model_aircraft"] is None or r["measured_aircraft"] is None else f"{r['model_aircraft'] - r['measured_aircraft']:+.1f}"
        rng = "" if not r["model_aircraft_range"] else f"{r['model_aircraft_range'][0]:.1f}-{r['model_aircraft_range'][1]:.1f}"
        print(f"{r['id']:7} {fmt(r['measured_aircraft']):>8} {fmt(r['model_aircraft']):>6} {rng:>12} {err:>6}   {fmt(r['model_road_cnel']):>5} {fmt(r['model_total_cnel']):>6}")
    used = [r for r in air_rows if not r["excluded"] and r["measured_aircraft"] is not None]
    e = np.array([r["model_aircraft"] - r["measured_aircraft"] for r in used if r["model_aircraft"] is not None])
    misses = [r["id"] for r in used if r["model_aircraft"] is None and r["measured_aircraft"] >= 57]
    if misses:
        print(f"  measured 57 dB or more but modeled below 55 dB (outside the estimated contours): {', '.join(misses)}")
    if len(e):
        summary["aircraft"] = {"n": len(e), "mean_error_db": round(float(e.mean()), 1), "rms_error_db": round(float(np.sqrt((e ** 2).mean())), 1), "missed": misses}
        for airport in sorted({r["airport"] for r in used}):
            ea = np.array([r["model_aircraft"] - r["measured_aircraft"] for r in used if r["airport"] == airport and r["model_aircraft"] is not None])
            if len(ea):
                summary.setdefault("by_airport", {})[airport] = {"n": len(ea), "mean_error_db": round(float(ea.mean()), 1), "rms_error_db": round(float(np.sqrt((ea ** 2).mean())), 1)}
                print(f"  {airport:26} n={len(ea):2}  mean {ea.mean():+.1f} dB  RMS {np.sqrt((ea ** 2).mean()):.1f} dB")
        print(f"  model - measured: mean {e.mean():+.1f} dB, RMS {np.sqrt((e ** 2).mean()):.1f} dB over {len(e)} monitors")
    print("\n24-HOUR MEASUREMENTS (total noise; model = roads + aircraft at the nearest open-ground points)")
    print(f"{'site':7} {'meas CNEL':>9} {'model':>6} {'range':>11} {'error':>6}  {'D meas/model':>13} {'N meas/model':>13}  note")
    for r in total_rows:
        if r["model_road"] is None:
            continue
        m, mod = r["measured"], r["model_road"]
        err = r["model_total_cnel"] - m["CNEL"]
        rng = "" if not mod["range_CNEL"] else f"{mod['range_CNEL'][0]:.0f}-{mod['range_CNEL'][1]:.0f}"
        dn = lambda k: f"{fmt(m.get(k))}/{fmt(mod[k])}"
        print(f"{r['id']:7} {m['CNEL']:9.1f} {r['model_total_cnel']:6.1f} {rng:>11} {err:+6.1f}  {dn('D'):>13} {dn('N'):>13}  {r['excluded'] or ''}")
    used = [r for r in total_rows if r["model_road"] is not None and not r["excluded"]]
    if used:
        e = np.array([r["model_total_cnel"] - r["measured"]["CNEL"] for r in used])
        inside = sum(1 for r in used if r["model_road"]["range_CNEL"] and r["model_road"]["range_CNEL"][0] - 1 <= r["measured"]["CNEL"] <= r["model_road"]["range_CNEL"][1] + 1)
        summary["total"] = {"n": len(e), "mean_error_db": round(float(e.mean()), 1), "rms_error_db": round(float(np.sqrt((e ** 2).mean())), 1),
                            "within_position_range_pm1db": inside, "not_computed_yet": sum(1 for r in total_rows if r["model_road"] is None)}
        print(f"  model - measured CNEL: mean {e.mean():+.1f} dB, RMS {np.sqrt((e ** 2).mean()):.1f} dB over {len(e)} sites "
              f"({inside} within the modeled range across the position uncertainty, ±1 dB); "
              f"{summary['total']['not_computed_yet']} sites not computed yet")
    if args.json:
        args.json.write_text(json.dumps({"year": args.year, "summary": summary, "aircraft_monitors": air_rows, "measurements": total_rows}, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
