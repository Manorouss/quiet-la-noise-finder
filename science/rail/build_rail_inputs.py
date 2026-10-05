#!/usr/bin/env python3
"""Prepare a rail attempt for one tile from its road attempt (same receivers, buildings, ground, terrain).

Tracks: OpenStreetMap railway=rail ways with usage=main (no yards, sidings, spurs), each one track, cut into
~200 m sections. Speeds: TRACKSPD from OSM maxspeed; passenger trains slow down into the stations they stop at
(constant 0.5 m/s2 deceleration / acceleration, capped by the line speed); freight runs at a constant speed.
Traffic: average trains per hour per CNEL period (day 7-19, evening 19-22, night 22-7) for the line, split evenly
over its main tracks (GTFS weekly averages, gtfs_counts.py; freight from the FRA crossing inventory).

  build_rail_inputs.py --road-attempt <dir> --osm <overpass json> --config <line json> --out <attempt dir>
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import shutil
import sys
from pathlib import Path

import shapely
from pyproj import Transformer
from shapely.geometry import LineString, Point
from shapely.ops import substring

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rail_vehicles  # noqa: E402

TO_UTM = Transformer.from_crs("OGC:CRS84", "EPSG:26911", always_xy=True)
PIECE_M, ACCEL = 200.0, 0.5
MPH = 1.609344


def speed_kmh(text: str | None, default: float) -> float:
    if not text:
        return default
    value = float(text.split()[0])
    return value * MPH if "mph" in text else value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--road-attempt", type=Path, required=True)
    parser.add_argument("--osm", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    cfg = json.loads(args.config.read_text())
    out = args.out
    if out.exists():
        raise SystemExit(f"{out} exists; rail attempts are never overwritten")
    (out / "input").mkdir(parents=True)
    for name in ("buildings.geojson", "receivers.geojson", "ground.geojson", "terrain.asc"):
        shutil.copy2(args.road_attempt / "input" / name, out / "input" / name)
    rail_vehicles.write(out / "data")
    stations = [Point(TO_UTM.transform(lon, lat)) for lon, lat in cfg.get("stops", [])]
    ways = [w for w in json.loads(args.osm.read_text())["elements"] if w["tags"].get("usage") in cfg.get("usage", ["main"])]
    features, traffic, pk = [], [], 0
    for w in ways:
        line = LineString([TO_UTM.transform(p["lon"], p["lat"]) for p in w["geometry"]])
        track = speed_kmh(w["tags"].get("maxspeed"), cfg.get("default_track_kmh", 113.0))
        bridge = cfg.get("bridge_code", "EU3") if w["tags"].get("bridge") == "yes" else ""
        tunnel = 1 if w["tags"].get("tunnel") == "yes" else 0
        n = max(1, round(line.length / PIECE_M))
        for i in range(n):
            piece = substring(line, line.length * i / n, line.length * (i + 1) / n)
            if piece.length < 1:
                continue
            pk += 1
            mid = piece.interpolate(0.5, normalized=True)
            d = min((mid.distance(s) for s in stations), default=1e9)
            passenger = min(track, math.sqrt(2 * ACCEL * max(d, 30.0)) * 3.6)   # slowing into / out of a stop
            features.append({"type": "Feature", "geometry": {"type": "LineString", "coordinates": [list(c) for c in piece.coords]},
                             "properties": {"PK": pk, "IDSECTION": pk, "NTRACK": 1, "TRACKSPD": round(track, 1), "TRANSFER": cfg.get("transfer", "EU5"),
                                            "ROUGHNESS": cfg.get("roughness", "EU4"), "IMPACT": "", "CURVATURE": 0, "BRIDGE": bridge,
                                            "COMSPD": round(track, 1), "ISTUNNEL": tunnel, "TRACKSPC": 2.0, "OSM_WAY": w["id"]}})
            for service in cfg["services"]:
                share = 1.0 / cfg.get("main_tracks", 2)
                spd = passenger if service.get("stops", True) else min(track, service["speed_kmh"])
                traffic.append({"IDTRAFFIC": len(traffic) + 1, "IDSECTION": pk, "TRAINTYPE": service["trainset"], "TRAINSPD": round(spd, 1),
                                "TDAY": round(service["D"] * share, 4), "TEVENING": round(service["E"] * share, 4), "TNIGHT": round(service["N"] * share, 4)})
    (out / "input/rail_sections.geojson").write_text(json.dumps({"type": "FeatureCollection", "crs": {"type": "name", "properties": {"name": "EPSG:26911"}}, "features": features}))
    with (out / "input/rail_traffic.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(traffic[0]))
        writer.writeheader()
        writer.writerows(traffic)
    prefix = "RAIL_" + args.road_attempt.name.split("county-la-")[-1].split("-g")[0].upper().replace("-", "_")
    (out / "rail_manifest.json").write_text(json.dumps({"table_prefix": prefix, "road_attempt": args.road_attempt.name, "config": cfg,
                                                        "sections": len(features), "traffic_rows": len(traffic)}, indent=1))
    print(json.dumps({"out": str(out), "sections": len(features), "traffic_rows": len(traffic)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
