#!/usr/bin/env python3
"""Compare two engine runs of the same tile receiver by receiver (model-upgrade benchmarks).

Matches receivers by RECEIVER_KEY (so runs built from different terrain or walls still pair up),
and reports LAeq differences (B - A, dB) per period: overall percentiles, by receiver family,
by distance to the nearest freeway (S1100) source, and the run times.

Usage:
  compare_runs.py <run A dir> <run B dir> [--json out.json]
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path

import numpy as np


def load(run: Path) -> dict[str, dict]:
    receivers = {int(f["properties"]["PK"]): f for f in json.loads((run / "input/receivers.geojson").read_text())["features"]}
    rows: dict[str, dict] = {}
    with (run / "export/receivers_level_d_e_n.csv").open() as handle:
        for row in csv.DictReader(handle):
            feature = receivers.get(int(row["IDRECEIVER"]))
            if feature is None:
                continue
            key = feature["properties"]["RECEIVER_KEY"].split(":", 1)[1]  # without the tile name, which reruns may change
            entry = rows.setdefault(key, {"family": feature["properties"]["RECEIVER_FAMILY"], "xy": feature["geometry"]["coordinates"][:2]})
            entry[row["PERIOD"]] = float(row["LAEQ"])
    return rows


def freeway_distance(run: Path, xy: np.ndarray) -> np.ndarray:
    segments = []
    for f in json.loads((run / "input/sources.geojson").read_text())["features"]:
        if f["properties"].get("MTFCC") == "S1100":
            c = f["geometry"]["coordinates"]
            segments += [(c[i][0], c[i][1], c[i + 1][0], c[i + 1][1]) for i in range(len(c) - 1)]
    if not segments:
        return np.full(len(xy), np.inf)
    s = np.array(segments)
    best = np.full(len(xy), np.inf)
    for start in range(0, len(s), 2000):
        seg = s[start:start + 2000]
        ax, ay, bx, by = seg[:, 0], seg[:, 1], seg[:, 2], seg[:, 3]
        dx, dy = bx - ax, by - ay
        length2 = np.maximum(dx * dx + dy * dy, 1e-9)
        t = np.clip(((xy[:, None, 0] - ax) * dx + (xy[:, None, 1] - ay) * dy) / length2, 0, 1)
        d = np.hypot(xy[:, None, 0] - (ax + t * dx), xy[:, None, 1] - (ay + t * dy))
        best = np.minimum(best, d.min(axis=1))
    return best


def stats(values: np.ndarray) -> dict:
    if len(values) == 0:
        return {"n": 0}
    p = np.percentile(values, [5, 50, 95])
    return {"n": int(len(values)), "mean": round(float(values.mean()), 2), "p5": round(float(p[0]), 2), "p50": round(float(p[1]), 2),
            "p95": round(float(p[2]), 2), "share_gt_1db": round(float((np.abs(values) > 1).mean()), 3), "share_gt_3db": round(float((np.abs(values) > 3).mean()), 3)}


def elapsed(run: Path) -> float | None:
    manifest = run / "phase1_run_manifest.json"
    return json.loads(manifest.read_text()).get("elapsed_seconds") if manifest.exists() else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("a", type=Path)
    parser.add_argument("b", type=Path)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()
    a, b = load(args.a), load(args.b)
    keys = sorted(set(a) & set(b))
    xy = np.array([a[k]["xy"] for k in keys])
    distance = freeway_distance(args.a, xy)
    report = {"a": args.a.name, "b": args.b.name, "matched": len(keys), "only_a": len(set(a) - set(b)), "only_b": len(set(b) - set(a)),
              "elapsed_s": {"a": elapsed(args.a), "b": elapsed(args.b)}, "periods": {}}
    for period in ("D", "N"):
        diff = np.array([b[k].get(period, math.nan) - a[k].get(period, math.nan) for k in keys])
        ok = np.isfinite(diff)
        family = np.array([a[k]["family"] for k in keys])
        report["periods"][period] = {
            "all": stats(diff[ok]),
            "facade": stats(diff[ok & (family == "building_facade_exterior")]),
            "open": stats(diff[ok & (family != "building_facade_exterior")]),
            "freeway_lt_100m": stats(diff[ok & (distance < 100)]),
            "freeway_100_300m": stats(diff[ok & (distance >= 100) & (distance < 300)]),
            "freeway_gt_300m": stats(diff[ok & (distance >= 300)]),
        }
    text = json.dumps(report, indent=1)
    print(text)
    if args.json:
        args.json.write_text(text + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
