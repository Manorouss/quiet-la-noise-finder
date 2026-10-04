#!/usr/bin/env python3
"""Build portal tile assets from one completed engine attempt, with QA flags.

Generalizes scripts/build-direct-union-pilot-tile.py (wip branch) to any tile:
copies each D/E/N LAeq/Leq cell as exported, links façade receivers to their
LARIAC buildings, masks receivers above the physical ceiling (any period) and
labels receivers within --on-road-m of a road centerline. Shared buildings
reuse the geometry already published (or built earlier in this batch) so the
client deduplicates them by key.

Usage:
  build_tile_assets.py --tile r03-c03 --attempt <attempt dir> --out-root <candidate dir>
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import sys
from collections import Counter
from pathlib import Path

import numpy as np

QA = Path(__file__).resolve().parents[1] / "qa"
sys.path.insert(0, str(QA))
import physical_ceiling as pc  # noqa: E402
from apply_release_flags import road_distance  # noqa: E402

APP = Path(__file__).resolve().parents[2]
RELEASE = APP / "src/data/pilot-release-v1"
CONTRACT = APP / "src/data/pilot-release-contract.json"
PERIODS = ("D", "E", "N")
POINT_RE = re.compile(r"POINT Z \(([-+0-9.eE]+) ([-+0-9.eE]+) ([-+0-9.eE]+)\)")
MODEL = "tarzana-combined-road-study-r02-v1"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def utm11_to_wgs84(easting: float, northing: float) -> tuple[float, float]:
    a, ecc_sq, k0 = 6378137.0, 0.00669438, 0.9996
    e1 = (1 - math.sqrt(1 - ecc_sq)) / (1 + math.sqrt(1 - ecc_sq))
    x, y = easting - 500000.0, northing
    mu = y / k0 / (a * (1 - ecc_sq / 4 - 3 * ecc_sq**2 / 64 - 5 * ecc_sq**3 / 256))
    phi1 = mu + (3 * e1 / 2 - 27 * e1**3 / 32) * math.sin(2 * mu) + (21 * e1**2 / 16 - 55 * e1**3 / 32) * math.sin(4 * mu) + 151 * e1**3 / 96 * math.sin(6 * mu)
    n1 = a / math.sqrt(1 - ecc_sq * math.sin(phi1) ** 2)
    t1, c1 = math.tan(phi1) ** 2, ecc_sq / (1 - ecc_sq) * math.cos(phi1) ** 2
    ep2 = ecc_sq / (1 - ecc_sq)
    r1 = a * (1 - ecc_sq) / (1 - ecc_sq * math.sin(phi1) ** 2) ** 1.5
    d = x / (n1 * k0)
    lat = phi1 - n1 * math.tan(phi1) / r1 * (d**2 / 2 - (5 + 3*t1 + 10*c1 - 4*c1**2 - 9*ep2) * d**4 / 24 + (61 + 90*t1 + 298*c1 + 45*t1**2 - 252*ep2 - 3*c1**2) * d**6 / 720)
    lon = math.radians(-117) + (d - (1 + 2*t1 + c1) * d**3 / 6 + (5 - 2*c1 + 28*t1 - 3*c1**2 + 8*ep2 + 24*t1**2) * d**5 / 120) / math.cos(phi1)
    return math.degrees(lon), math.degrees(lat)


def same_footprint(left: dict, right: dict, tolerance: float = 5e-7) -> bool:
    if left.get("type") != "Polygon" or right.get("type") != "Polygon":
        return False
    a_rings, b_rings = left["coordinates"], right["coordinates"]
    if len(a_rings) != len(b_rings):
        return False

    def same_ring(a, b):
        if len(a) != len(b):
            return False
        n = len(a)
        for candidate in (b, list(reversed(b))):
            for offset in range(n):
                if all(abs(a[i][0] - candidate[(i + offset) % n][0]) <= tolerance and
                       abs(a[i][1] - candidate[(i + offset) % n][1]) <= tolerance for i in range(n)):
                    return True
        return False
    return all(same_ring(a, b) for a, b in zip(a_rings, b_rings))


def known_buildings(out_root: Path, tile_id: str) -> dict[str, tuple[str, dict]]:
    """source_bld_id -> (tile_id, geometry) from the release and earlier candidates."""
    known: dict[str, tuple[str, dict]] = {}
    contract = json.loads(CONTRACT.read_text())
    paths = [(t["tile_id"], RELEASE / next(a["path"] for a in t["assets"] if a["kind"] == "buildings")) for t in contract["tiles"]]
    paths += [(p.parent.name, p) for p in sorted(out_root.glob("*/buildings.geojson")) if p.parent.name != tile_id]
    for other, path in paths:
        for f in json.loads(path.read_text())["features"]:
            known.setdefault(str(f["properties"]["source_bld_id"]), (other, f["geometry"]))
    return known


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--tile", required=True)
    parser.add_argument("--attempt", type=Path, required=True)
    parser.add_argument("--out-root", type=Path, required=True)
    parser.add_argument("--margin-db", type=float, default=6.0)
    parser.add_argument("--on-road-m", type=float, default=3.0)
    args = parser.parse_args()
    tile_id, attempt = args.tile, args.attempt.resolve()
    out = args.out_root.resolve() / tile_id
    if out.exists():
        raise FileExistsError(f"candidate output must be new: {out}")

    manifest = json.loads((attempt / "attempt_manifest.json").read_text())
    run = json.loads((attempt / "phase1_run_manifest.json").read_text())
    if run.get("status") != "completed_export_unvalidated":
        raise ValueError("attempt has no completed export")
    export = attempt / "export/receivers_level_d_e_n.csv"
    export_bytes = export.read_bytes()
    if sha(export_bytes) != run["artifacts"]["export"]["sha256"]:
        raise ValueError("export differs from its run manifest")
    receiver_doc = json.loads((attempt / "input/receivers.geojson").read_text())
    buildings_by_pk = {int(f["properties"]["PK"]): f for f in json.loads((attempt / "input/buildings.geojson").read_text())["features"]}
    receiver_by_id = {int(f["properties"]["PK"]): f for f in receiver_doc["features"]}

    grouped: dict[int, dict[str, dict]] = {}
    with export.open(newline="", encoding="utf-8-sig") as stream:
        for row in csv.DictReader(stream):
            rid, period = int(row["IDRECEIVER"]), row["PERIOD"].strip().upper()
            xyz = [float(v) for v in POINT_RE.fullmatch(row["THE_GEOM"].split(";", 1)[-1]).groups()]
            src = receiver_by_id[rid]["geometry"]["coordinates"]
            if max(abs(xyz[i] - float(src[i])) for i in (0, 1)) > 1e-6:
                raise ValueError(f"receiver/result coordinate mismatch: {rid}")
            laeq, leq = float(row["LAEQ"]), float(row["LEQ"])
            if not (math.isfinite(laeq) and math.isfinite(leq)) or laeq == -99:
                raise ValueError(f"non-finite level: {rid}/{period}")
            if period in grouped.setdefault(rid, {}):
                raise ValueError(f"duplicate receiver-period {rid}/{period}")
            grouped[rid][period] = {"laeq": laeq, "leq": leq}
    if set(grouped) != set(receiver_by_id) or any(set(p) != set(PERIODS) for p in grouped.values()):
        raise ValueError("receiver/period completeness failed")

    ids = np.array(sorted(receiver_by_id))
    xy = np.array([receiver_by_id[i]["geometry"]["coordinates"][:2] for i in ids], dtype=float)
    masked: set[int] = set()
    for period in PERIODS:
        segments, powers = pc.load_sources(attempt / "input/periods.geojson", period)
        ceiling = pc.ceiling_levels(xy, segments, powers)
        masked |= {int(i) for i, c in zip(ids, ceiling) if grouped[int(i)][period]["laeq"] - c > args.margin_db}
    on_road = {int(i) for i, d in zip(ids, road_distance(xy, segments)) if d <= args.on_road_m} - masked

    receivers, heights, linked = [], Counter(), {}
    for rid in ids.tolist():
        props = receiver_by_id[rid]["properties"]
        building_pk = int(props["BUILDING_PK"]) if props.get("BUILDING_PK") is not None else None
        source_bld_id = str(props.get("SOURCE_BLD_ID") or "") or None
        if building_pk is not None:
            if str(buildings_by_pk[building_pk]["properties"].get("SOURCE_BLD_ID")) != source_bld_id:
                raise ValueError(f"receiver building lineage mismatch: {rid}")
            linked.setdefault(building_pk, []).append(rid)
        h = float(props["HEIGHT_ABOVE_GROUND_M"])
        heights[str(h)] += 1
        lng, lat = utm11_to_wgs84(*map(float, receiver_by_id[rid]["geometry"]["coordinates"][:2]))
        values = {p: None for p in PERIODS} if rid in masked else grouped[rid]
        feature_props = {
            "id": rid, "receiver_key": f"{tile_id}:{props['RECEIVER_KEY']}", "source_receiver_key": props["RECEIVER_KEY"],
            "source_tile": tile_id, "masked": rid in masked, "receiver_family": props.get("RECEIVER_FAMILY"),
            "building_pk": building_pk, "building_key": f"lariac:{source_bld_id}" if source_bld_id else None,
            "height_agl_m": h, **values,
        }
        if rid in on_road:
            feature_props["on_road"] = True
        receivers.append({"type": "Feature", "id": f"{tile_id}:{rid}", "geometry": {"type": "Point", "coordinates": [lng, lat]}, "properties": feature_props})

    known = known_buildings(args.out_root.resolve(), tile_id)
    buildings, shared = [], []
    for pk, rids in sorted(linked.items()):
        props = buildings_by_pk[pk]["properties"]
        source_bld_id = str(props["SOURCE_BLD_ID"])
        geom = buildings_by_pk[pk]["geometry"]
        if geom.get("type") != "Polygon":
            raise ValueError(f"unsupported building geometry {geom.get('type')}")
        geometry = {"type": "Polygon", "coordinates": [[list(utm11_to_wgs84(float(x), float(y))) for x, y, *_ in ring] for ring in geom["coordinates"]]}
        tiles = {tile_id}
        if source_bld_id in known:
            other, prior = known[source_bld_id]
            if not same_footprint(prior, geometry):
                raise ValueError(f"shared building footprint differs: {source_bld_id} vs {other}")
            geometry, tiles = prior, tiles | {other}
            shared.append(source_bld_id)
        summaries = {}
        for period in PERIODS:
            levels = [grouped[r][period]["laeq"] for r in rids if r not in masked]
            summaries[period] = {"min": min(levels) if levels else None, "max": max(levels) if levels else None,
                                 "receiver_count": len(rids), "unavailable_count": len(rids) - len(levels)}
        key = f"lariac:{source_bld_id}"
        buildings.append({"type": "Feature", "id": key, "geometry": geometry, "properties": {
            "building_pk": pk, "building_key": key, "source_tile": tile_id, "source_tile_ids": sorted(tiles),
            "source_bld_id": source_bld_id, "height_m": float(props["HEIGHT"]), "receiver_ids": sorted(rids),
            "receiver_keys": sorted(f"{tile_id}:{receiver_by_id[r]['properties']['RECEIVER_KEY']}" for r in rids),
            "receiver_count": len(rids), "periods": summaries}})

    levels = [v["laeq"] for r in receivers if not r["properties"]["masked"] for v in (r["properties"][p] for p in PERIODS)]
    coords = [r["geometry"]["coordinates"] for r in receivers]
    bbox = [min(c[0] for c in coords), min(c[1] for c in coords), max(c[0] for c in coords), max(c[1] for c in coords)]
    facade = sum(len(v) for v in linked.values())
    quality = {"physical_ceiling_margin_db": args.margin_db, "physical_ceiling_masked_ids": sorted(masked),
               "on_road_distance_m": args.on_road_m, "on_road_count": len(on_road),
               "method": "science/qa/physical_ceiling.py; science/pipeline/build_tile_assets.py"}
    assumptions = {
        "source_scenario": "Assumed historical-context mixed-road scenario (852 corrected freeway + 583 corrected lower-road sources).",
        "traffic": "Evening flow = 0.6 × day, night = 0.2 × day. Not observed or current traffic.",
        "method": "Direct union export copied as emitted; physically impossible receivers masked, on-road receivers labelled.",
        "calibration": "Uncalibrated modeled exterior LAeq; not a measurement, interior level, or quietness rating.",
    }
    meta = {"model": MODEL, "tile_id": tile_id, "status": "candidate_pending_owner_review", "coordinate_crs": "OGC:CRS84",
            "source_projected_crs": "EPSG:26911", "bbox_wgs84": bbox, "receiver_count": len(receivers),
            "numeric_rows": 3 * (len(receivers) - len(masked)), "numeric_laeq_min": min(levels), "numeric_laeq_max": max(levels),
            "masked_ids": sorted(masked), "receiver_height_counts_m": dict(sorted(heights.items())),
            "building_count": len(buildings), "facade_receiver_count": facade, "attempt_id": manifest["attempt_id"],
            "source_hashes": {"export": sha(export_bytes)}, "shared_building_source_ids": sorted(shared),
            "quality_flags": quality, "assumptions": assumptions}
    public_manifest = {"schema": "quiet_la_tarzana_direct_union_tile_v1", "model": MODEL, "tile_id": tile_id,
                       "status": meta["status"], "receiver_count": len(receivers), "numeric_rows": meta["numeric_rows"],
                       "building_count": len(buildings), "facade_receiver_count": facade,
                       "receiver_height_counts_m": meta["receiver_height_counts_m"], "bbox_wgs84": bbox,
                       "masked_ids": sorted(masked), "min_laeq": min(levels), "max_laeq": max(levels),
                       "source_csv_sha256": sha(export_bytes), "attempt_id": manifest["attempt_id"],
                       "quality_flags": quality, "assumptions": assumptions}
    out.mkdir(parents=True)
    assets = {
        "benchmark.geojson": json.dumps({"type": "FeatureCollection", "features": receivers, "metadata": meta}, separators=(",", ":"), ensure_ascii=False).encode() + b"\n",
        "buildings.geojson": json.dumps({"type": "FeatureCollection", "features": buildings, "metadata": meta}, separators=(",", ":"), ensure_ascii=False).encode() + b"\n",
        "build-manifest.json": (json.dumps(public_manifest, indent=2, ensure_ascii=False) + "\n").encode(),
    }
    for name, data in assets.items():
        (out / name).write_bytes(data)
    summary = {"tile": tile_id, "attempt": attempt.name, "receivers": len(receivers), "buildings": len(buildings),
               "masked": len(masked), "on_road": len(on_road), "laeq_range": [round(min(levels), 2), round(max(levels), 2)],
               "shared_buildings": len(shared), "outputs": {k: {"bytes": len(v), "sha256": sha(v)} for k, v in assets.items()}}
    (out / "build-receipt.json").write_text(json.dumps(summary, indent=1) + "\n")
    print(json.dumps({k: v for k, v in summary.items() if k != "outputs"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
