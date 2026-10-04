#!/usr/bin/env python3
"""Flag receiver levels that exceed a physical ceiling for road traffic noise.

The ceiling is the level every road source would produce with nothing in the
way: incoherent line sources radiating into a half-space (perfect ground
reflection), no air absorption, 2D distance (never longer than the true 3D
path), and every source counted regardless of the engine's distance cutoff.
Emissions come from the pinned NoiseModelling CNOSSOS-EU jar, evaluated at the
louder of two temperatures.

CNOSSOS-EU ground and meteorological terms can push a valid path a few dB
above that half-space level in favourable conditions, so a result is flagged
only when it exceeds the ceiling by more than --margin-db (default 6 dB).
Diffraction never adds energy, so anything beyond that margin is not a
plausible propagation result.

Usage:
  physical_ceiling.py --periods periods.geojson --receivers receivers.geojson \
      --values export.csv|release.geojson [--period D] [--out report.json]
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import subprocess
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
NM_LIB = Path("/Volumes/NoiseModelling/NoiseModelling.app/Contents/app/lib")
A_WEIGHTS = np.array([-26.2, -16.1, -8.6, -3.2, 0.0, 1.2, 1.0, -1.1])
TRAFFIC_FIELDS = ("LV", "MV", "HGV", "WAV", "WBV", "LV_SPD", "MV_SPD", "HGV_SPD", "WAV_SPD", "WBV_SPD")


def emissions(rows: list[tuple[str, dict]], temperature: float) -> dict[str, np.ndarray]:
    jars = ":".join(str(p) for p in NM_LIB.glob("*.jar") if p.name.startswith(("noisemodelling-emission", "jackson-")))
    lines = []
    for key, p in rows:
        values = [str(float(p.get(f) or 0.0)) for f in TRAFFIC_FIELDS]
        lines.append("\t".join([key, *values, str(p.get("PVMT") or "NL08"), str(float(p.get("TS_STUD") or 0)),
                                str(float(p.get("PM_STUD") or 0)), str(float(p.get("JUNC_DIST") or 250)),
                                str(int(p.get("JUNC_TYPE") or 0)), str(int(p.get("WAY") or 1)), str(temperature)]))
    result = subprocess.run(["java", "-cp", jars, str(HERE / "EmissionDump.java")], input="\n".join(lines),
                            capture_output=True, text=True, check=True)
    out = {}
    for line in result.stdout.splitlines():
        parts = line.split("\t")
        out[parts[0]] = np.array([float(x) for x in parts[1:]])
    return out


def load_sources(path: Path, period: str):
    features = [f for f in json.loads(path.read_text())["features"] if f["properties"].get("PERIOD") == period]
    rows = [(str(f["properties"]["IDSOURCE"]), f["properties"]) for f in features]
    warm, cold = emissions(rows, 20.0), emissions(rows, 15.0)
    segments, powers = [], []
    for f in features:
        key = str(f["properties"]["IDSOURCE"])
        lw = np.maximum(warm[key], cold[key])
        power = float(np.sum(10 ** ((lw + A_WEIGHTS) / 10)))
        geom = f["geometry"]
        lines = [geom["coordinates"]] if geom["type"] == "LineString" else geom["coordinates"]
        for line in lines:
            for a, b in zip(line, line[1:]):
                segments.append((a[0], a[1], b[0], b[1]))
                powers.append(power)
    return np.array(segments), np.array(powers)


def load_receivers(path: Path):
    ids, xy = [], []
    for f in json.loads(path.read_text())["features"]:
        ids.append(int(f["properties"]["PK"]))
        xy.append(f["geometry"]["coordinates"][:2])
    return np.array(ids), np.array(xy, dtype=float)


def load_values(path: Path, period: str) -> dict[int, float]:
    if path.suffix == ".csv":
        with path.open() as stream:
            return {int(r["IDRECEIVER"]): float(r["LAEQ"]) for r in csv.DictReader(stream) if r["PERIOD"] == period}
    values = {}
    for f in json.loads(path.read_text())["features"]:
        p = f["properties"]
        level = (p.get(period) or {}).get("laeq") if not p.get("masked") else None
        if level is not None:
            values[int(p["id"])] = float(level)
    return values


def ceiling_levels(xy: np.ndarray, segments: np.ndarray, powers: np.ndarray) -> np.ndarray:
    intensity = np.zeros(len(xy))
    px, py = xy[:, 0], xy[:, 1]
    for (ax, ay, bx, by), power in zip(segments, powers):
        length = math.hypot(bx - ax, by - ay)
        if length == 0:
            continue
        ux, uy = (bx - ax) / length, (by - ay) / length
        sa = (ax - px) * ux + (ay - py) * uy
        h = np.maximum(np.abs((ax - px) * uy - (ay - py) * ux), 0.1)
        intensity += power * (np.arctan((sa + length) / h) - np.arctan(sa / h)) / h
    return 10 * np.log10(intensity / (2 * math.pi))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--periods", type=Path, required=True)
    parser.add_argument("--receivers", type=Path, required=True)
    parser.add_argument("--values", type=Path, required=True)
    parser.add_argument("--period", default="D")
    parser.add_argument("--margin-db", type=float, default=6.0)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()

    segments, powers = load_sources(args.periods, args.period)
    ids, xy = load_receivers(args.receivers)
    values = load_values(args.values, args.period)
    ceiling = ceiling_levels(xy, segments, powers)
    rows = [(int(i), values[int(i)], float(c)) for i, c in zip(ids, ceiling) if int(i) in values]
    excess = np.array([v - c for _, v, c in rows])
    flagged = sorted(({"id": i, "laeq": round(v, 2), "ceiling": round(c, 2), "excess_db": round(v - c, 2)}
                      for i, v, c in rows if v - c > args.margin_db), key=lambda r: -r["excess_db"])
    report = {
        "period": args.period,
        "margin_db": args.margin_db,
        "receivers_checked": len(rows),
        "sources_segments": int(len(segments)),
        "excess_over_ceiling_db_percentiles": {str(q): round(float(np.percentile(excess, q)), 2) for q in (50, 90, 99, 100)},
        "flagged_count": len(flagged),
        "flagged": flagged,
    }
    text = json.dumps(report, indent=1)
    if args.out:
        args.out.write_text(text + "\n")
    print(text if len(flagged) < 40 else json.dumps({k: v for k, v in report.items() if k != "flagged"} | {"flagged_head": flagged[:20]}, indent=1))
    return 1 if flagged else 0


if __name__ == "__main__":
    sys.exit(main())
