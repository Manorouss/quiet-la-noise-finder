#!/usr/bin/env python3
"""Build the model-v2 corridor products (lidar sound walls and bridge decks) in 2 km blocks.

Plan (written once to <out>/plan.json, nearest-first from --start): every 2 km UTM block that
touches the county target area (1 km cells with >= 25 buildings, plus a 1.5 km halo) and either
a freeway corridor (TIGER S1100 + 70 m) or the midpoint of an OSM road bridge. Per block:

  walls    full-density 2023 lidar within 70 m of freeways (block + 40 m margin) -> detect_walls
           -> <block>/walls.geojson (WGS84 lines clipped to the block, height in m);
  bridges  lidar to octree level 11 (~1 m spacing) within 6 m of each bridge whose midpoint is in
           the block; class 17 (bridge deck) points within 4 m of the line give a deck elevation
           every 5 m (median per station, spikes and decks seen through crossing structures
           removed, gaps interpolated) -> <block>/bridges.geojson (EPSG:26911 lines, z = deck
           elevation in m NAVD88; bridges without deck points are listed with deck_source none).

<block>/done.json marks a finished block. build_county_tile.py --corridor-dir assembles a tile
halo from finished blocks, and county_daemon.py builds a v2 tile only when they all exist.
Stops when <out>/STOP exists or the plan is done; rerunning skips finished blocks. No new block
starts while implementation/work/pipeline_control/pause-mac exists.

Usage (geo-python-net for HTTPS trust):
  corridor_products.py --out implementation/work/source_cache/corridor_v2 [--jobs 2] [--plan-only] [--blocks e372-n3778 ...]
"""
from __future__ import annotations

import argparse
import gzip
import json
import math
import sys
import time
import zipfile
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import shapely
from pyproj import Transformer
from shapely.geometry import LineString, Point, box, mapping, shape
from shapely.ops import transform as shp_transform, unary_union
from shapely.prepared import prep
from shapely.strtree import STRtree

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "pipeline"))
import detect_walls  # noqa: E402
from build_county_tile import EDGES, dbf_rows, shp_rows  # noqa: E402
from ept_fetch import fetch_points  # noqa: E402

PROJECT = HERE.parents[4]
WORK = PROJECT / "implementation/work"
DENSITY = WORK / "county_building_density_1km.json"
BUILDING_PAGES = WORK / "county_buildings_2026-10-04/pages"
BRIDGES = WORK / "source_cache/osm/la_road_bridges.geojson"
BLOCK = 2000.0
CORRIDOR = 70.0
MARGIN = 40.0
STATION = 5.0
BRIDGE_DEPTH = 11
TO_UTM_NAD = Transformer.from_crs("EPSG:4269", "EPSG:26911", always_xy=True)
TO_UTM_WGS = Transformer.from_crs("EPSG:4326", "EPSG:26911", always_xy=True)


def block_key(x: float, y: float) -> str:
    return f"e{int(x // 1000)}-n{int(y // 1000)}"


def block_box(key: str):
    x, y = (int(part[1:]) * 1000 for part in key.split("-"))
    return box(x, y, x + BLOCK, y + BLOCK)


def stamp() -> str:
    return time.strftime("%FT%TZ", time.gmtime())


# ---------------------------------------------------------------- shared inputs (cached in <out>)

def county_roads(out: Path) -> list[dict]:
    """TIGER S1100 (freeway) and S1630 (ramp) edges in EPSG:26911, cached as roads.geojson."""
    cache = out / "roads.geojson"
    if not cache.exists():
        with zipfile.ZipFile(EDGES) as z:
            attrs = list(dbf_rows(z.read("tl_2025_06037_edges.dbf")))
            geoms = list(shp_rows(z.read("tl_2025_06037_edges.shp")))
        features = []
        for a, g in zip(attrs, geoms):
            if a and g is not None and a.get("ROADFLG") == "Y" and a.get("MTFCC") in ("S1100", "S1630"):
                features.append({"type": "Feature", "properties": {"MTFCC": a["MTFCC"], "TLID": a.get("TLID")},
                                 "geometry": mapping(shp_transform(TO_UTM_NAD.transform, g))})
        cache.write_text(json.dumps({"type": "FeatureCollection", "features": features}))
    return json.loads(cache.read_text())["features"]


def county_bridges() -> list[dict]:
    out = []
    for f in json.loads(BRIDGES.read_text())["features"]:
        line = shp_transform(TO_UTM_WGS.transform, shape(f["geometry"]))
        if line.length >= 5:
            out.append({"line": line, "props": f["properties"]})
    return out


