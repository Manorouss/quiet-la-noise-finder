#!/usr/bin/env python3
"""Build a hash-bound candidate map tile from supplied, approved source files.

This source-only builder does not fetch, copy, or bundle campaign inputs. Paths
are explicit arguments so no workstation paths enter the app source or receipt.
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

PERIODS = ("D", "E", "N")
POINT_RE = re.compile(r"POINT Z \(([-+0-9.eE]+) ([-+0-9.eE]+) ([-+0-9.eE]+)\)")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def utm11_to_wgs84(easting: float, northing: float) -> tuple[float, float]:
    a, ecc_sq, k0 = 6378137.0, 0.00669438, 0.9996
    e1 = (1 - math.sqrt(1 - ecc_sq)) / (1 + math.sqrt(1 - ecc_sq))
    x, y = easting - 500000.0, northing
    mu = y / k0 / (a * (1 - ecc_sq / 4 - 3 * ecc_sq**2 / 64 - 5 * ecc_sq**3 / 256))
    phi1 = mu + (3 * e1 / 2 - 27 * e1**3 / 32) * math.sin(2 * mu) + (21 * e1**2 / 16 - 55 * e1**4 / 32) * math.sin(4 * mu) + 151 * e1**3 / 96 * math.sin(6 * mu)
    n1 = a / math.sqrt(1 - ecc_sq * math.sin(phi1) ** 2)
    t1, c1 = math.tan(phi1) ** 2, ecc_sq / (1 - ecc_sq) * math.cos(phi1) ** 2
    ep2 = ecc_sq / (1 - ecc_sq)
    r1 = a * (1 - ecc_sq) / (1 - ecc_sq * math.sin(phi1) ** 2) ** 1.5
    d = x / (n1 * k0)
    lat = phi1 - n1 * math.tan(phi1) / r1 * (d**2 / 2 - (5 + 3*t1 + 10*c1 - 4*c1**2 - 9*ep2) * d**4 / 24 + (61 + 90*t1 + 298*c1 + 45*t1**2 - 252*ep2 - 3*c1**2) * d**6 / 720)
    lon = math.radians(-117) + (d - (1 + 2*t1 + c1) * d**3 / 6 + (5 - 2*c1 + 28*t1 - 3*c1**2 + 8*ep2 + 24*t1**2) * d**5 / 120) / math.cos(phi1)
    return math.degrees(lon), math.degrees(lat)


def finite(value):
    if value is None or value == "":
        return None
    number = float(value)
    if not math.isfinite(number) or number == -99:
        return None
    return number


def main() -> None:
    parser = argparse.ArgumentParser()
    for key in ("combined_csv", "receivers", "buildings", "attempt_manifest", "combination_manifest", "independent_audit", "reference_attempt_manifest", "legacy_receivers", "legacy_buildings"):
        parser.add_argument(f"--{key.replace('_', '-')}", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    inputs = {key: getattr(args, key) for key in ("combined_csv", "receivers", "buildings", "attempt_manifest", "combination_manifest", "independent_audit", "reference_attempt_manifest", "legacy_receivers", "legacy_buildings")}
    source_bytes = {key: value.read_bytes() for key, value in inputs.items()}
    app_root = Path(__file__).resolve().parents[1]
    contract = json.loads((app_root / "src/data/pilot-release-contract.json").read_text())
    c02 = next(tile for tile in contract["tiles"] if tile["tile_id"] == "r02-c02")
    c03 = next(tile for tile in contract["tiles"] if tile["tile_id"] == "r02-c03")
    locked_sources = {
        "combined_csv": c02["provenance"]["combined_derivative_sha256"],
        "receivers": c02["provenance"]["receiver_input_sha256"],
        "buildings": c02["provenance"]["shared_buildings_input_sha256"],
        "attempt_manifest": c02["provenance"]["attempt_manifest_sha256"],
        "combination_manifest": c02["provenance"]["combination_manifest_sha256"],
        "independent_audit": c02["provenance"]["independent_acceptance_audit_sha256"],
        "reference_attempt_manifest": c03["provenance"]["attempt_manifest_sha256"],
        "legacy_receivers": c03["assets"][0]["sha256"],
        "legacy_buildings": c03["assets"][1]["sha256"],
    }
    for key, expected_hash in locked_sources.items():
        if sha(source_bytes[key]) != expected_hash:
            raise SystemExit(f"source hash rejected for {key}: {sha(source_bytes[key])}")
    if len(source_bytes["combined_csv"]) != 8137783 or len(source_bytes["receivers"]) != 5805684 or len(source_bytes["buildings"]) != 76206417:
        raise SystemExit("approved source input byte size drift")

    attempt = json.loads(source_bytes["attempt_manifest"])
    reference_attempt = json.loads(source_bytes["reference_attempt_manifest"])
    combination = json.loads(source_bytes["combination_manifest"])
    audit = json.loads(source_bytes["independent_audit"])
    if attempt.get("attempt_id") != "phase1-tarzana-full-mixed-r02-c02-freeway-v3" or attempt.get("counts", {}).get("receivers") != 8809:
        raise SystemExit("attempt manifest identity/count rejected")
    if combination.get("status") != "BUILT_UNVALIDATED" or combination.get("row_count") != 26427 or combination.get("output", {}).get("sha256") != locked_sources["combined_csv"]:
        raise SystemExit("combination manifest identity/count/source rejected")
    audit_combination_binding = audit.get("bindings", {}).get("combination_manifest", {})
    if not audit.get("combination_accepted") or audit.get("status") != "PASS_FULL_TARZANA_PRIMARY_CLOSURE_AND_DERIVATIVE" or audit.get("receiver_identity", {}).get("full", {}).get("count") != 8809:
        raise SystemExit("independent combination acceptance audit rejected")
    if audit_combination_binding.get("sha256") != locked_sources["combination_manifest"] or audit_combination_binding.get("bytes") != len(source_bytes["combination_manifest"]):
        raise SystemExit("independent audit does not bind the canonical accepted combination manifest")
    if attempt.get("physics_contract") != reference_attempt.get("physics_contract") or attempt.get("source_contract") != reference_attempt.get("source_contract"):
        raise SystemExit("r02-c02 assumptions differ from the accepted r02-c03 study contract")
    for input_name in ("buildings.geojson", "ground.geojson", "periods.geojson", "sources.geojson", "terrain.asc"):
        if attempt.get("inputs", {}).get(input_name, {}).get("sha256") != reference_attempt.get("inputs", {}).get(input_name, {}).get("sha256"):
            raise SystemExit(f"shared study input differs across tiles: {input_name}")
    if audit.get("bindings", {}).get("combined_derivative", {}).get("sha256") != locked_sources["combined_csv"] or audit.get("bindings", {}).get("freeway_receivers", {}).get("sha256") != locked_sources["receivers"]:
        raise SystemExit("independent audit is not bound to the supplied derivative and receiver input")

    input_doc = json.loads(source_bytes["receivers"])
    input_features = input_doc.get("features", [])
    if len(input_features) != 8809:
        raise SystemExit(f"unexpected receiver input count {len(input_features)}")
    receiver_by_id = {}
    source_receiver_keys = set()
    for feature in input_features:
        props = feature.get("properties") or {}
        rid = int(props["PK"])
        key = str(props["RECEIVER_KEY"])
        if rid in receiver_by_id or key in source_receiver_keys:
            raise SystemExit(f"duplicate receiver primary/source key: {rid}/{key}")
        if feature.get("geometry", {}).get("type") != "Point" or len(feature["geometry"].get("coordinates", [])) < 2:
            raise SystemExit(f"invalid receiver geometry: {rid}")
        receiver_by_id[rid] = feature
        source_receiver_keys.add(key)

    grouped = {}
    row_count = numeric_count = 0
    min_laeq = math.inf
    max_laeq = -math.inf
    with inputs["combined_csv"].open(newline="", encoding="utf-8") as stream:
        for row in csv.DictReader(stream):
            row_count += 1
            rid = int(row["IDRECEIVER"])
            period = row["PERIOD"]
            if rid not in receiver_by_id or period not in PERIODS:
                raise SystemExit(f"unexpected derivative receiver/period {rid}/{period}")
            match = POINT_RE.fullmatch(row["THE_GEOM"].split(";", 1)[-1])
            if not match:
                raise SystemExit(f"unparseable result point geometry for receiver {rid}")
            xyz = [float(match.group(i)) for i in (1, 2, 3)]
            src = receiver_by_id[rid]["geometry"]["coordinates"]
            if not all(math.isfinite(value) for value in xyz) or max(abs(xyz[i] - float(src[i])) for i in (0, 1)) > 1e-6:
                raise SystemExit(f"result/source coordinate mismatch for receiver {rid}")
            period_values = {"laeq": finite(row["LAEQ"]), "freeway": finite(row["FREEWAY_LAEQ"]), "local": finite(row["LOWER_LAEQ"])}
            if period_values["laeq"] is None:
                raise SystemExit(f"unavailable combined LAEQ for receiver {rid} period {period}")
            numeric_count += 1
            min_laeq = min(min_laeq, period_values["laeq"])
            max_laeq = max(max_laeq, period_values["laeq"])
            lng, lat = utm11_to_wgs84(xyz[0], xyz[1])
            item = grouped.setdefault(rid, {"coordinates": [lng, lat], "periods": {}})
            if period in item["periods"]:
                raise SystemExit(f"duplicate receiver period {rid}/{period}")
            item["periods"][period] = period_values
    if row_count != 26427 or numeric_count != 26427 or len(grouped) != 8809:
        raise SystemExit(f"combined identity/count failure: {row_count}/{numeric_count}/{len(grouped)}")
    if any(set(item["periods"]) != set(PERIODS) for item in grouped.values()):
        raise SystemExit("one or more receivers do not have exactly D/E/N")

    all_buildings = json.loads(source_bytes["buildings"])
    building_by_pk = {}
    for feature in all_buildings.get("features", []):
        props = feature.get("properties") or {}
        pk = int(props["PK"])
        if pk in building_by_pk:
            raise SystemExit(f"duplicate source building PK {pk}")
        building_by_pk[pk] = feature
    facade = [f for f in input_features if (f.get("properties") or {}).get("RECEIVER_FAMILY") == "building_facade_exterior"]
    building_receivers = {}
    for feature in facade:
        props = feature["properties"]
        pk = int(props["BUILDING_PK"])
        source_building = building_by_pk.get(pk)
        if source_building is None or str(props["SOURCE_BLD_ID"]) != str(source_building["properties"].get("SOURCE_BLD_ID")):
            raise SystemExit(f"receiver-to-building lineage mismatch for receiver {props['PK']}")
        building_receivers.setdefault(pk, []).append(int(props["PK"]))
    if len(facade) != 3695 or len(building_receivers) != 206:
        raise SystemExit(f"unexpected facade/building subset {len(facade)}/{len(building_receivers)}")

    receivers = []
    heights = Counter()
    for rid in sorted(grouped):
        item, original = grouped[rid], receiver_by_id[rid]
        props = original["properties"]
        heights[str(props.get("HEIGHT_ABOVE_GROUND_M"))] += 1
        feature_props = {
            "id": rid,
            "receiver_key": f"r02-c02:{props['RECEIVER_KEY']}",
            "source_receiver_key": str(props["RECEIVER_KEY"]),
            "source_tile": "r02-c02",
            "masked": False,
            "receiver_family": props.get("RECEIVER_FAMILY"),
            "building_pk": int(props["BUILDING_PK"]) if props.get("BUILDING_PK") is not None else None,
            "building_key": f"lariac:{props['SOURCE_BLD_ID']}" if props.get("SOURCE_BLD_ID") else None,
            "height_agl_m": float(props["HEIGHT_ABOVE_GROUND_M"]),
        }
        feature_props.update({period: item["periods"][period] for period in PERIODS})
        receivers.append({"type": "Feature", "id": f"r02-c02:{rid}", "geometry": {"type": "Point", "coordinates": item["coordinates"]}, "properties": feature_props})

    building_features = []
    for pk, receiver_ids in sorted(building_receivers.items()):
        source = building_by_pk[pk]
        p = source["properties"]
        geometry = source["geometry"]
        if geometry.get("type") != "Polygon":
            raise SystemExit(f"unsupported building geometry type for {pk}: {geometry.get('type')}")
        coords = [[[list(utm11_to_wgs84(float(point[0]), float(point[1]))) for point in ring] for ring in geometry["coordinates"]]][0]
        periods = {}
        for period in PERIODS:
            values = [grouped[rid]["periods"][period]["laeq"] for rid in receiver_ids]
            periods[period] = {"min": min(values), "max": max(values), "receiver_count": len(values), "unavailable_count": 0}
        building_key = f"lariac:{p['SOURCE_BLD_ID']}"
        building_features.append({
            "type": "Feature", "id": building_key, "geometry": {"type": "Polygon", "coordinates": coords},
            "properties": {
                "building_pk": pk, "building_key": building_key, "source_tile": "r02-c02",
                "source_bld_id": str(p["SOURCE_BLD_ID"]), "height_m": float(p["HEIGHT"]),
                "receiver_ids": sorted(receiver_ids), "receiver_keys": sorted(f"r02-c02:{receiver_by_id[rid]['properties']['RECEIVER_KEY']}" for rid in receiver_ids),
                "receiver_count": len(receiver_ids), "periods": periods,
            },
        })

    all_points = [feature["geometry"]["coordinates"] for feature in receivers]
    bbox = [min(p[0] for p in all_points), min(p[1] for p in all_points), max(p[0] for p in all_points), max(p[1] for p in all_points)]
    source_hashes = {key: sha(value) for key, value in source_bytes.items()}
    common_metadata = {
        "model": "tarzana-combined-road-study-r02-v1", "tile_id": "r02-c02", "status": "candidate_internal_pending_root_review",
        "coordinate_crs": "OGC:CRS84", "source_projected_crs": "EPSG:26911", "bbox_wgs84": bbox,
        "receiver_count": len(receivers), "numeric_rows": numeric_count, "numeric_laeq_min": min_laeq, "numeric_laeq_max": max_laeq,
        "masked_ids": [], "receiver_height_counts_m": dict(sorted(heights.items())),
        "building_count": len(building_features), "facade_receiver_count": len(facade),
        "source_hashes": {key: source_hashes[key] for key in ("combined_csv", "receivers", "buildings")},
        "provenance_audits": {"combination_manifest_sha256": source_hashes["combination_manifest"], "combination_independent_audit_sha256": source_hashes["independent_audit"], "attempt_manifest_sha256": source_hashes["attempt_manifest"]},
        "claim_boundary": "Internal assumed historical-context mixed-road engineering only; relative and uncalibrated, not measured dBA/CNEL, current/live traffic, quietness, indoor sound, or an address prediction.",
        "no_acoustic_recomputation": True,
    }
    receiver_doc = {"type": "FeatureCollection", "features": receivers, "metadata": common_metadata}
    building_metadata = {**common_metadata, "source_building_subset_count": len(building_features)}
    building_doc = {"type": "FeatureCollection", "features": building_features, "metadata": building_metadata}
    public_manifest = {
        "schema": "quiet_la_tarzana_combined_road_tile_v1", "model": common_metadata["model"], "tile_id": "r02-c02",
        "status": "candidate_internal_pending_root_review", "receiver_count": len(receivers), "numeric_rows": numeric_count,
        "building_count": len(building_features), "facade_receiver_count": len(facade), "receiver_height_counts_m": dict(sorted(heights.items())),
        "bbox_wgs84": bbox, "masked_ids": [], "min_laeq": min_laeq, "max_laeq": max_laeq,
        "source_csv_sha256": source_hashes["combined_csv"], "source_receivers_sha256": source_hashes["receivers"],
        "source_buildings_sha256": source_hashes["buildings"], "combination_audit_sha256": source_hashes["independent_audit"],
        "disclosure": "Assumed historical-context mixed-road exterior LAeq; uncalibrated and not measured/current traffic. See study assumptions.",
    }
    legacy_receivers = json.loads(source_bytes["legacy_receivers"])
    legacy_buildings = json.loads(source_bytes["legacy_buildings"])
    legacy_keys = {str(feature["properties"]["source_bld_id"]): feature for feature in legacy_buildings["features"]}
    candidate_keys = {str(feature["properties"]["source_bld_id"]): feature for feature in building_features}
    shared_building_ids = sorted(legacy_keys.keys() & candidate_keys.keys())
    expected_shared = c02["provenance"]["cross_tile_duplicate_building_source_ids"]
    if shared_building_ids != expected_shared:
        raise SystemExit(f"cross-tile stable building-key overlap changed: {shared_building_ids}")
    for source_id in shared_building_ids:
        if legacy_keys[source_id]["geometry"] != candidate_keys[source_id]["geometry"]:
            raise SystemExit(f"shared physical building geometry differs by tile: {source_id}")
    def point_key(feature):
        coordinates = feature["geometry"]["coordinates"]
        return tuple(round(float(value), 7) for value in coordinates[:2])
    legacy_points = {point_key(feature) for feature in legacy_receivers["features"]}
    candidate_points = {point_key(feature) for feature in receivers}
    coordinate_duplicates = len(legacy_points & candidate_points)
    if coordinate_duplicates != c02["provenance"]["cross_tile_receiver_coordinate_duplicates_at_1e-7_degrees"]:
        raise SystemExit(f"cross-tile receiver point overlap changed: {coordinate_duplicates}")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    outputs = {"benchmark.geojson": json.dumps(receiver_doc, separators=(",", ":"), ensure_ascii=False).encode() + b"\n", "buildings.geojson": json.dumps(building_doc, separators=(",", ":"), ensure_ascii=False).encode() + b"\n", "build-manifest.json": (json.dumps(public_manifest, indent=2, sort_keys=True) + "\n").encode()}
    expected_assets = {Path(asset["path"]).name: asset for asset in c02["assets"]}
    for name, data in outputs.items():
        expected = expected_assets[name]
        if len(data) != expected["bytes"] or sha(data) != expected["sha256"]:
            raise SystemExit(f"deterministic candidate asset differs from release contract: {name}")
        (args.output_dir / name).write_bytes(data)
    receipt = {
        "schema": "quiet_la_candidate_tile_build_receipt_v1", "status": "CANDIDATE_ASSETS_BUILT_ROOT_REVIEW_REQUIRED",
        "tile_id": "r02-c02", "inputs": {key: {"bytes": len(value), "sha256": sha(value)} for key, value in source_bytes.items()},
        "outputs": {name: {"bytes": len(value), "sha256": sha(value)} for name, value in outputs.items()},
        "counts": {"receivers": len(receivers), "numeric_rows": numeric_count, "buildings": len(building_features), "facade_receivers": len(facade), "masked_ids": [], "min_laeq": min_laeq, "max_laeq": max_laeq, "bbox_wgs84": bbox},
        "disposition": "Candidate assets are excluded from all standard stages and hosted build profiles until root review accepts their exact hashes.",
    }
    (args.output_dir / "build-receipt.json").write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    print(json.dumps(receipt, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
