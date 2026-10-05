#!/usr/bin/env python3
"""Prepare a rail attempt for one tile from its road attempt (same receivers, buildings, ground, terrain).

Tracks: OpenStreetMap railway=rail ways with usage=main (no yards, sidings, spurs), from the line config's
OSM extract, clipped to the receivers' extent plus the 1.5 km propagation distance and cut into ~200 m
sections. Each way is one track; parallel main tracks (within 8 m) share the line's traffic evenly. Each way
belongs to the first class of the config that matches (name, centroid bounds), which lists its services.
Speeds: TRACKSPD from OSM maxspeed; a passenger service slows into each station it stops at (0.5 m/s2,
capped by the line speed) and keeps the line speed elsewhere; freight runs at its constant speed.
Traffic: average trains per hour per CNEL period (day 7-19, evening 19-22, night 22-7).

  build_rail_inputs.py --road-attempt <dir> --config lines/sfv.json --out <attempt dir>
Exit status 3 when no track is within range of the tile (nothing to compute).
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
from shapely.geometry import LineString, Point, box
from shapely.ops import substring

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rail_vehicles  # noqa: E402

PROJECT = Path(__file__).resolve().parents[5]
TO_UTM = Transformer.from_crs("OGC:CRS84", "EPSG:26911", always_xy=True)
PIECE_M, ACCEL, REACH_M, PARALLEL_M = 200.0, 0.5, 1500.0, 8.0
MPH = 1.609344


def speed_kmh(text: str | None, default: float) -> float:
    if not text:
        return default
    try:
        value = float(text.split()[0])
    except ValueError:
        return default
    return value * MPH if "mph" in text else value


def classify(way: dict, line: LineString, classes: list[dict]) -> dict | None:
    lon, lat = (sum(p["lon"] for p in way["geometry"]) / len(way["geometry"]), sum(p["lat"] for p in way["geometry"]) / len(way["geometry"]))
    name = way["tags"].get("name", "")
    for c in classes:
        if c.get("name_contains") and c["name_contains"] not in name:
            continue
        if ("lon_max" in c and lon > c["lon_max"]) or ("lon_min" in c and lon < c["lon_min"]) \
                or ("lat_max" in c and lat > c["lat_max"]) or ("lat_min" in c and lat < c["lat_min"]):
            continue
        return c
    return None


def tracks(cfg: dict) -> list[tuple[dict, LineString, dict, int]]:
    """(way, UTM line, class, number of parallel main tracks incl. itself) for every classified main-line way."""
    ways = [w for w in json.loads((PROJECT / cfg["osm"]).read_text())["elements"] if w["tags"].get("usage") == "main"]
    lines = [(w, LineString([TO_UTM.transform(p["lon"], p["lat"]) for p in w["geometry"]])) for w in ways]
    out = []
    for w, g in lines:
        c = classify(w, g, cfg["classes"])
        if c is None:
            continue
        zone = g.buffer(PARALLEL_M)
        parallel = sum(1 for o, h in lines if o is not w and h.intersects(zone) and zone.intersection(h).length > min(50.0, 0.5 * g.length))
        out.append((w, g, c, 1 + parallel))
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--road-attempt", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    cfg = json.loads(args.config.read_text())
    receivers = json.loads((args.road_attempt / "input/receivers.geojson").read_text())["features"]
    xs = [f["geometry"]["coordinates"][0] for f in receivers]
    ys = [f["geometry"]["coordinates"][1] for f in receivers]
    reach = box(min(xs) - REACH_M, min(ys) - REACH_M, max(xs) + REACH_M, max(ys) + REACH_M)
    stations = {name: Point(TO_UTM.transform(*lonlat)) for name, lonlat in cfg["stations"].items()}
    features, traffic, pk = [], [], 0
    for w, line, cls, ntracks in tracks(cfg):
        if not line.intersects(reach):
            continue
        track = speed_kmh(w["tags"].get("maxspeed"), cfg.get("default_track_kmh", 113.0))
        bridge = cfg.get("bridge_code", "EU3") if w["tags"].get("bridge") == "yes" else ""
        tunnel = 1 if w["tags"].get("tunnel") == "yes" else 0
        n = max(1, round(line.length / PIECE_M))
        for i in range(n):
            piece = substring(line, line.length * i / n, line.length * (i + 1) / n)
            if piece.length < 1 or not piece.intersects(reach):
                continue
            pk += 1
            mid = piece.interpolate(0.5, normalized=True)
            features.append({"type": "Feature", "geometry": {"type": "LineString", "coordinates": [list(c) for c in piece.coords]},
                             "properties": {"PK": pk, "IDSECTION": pk, "NTRACK": 1, "TRACKSPD": round(track, 1), "TRANSFER": cfg.get("transfer", "EU5"),
                                            "ROUGHNESS": cfg.get("roughness", "EU4"), "IMPACT": "", "CURVATURE": 0, "BRIDGE": bridge,
                                            "COMSPD": round(track, 1), "ISTUNNEL": tunnel, "TRACKSPC": 2.0, "OSM_WAY": w["id"], "CLASS": cls["name"]}})
            for key in cls["services"]:
                s = cfg["services"][key]
                if s["stops"]:
                    d = min(mid.distance(stations[name]) for name in s["stops"])
                    spd = min(track, math.sqrt(2 * ACCEL * max(d, 30.0)) * 3.6)
                else:
                    spd = min(track, s["speed_kmh"])
                share = 1.0 / ntracks
                traffic.append({"IDTRAFFIC": len(traffic) + 1, "IDSECTION": pk, "TRAINTYPE": s["trainset"], "TRAINSPD": round(spd, 1),
                                "TDAY": round(s["D"] * share, 4), "TEVENING": round(s["E"] * share, 4), "TNIGHT": round(s["N"] * share, 4)})
    if not features:
        print(json.dumps({"road_attempt": args.road_attempt.name, "sections": 0}))
        return 3
    out = args.out
    if out.exists():
        raise SystemExit(f"{out} exists; rail attempts are never overwritten")
    (out / "input").mkdir(parents=True)
    for name in ("buildings.geojson", "receivers.geojson", "ground.geojson", "terrain.asc"):
        shutil.copy2(args.road_attempt / "input" / name, out / "input" / name)
    rail_vehicles.write(out / "data")
    (out / "input/rail_sections.geojson").write_text(json.dumps({"type": "FeatureCollection", "crs": {"type": "name", "properties": {"name": "EPSG:26911"}}, "features": features}))
    with (out / "input/rail_traffic.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(traffic[0]))
        writer.writeheader()
        writer.writerows(traffic)
    cell = args.road_attempt.name.split("county-la-")[-1].split("-g")[0]
    (out / "rail_manifest.json").write_text(json.dumps({"table_prefix": "RAIL_" + cell.upper().replace("-", "_"), "cell": cell, "road_attempt": args.road_attempt.name,
                                                        "config": args.config.name, "sections": len(features), "traffic_rows": len(traffic)}, indent=1))
    print(json.dumps({"out": str(out), "sections": len(features), "traffic_rows": len(traffic)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