def corridor_buildings(out: Path, freeways) -> Path:
    """Building footprints within 80 m of freeways (from the local county capture), for the wall detector."""
    cache = out / "corridor_buildings.geojson"
    if cache.exists():
        return cache
    zone = prep(unary_union([g.buffer(CORRIDOR + 10) for g in freeways]))
    keep = []
    for page in sorted(BUILDING_PAGES.glob("page_*.geojson.gz")):
        for f in json.loads(gzip.decompress(page.read_bytes()))["features"]:
            if f.get("geometry") and zone.intersects(shape(f["geometry"])):
                keep.append({"type": "Feature", "properties": {"BLD_ID": (f.get("properties") or {}).get("BLD_ID")}, "geometry": f["geometry"]})
    tmp = cache.with_suffix(".part")
    tmp.write_text(json.dumps({"type": "FeatureCollection", "features": keep}))
    tmp.replace(cache)
    return cache


def make_plan(out: Path, start: tuple[float, float]) -> dict:
    roads = county_roads(out)
    freeways = [shape(f["geometry"]) for f in roads if f["properties"]["MTFCC"] == "S1100"]
    bridges = county_bridges()
    cells = [tuple(map(int, k.split(","))) for k, n in json.loads(DENSITY.read_text()).items() if n >= 25]
    target = unary_union([box(x * 1000 - 1500, y * 1000 - 1500, x * 1000 + 2500, y * 1000 + 2500) for x, y in cells])
    fw_tree = STRtree([g.buffer(CORRIDOR) for g in freeways])
    mids = [b["line"].interpolate(0.5, normalized=True) for b in bridges]
    xmin, ymin, xmax, ymax = target.bounds
    blocks = {}
    for bx in np.arange(math.floor(xmin / BLOCK) * BLOCK, xmax, BLOCK):
        for by in np.arange(math.floor(ymin / BLOCK) * BLOCK, ymax, BLOCK):
            core = box(bx, by, bx + BLOCK, by + BLOCK)
            if not core.intersects(target):
                continue
            has_fw = any(fw_tree.geometries[int(j)].intersects(core) for j in fw_tree.query(core))
            blocks[block_key(bx, by)] = {"freeway": has_fw, "bridges": 0, "dist": math.hypot(bx + BLOCK / 2 - start[0], by + BLOCK / 2 - start[1])}
    for m in mids:
        key = block_key(math.floor(m.x / BLOCK) * BLOCK, math.floor(m.y / BLOCK) * BLOCK)
        if key in blocks:
            blocks[key]["bridges"] += 1
    keys = sorted((k for k, v in blocks.items() if v["freeway"] or v["bridges"]), key=lambda k: blocks[k]["dist"])
    plan = {"block_m": BLOCK, "corridor_m": CORRIDOR, "start": list(start), "created_utc": stamp(),
            "blocks": keys, "freeway_blocks": sum(blocks[k]["freeway"] for k in keys), "bridges": sum(blocks[k]["bridges"] for k in keys),
            "wall_detector": {"density": 0.6, "max_angle": 25.0, "corridor": 60.0, "min_length": 25.0},
            "lidar": "USGS 3DEP CA_LosAngeles_1_B23 (2023) EPT", "bridges_source": BRIDGES.name}
    (out / "plan.json").write_text(json.dumps(plan, indent=1) + "\n")
    return plan


# ---------------------------------------------------------------- per-block work (worker processes)

STATE: dict = {}


def init_worker(out: str) -> None:
    out_path = Path(out)
    roads = county_roads(out_path)
    STATE["out"] = out_path
    STATE["freeways"] = [shape(f["geometry"]) for f in roads if f["properties"]["MTFCC"] == "S1100"]
    STATE["refs"] = [shape(f["geometry"]) for f in roads]
    STATE["fw_tree"] = STRtree(STATE["freeways"])
    STATE["ref_tree"] = STRtree(STATE["refs"])
    STATE["bridges"] = county_bridges()
    STATE["br_tree"] = STRtree([b["line"] for b in STATE["bridges"]])
    footprints = [shape(f["geometry"]) for f in json.loads((out_path / "corridor_buildings.geojson").read_text())["features"]]
    STATE["footprints"] = footprints
    STATE["fp_tree"] = STRtree(footprints)


