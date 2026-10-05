#!/usr/bin/env python3
"""Calibrate the US train types against the FTA reference pass-by levels.

FTA Transit Noise and Vibration Impact Assessment Manual (2018), reference SEL at 50 ft (15.24 m) for a 50 mph
pass-by: diesel-electric locomotive 92 dBA per locomotive, passenger rail car 82 dBA per car. Each train type
runs on its own straight 3 km track over flat ground (one train per hour at 50 mph, day only); receivers at 50 ft,
1.5 m high, mid-track. SEL = LAeq,1h + 10 log10(3600 s).

  calibrate.py [--traction EU7] [--ground 0.5] [--port 9140]
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import rail_vehicles  # noqa: E402

PROJECT = HERE.parents[4]
ROOT = PROJECT / "implementation/work/rail/attempts"
MPH50 = 80.4672
FT50 = 15.24
CASES = [("CAL_LOCO", "locomotive", 92.0), ("CAL_LOCO_X2", "locomotive as 2 CNOSSOS units", 92.0),
         ("CAL_FRT_LOCO", "freight locomotive (6 axles)", 92.0), ("CAL_FRT_LOCO_X2", "freight locomotive as 2 units", 92.0),
         ("CAL_LOCO_SNCF", "locomotive (French default traction)", 92.0),
         ("CAL_COACH", "passenger car", 82.0), ("CAL_FRT_CAR", "freight car", None), ("CAL_LRV", "light-rail vehicle", None)]
X0, Y0 = 500000.0, 3700000.0   # empty test area (UTM 11N)


def fc(features):
    return {"type": "FeatureCollection", "crs": {"type": "name", "properties": {"name": "EPSG:26911"}}, "features": features}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--traction", default=rail_vehicles.TRACTION)
    parser.add_argument("--ground", type=float, default=0.5)
    parser.add_argument("--port", type=int, default=9140)
    args = parser.parse_args()
    attempt = ROOT / f"calibration-{args.traction.lower()}-g{int(args.ground * 100):03d}-{time.strftime('%Y%m%d%H%M%S')}"
    inp = attempt / "input"
    inp.mkdir(parents=True)
    rail_vehicles.write(attempt / "data", args.traction)
    sections, receivers, traffic = [], [], []
    for i, (trainset, _, _) in enumerate(CASES, 1):
        y = Y0 + i * 3000
        sections.append({"type": "Feature", "geometry": {"type": "LineString", "coordinates": [[X0 - 1500, y], [X0 + 1500, y]]},
                         "properties": {"PK": i, "IDSECTION": i, "NTRACK": 1, "TRACKSPD": 160.0, "TRANSFER": "EU5", "ROUGHNESS": "EU4",
                                        "IMPACT": "", "CURVATURE": 0, "BRIDGE": "", "COMSPD": 160.0, "ISTUNNEL": 0, "TRACKSPC": 2.0}})
        receivers.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [X0, y + FT50, 1.5]}, "properties": {"PK": i}})
        traffic.append({"IDTRAFFIC": i, "IDSECTION": i, "TRAINTYPE": trainset, "TRAINSPD": MPH50, "TDAY": 1.0, "TEVENING": 0.0, "TNIGHT": 0.0})
    (inp / "rail_sections.geojson").write_text(json.dumps(fc(sections)))
    (inp / "receivers.geojson").write_text(json.dumps(fc(receivers)))
    b = X0 + 20000
    (inp / "buildings.geojson").write_text(json.dumps(fc([{"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [[[b, Y0], [b + 10, Y0], [b + 10, Y0 + 10], [b, Y0 + 10], [b, Y0]]]},
                                                          "properties": {"PK": 1, "HEIGHT": 5.0}}])))
    g = [[X0 - 30000, Y0 - 30000], [X0 + 30000, Y0 - 30000], [X0 + 30000, Y0 + 30000], [X0 - 30000, Y0 + 30000], [X0 - 30000, Y0 - 30000]]
    (inp / "ground.geojson").write_text(json.dumps(fc([{"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [g]}, "properties": {"PK": 1, "G": args.ground}}])))
    with (inp / "rail_traffic.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(traffic[0]))
        w.writeheader()
        w.writerows(traffic)
    (attempt / "rail_manifest.json").write_text(json.dumps({"table_prefix": "RAILCAL", "purpose": "FTA reference calibration"}))
    subprocess.run([sys.executable, str(HERE / "run_rail_attempt.py"), str(attempt), "--port", str(args.port)], check=True)
    levels = {}
    with (attempt / "export/receivers_level_rail.csv").open() as f:
        for r in csv.DictReader(f):
            if r["PERIOD"] == "D":
                levels[int(r["IDRECEIVER"])] = float(r["LAEQ"])
    print(f"\ntraction {args.traction}, ground G={args.ground}: SEL at 50 ft, 50 mph (FTA reference in brackets)")
    result = []
    for i, (trainset, what, ref) in enumerate(CASES, 1):
        sel = levels[i] + 10 * math.log10(3600)
        result.append({"trainset": trainset, "what": what, "sel_dba": round(sel, 1), "fta_reference_dba": ref})
        print(f"  {what:38} {sel:5.1f} dBA" + (f"  ({ref:.0f}; {sel - ref:+.1f})" if ref else ""))
    (attempt / "calibration.json").write_text(json.dumps({"traction": args.traction, "ground": args.ground, "results": result}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
