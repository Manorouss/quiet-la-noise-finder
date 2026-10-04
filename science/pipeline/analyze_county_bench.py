#!/usr/bin/env python3
"""Throughput and receiver-layout error for the county benchmark tiles.

For each benchmark area run in both layouts (g10f4 dense, g20f10 light):
- throughput: receivers / propagation-second, per host and thread count;
- grid error: bilinear interpolation (in dB) of the 20 m grid at every 10 m
  grid point, compared with the computed 10 m value;
- facade error: per building, max and min D-period LAeq of its facade
  receivers in the light layout vs the dense layout (the portal's building
  summary).

Usage:
  analyze_county_bench.py [--out report.json]
"""
from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import numpy as np

ATTEMPTS = Path(__file__).resolve().parents[5] / "implementation/work/campaign/county_v1/attempts"


def run_dir(area: str, layout: str) -> Path | None:
    hits = sorted(ATTEMPTS.glob(f"phase1-county-{area}-{layout}-nv-*-v1"))
    hits = [h for h in hits if (h / "export/receivers_level_d_e_n.csv").exists()]
    return hits[0] if hits else None


def propagation_seconds(attempt: Path) -> float:
    events = [json.loads(line) for line in (attempt / "phase1_events.jsonl").open()]
    start = next(e for e in events if e.get("stage") == "propagation" and e["event"] == "stage_submitted")
    end = next(e for e in events if e.get("stage") == "propagation" and e["event"] == "stage_completed")
    return (datetime.fromisoformat(end["at_utc"]) - datetime.fromisoformat(start["at_utc"])).total_seconds()


def load(attempt: Path):
    receivers = {f["properties"]["PK"]: f for f in json.load(open(attempt / "input/receivers.geojson"))["features"]}
    levels = {}
    with (attempt / "export/receivers_level_d_e_n.csv").open() as stream:
        for row in csv.DictReader(stream):
            if row["PERIOD"] == "D":
                levels[int(row["IDRECEIVER"])] = float(row["LAEQ"])
    return receivers, levels


def grid_error(dense, light):
    (dr, dl), (lr, ll) = dense, light
    g20 = {(round(f["geometry"]["coordinates"][0], 1), round(f["geometry"]["coordinates"][1], 1)): ll[pk]
           for pk, f in lr.items() if f["properties"]["RECEIVER_FAMILY"] == "open_space_metric_lattice"}
    xs = sorted({k[0] for k in g20}); ys = sorted({k[1] for k in g20})
    errs = []
    for pk, f in dr.items():
        if f["properties"]["RECEIVER_FAMILY"] != "open_space_metric_lattice":
            continue
        x, y = f["geometry"]["coordinates"][:2]
        x0 = max((v for v in xs if v <= x), default=None); x1 = min((v for v in xs if v >= x), default=None)
        y0 = max((v for v in ys if v <= y), default=None); y1 = min((v for v in ys if v >= y), default=None)
        if None in (x0, x1, y0, y1):
            continue
        corners = [g20.get((x0, y0)), g20.get((x1, y0)), g20.get((x0, y1)), g20.get((x1, y1))]
        if any(c is None for c in corners):
            continue  # a corner fell inside a building: no interpolation support
        tx = 0 if x1 == x0 else (x - x0) / (x1 - x0)
        ty = 0 if y1 == y0 else (y - y0) / (y1 - y0)
        est = (corners[0] * (1 - tx) + corners[1] * tx) * (1 - ty) + (corners[2] * (1 - tx) + corners[3] * tx) * ty
        errs.append(est - dl[pk])
    e = np.abs(np.array(errs))
    return {"points": int(len(e)), "mae_db": float(e.mean()), "p90_db": float(np.percentile(e, 90)),
            "p99_db": float(np.percentile(e, 99)), "within_1db": float((e <= 1).mean()), "within_3db": float((e <= 3).mean())} if len(e) else None


def facade_error(dense, light):
    def summary(receivers, levels):
        by = defaultdict(list)
        for pk, f in receivers.items():
            if f["properties"]["RECEIVER_FAMILY"] == "building_facade_exterior":
                by[f["properties"]["SOURCE_BLD_ID"]].append(levels[pk])
        return {b: (max(v), min(v)) for b, v in by.items()}
    d, l = summary(*dense), summary(*light)
    common = sorted(set(d) & set(l))
    mx = np.abs(np.array([l[b][0] - d[b][0] for b in common]))
    mn = np.abs(np.array([l[b][1] - d[b][1] for b in common]))
    bias = np.array([l[b][0] - d[b][0] for b in common])
    return {"buildings": len(common), "max_mae_db": float(mx.mean()), "max_p90_db": float(np.percentile(mx, 90)),
            "max_bias_db": float(bias.mean()), "min_mae_db": float(mn.mean()), "min_p90_db": float(np.percentile(mn, 90)),
            "max_within_1db": float((mx <= 1).mean())} if common else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    report = {"throughput": [], "layout_error": {}}
    for area in ("tarzana-ventura", "koreatown", "cahuenga-pass"):
        runs = {}
        for layout in ("g10f4", "g20f10"):
            d = run_dir(area, layout)
            if d is None:
                continue
            host = json.load(open(d / "run_host.json"))
            n = json.load(open(d / "attempt_manifest.json"))["counts"]["receivers"]
            secs = propagation_seconds(d)
            report["throughput"].append({"area": area, "layout": layout, "host": host["host"], "threads": host["threads"],
                                         "receivers": n, "propagation_s": round(secs, 1), "receivers_per_s": round(n / secs, 1)})
            runs[layout] = load(d)
        if len(runs) == 2:
            report["layout_error"][area] = {"grid_20m_interpolated_vs_10m": grid_error(runs["g10f4"], runs["g20f10"]),
                                            "facade_10m_vs_4m_building_summary": facade_error(runs["g10f4"], runs["g20f10"])}
    text = json.dumps(report, indent=1)
    if args.out:
        args.out.write_text(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