def tangent(line: LineString, t: float) -> float:
    p0, p1 = line.interpolate(max(0.0, t - 3.0)), line.interpolate(min(line.length, t + 3.0))
    return math.atan2(p1.y - p0.y, p1.x - p0.x)


def deck_profile(bridge: dict, xy: np.ndarray, z: np.ndarray, others: list[LineString]) -> dict:
    """Deck elevation every STATION m along a bridge line from class-17 points (xy, z already near it)."""
    line = bridge["line"]
    length = line.length
    stations = np.linspace(0.0, length, max(2, int(math.ceil(length / STATION)) + 1))
    deck = np.full(len(stations), np.nan)
    if len(z):
        pts = shapely.points(xy[:, 0], xy[:, 1])
        near = shapely.distance(pts, line) <= 4.0
        t = shapely.line_locate_point(line, pts[near])
        zz = z[near]
        idx = np.clip(np.round(t / (length / (len(stations) - 1))).astype(int), 0, len(stations) - 1)
        for k in np.unique(idx):
            values = zz[idx == k]
            if len(values) >= 3:
                deck[k] = float(np.median(values))
    valid = ~np.isnan(deck)
    if valid.sum() >= 3:  # spikes: a deck is smooth along its length
        v = np.flatnonzero(valid)
        smooth = np.array([np.median(deck[v[max(0, i - 2):i + 3]]) for i in range(len(v))])
        deck[v[np.abs(deck[v] - smooth) > 1.5]] = np.nan
    # Where another structure crosses, the lidar may see the upper deck: replace values that sit
    # well above the line interpolated from the stations outside the crossing.
    crossing = np.zeros(len(stations), bool)
    heading = tangent(line, length / 2)
    for other in others:
        hit = line.intersection(other.buffer(4.0))
        if hit.is_empty:
            continue
        t_hit = line.project(hit.centroid)
        angle = abs(math.degrees(tangent(other, other.project(hit.centroid)) - tangent(line, t_hit))) % 180
        if min(angle, 180 - angle) > 25:
            crossing |= np.abs(stations - t_hit) <= 12.0
    clean = ~np.isnan(deck) & ~crossing
    if clean.sum() >= 2:
        expected = np.interp(stations, stations[clean], deck[clean])
        high = crossing & ~np.isnan(deck) & (deck > expected + 2.0)
        deck[high] = np.nan
    valid = ~np.isnan(deck)
    share = float(valid.mean())
    if valid.sum() == 0:
        return {"stations": stations, "deck": None, "share": 0.0, "heading": heading}
    deck = np.interp(stations, stations[valid], deck[valid])
    return {"stations": stations, "deck": deck, "share": share, "heading": heading}


def process_block(key: str) -> dict:
    out: Path = STATE["out"]
    target = out / "blocks" / key
    target.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    core = block_box(key)
    margin = core.buffer(MARGIN, join_style=2)
    stats = {"block": key, "walls": 0, "wall_km": 0.0, "bridges": 0, "bridges_with_deck": 0, "points_walls": 0, "points_bridges": 0}
    cache = out / "ept_cache"

    # Walls.
    freeways = [STATE["freeways"][int(j)] for j in STATE["fw_tree"].query(margin.buffer(CORRIDOR + 100))]
    walls = []
    if freeways:
        fw_union = unary_union(freeways)
        area = fw_union.buffer(CORRIDOR).intersection(margin)
        if not area.is_empty:
            d = fetch_points(area, cache=cache)
            stats["points_walls"] = len(d["x"])
            refs = detect_walls.References([STATE["refs"][int(j)] for j in STATE["ref_tree"].query(margin.buffer(300))])
            footprints = [STATE["footprints"][int(j)].buffer(1.0) for j in STATE["fp_tree"].query(margin)]
            args = argparse.Namespace(corridor=60.0, min_length=25.0, density=0.6, max_angle=25.0)
            if len(d["x"]):
                walls = detect_walls.detect_area(d, fw_union, footprints, refs, args, clip=core)
            del d
    (target / "walls.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": walls}) + "\n")
    stats["walls"], stats["wall_km"] = len(walls), round(sum(w["properties"]["length_m"] for w in walls) / 1000, 3)

    # Bridges whose midpoint is in the block.
    mine = [b for b in (STATE["bridges"][int(j)] for j in STATE["br_tree"].query(core))
            if core.contains(b["line"].interpolate(0.5, normalized=True))]
    features = []
    if mine:
        area = unary_union([b["line"].buffer(6.0) for b in mine])
        d = fetch_points(area, max_depth=BRIDGE_DEPTH, cache=cache)
        stats["points_bridges"] = len(d["x"])
        deck_pts = d["cls"] == 17
        xy, z = np.column_stack([d["x"][deck_pts], d["y"][deck_pts]]), d["z"][deck_pts].astype(float)
        tree = STRtree(shapely.points(xy[:, 0], xy[:, 1])) if len(z) else None
        for b in mine:
            sel = tree.query(b["line"].buffer(4.5)) if tree is not None else np.zeros(0, int)
            others = [STATE["bridges"][int(j)]["line"] for j in STATE["br_tree"].query(b["line"].buffer(6.0)) if STATE["bridges"][int(j)] is not b]
            prof = deck_profile(b, xy[sel], z[sel], others)
            props = {"osm_id": b["props"].get("@id"), "highway": b["props"].get("highway"), "layer": b["props"].get("layer"),
                     "name": b["props"].get("name") or b["props"].get("ref"), "deck_source": "lidar_class17" if prof["deck"] is not None else "none",
                     "lidar_station_share": round(prof["share"], 2)}
            if prof["deck"] is None:
                coords = [[p[0], p[1]] for p in b["line"].coords]
            else:
                coords = [[round(q.x, 2), round(q.y, 2), round(float(h), 2)] for q, h in zip((b["line"].interpolate(s) for s in prof["stations"]), prof["deck"])]
                stats["bridges_with_deck"] += 1
            features.append({"type": "Feature", "properties": props, "geometry": {"type": "LineString", "coordinates": coords}})
        del d
    stats["bridges"] = len(features)
    crs = {"type": "name", "properties": {"name": "EPSG:26911"}}
    (target / "bridges.geojson").write_text(json.dumps({"type": "FeatureCollection", "crs": crs, "features": features}) + "\n")
    stats["seconds"] = round(time.monotonic() - started, 1)
    stats["finished_utc"] = stamp()
    (target / "done.json").write_text(json.dumps(stats, indent=1) + "\n")
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--start", type=float, nargs=2, default=(356500.0, 3782500.0))
    parser.add_argument("--jobs", type=int, default=2)
    parser.add_argument("--plan-only", action="store_true")
    parser.add_argument("--blocks", nargs="*", help="only these block keys (default: the whole plan)")
    args = parser.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    log = (out / "corridor.log").open("a")
    plan = json.loads((out / "plan.json").read_text()) if (out / "plan.json").exists() else make_plan(out, tuple(args.start))
    print(f"{stamp()} plan: {len(plan['blocks'])} blocks ({plan['freeway_blocks']} with freeways), {plan['bridges']} bridges", file=log, flush=True)
    if args.plan_only:
        print(json.dumps({k: v for k, v in plan.items() if k != "blocks"}, indent=1))
        return 0
    freeways = [shape(f["geometry"]) for f in county_roads(out) if f["properties"]["MTFCC"] == "S1100"]
    corridor_buildings(out, freeways)
    todo = [k for k in (args.blocks or plan["blocks"]) if not (out / "blocks" / k / "done.json").exists()]
    print(f"{stamp()} {len(todo)} blocks to build", file=log, flush=True)
    failures = 0
    with ProcessPoolExecutor(args.jobs, initializer=init_worker, initargs=(str(out),)) as pool:
        pending = {}
        queue = list(todo)
        while queue or pending:
            paused = (WORK / "pipeline_control/pause-mac").exists()  # the owner's pause button holds new blocks too
            while queue and len(pending) < args.jobs and not paused and not (out / "STOP").exists():
                key = queue.pop(0)
                pending[pool.submit(process_block, key)] = key
            if not pending:
                if queue and paused and not (out / "STOP").exists():
                    time.sleep(10)
                    continue
                break
            done = next(iter(f for f in list(pending) if f.done()), None)
            if done is None:
                time.sleep(2)
                continue
            key = pending.pop(done)
            try:
                s = done.result()
                print(f"{stamp()} done {key}: {s['wall_km']} km walls, {s['bridges_with_deck']}/{s['bridges']} bridge decks, "
                      f"{s['points_walls'] + s['points_bridges']:,} points, {s['seconds']} s", file=log, flush=True)
            except Exception as exc:  # keep going; the block stays without done.json and is retried on the next run
                failures += 1
                print(f"{stamp()} FAILED {key}: {exc!r}"[:600], file=log, flush=True)
    print(f"{stamp()} stopped: {failures} failures", file=log, flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
