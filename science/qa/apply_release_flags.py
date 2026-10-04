#!/usr/bin/env python3
"""Apply physical-ceiling masks and on-road labels to one released pilot tile.

Receivers above the physical ceiling (see physical_ceiling.py) in any period
become masked: geometry and identity stay, D/E/N become null. Receivers within
--on-road-m of a modeled road centerline keep their values and gain
`on_road: true`, because a point on the carriageway is not a living location.
The tile manifest, receiver metadata and release contract (counts, masks,
asset hashes) are updated so the existing stage/verify checks pass.

Usage:
  apply_release_flags.py --tile r02-c02 --attempt <engine attempt dir> [--write]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import physical_ceiling as pc  # noqa: E402

APP = Path(__file__).resolve().parents[2]
CONTRACT = APP / "src/data/pilot-release-contract.json"
RELEASE = APP / "src/data/pilot-release-v1"


def road_distance(xy: np.ndarray, segments: np.ndarray) -> np.ndarray:
    best = np.full(len(xy), np.inf)
    for ax, ay, bx, by in segments:
        dx, dy = bx - ax, by - ay
        length2 = dx * dx + dy * dy
        t = np.clip(((xy[:, 0] - ax) * dx + (xy[:, 1] - ay) * dy) / length2, 0, 1) if length2 else 0
        best = np.minimum(best, np.hypot(xy[:, 0] - ax - t * dx, xy[:, 1] - ay - t * dy))
    return best


def dump(path: Path, value, compact: bool) -> bytes:
    text = json.dumps(value, separators=(",", ":"), ensure_ascii=False) if compact else json.dumps(value, indent=2, ensure_ascii=False)
    data = (text + "\n").encode()
    path.write_bytes(data)
    return data


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--tile", required=True)
    parser.add_argument("--attempt", type=Path, required=True, help="engine attempt with input/periods.geojson and input/receivers.geojson")
    parser.add_argument("--margin-db", type=float, default=6.0)
    parser.add_argument("--on-road-m", type=float, default=3.0)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()

    contract = json.loads(CONTRACT.read_text())
    tile = next(t for t in contract["tiles"] if t["tile_id"] == args.tile)
    assets = {a["kind"]: a for a in tile["assets"]}
    receivers_path, manifest_path = RELEASE / assets["receivers"]["path"], RELEASE / assets["manifest"]["path"]
    doc = json.loads(receivers_path.read_text())

    ids, xy = pc.load_receivers(args.attempt / "input/receivers.geojson")
    ceiling_flags: set[int] = set()
    for period in ("D", "E", "N"):
        segments, powers = pc.load_sources(args.attempt / "input/periods.geojson", period)
        values = pc.load_values(receivers_path, period)
        ceiling = pc.ceiling_levels(xy, segments, powers)
        ceiling_flags |= {int(i) for i, c in zip(ids, ceiling) if int(i) in values and values[int(i)] - c > args.margin_db}
    on_road = {int(i) for i, d in zip(ids, road_distance(xy, segments)) if d <= args.on_road_m}

    masked, labelled = [], 0
    for feature in doc["features"]:
        p = feature["properties"]
        if p["id"] in ceiling_flags and not p["masked"]:
            p.update(masked=True, D=None, E=None, N=None)
        if p["masked"]:
            masked.append(p["id"])
            p.pop("on_road", None)
        elif p["id"] in on_road:
            p["on_road"] = True
            labelled += 1
    masked.sort()
    levels = [p[k]["laeq"] for f in doc["features"] for k in ("D", "E", "N") if (p := f["properties"])[k]]
    numeric_rows = 3 * (len(doc["features"]) - len(masked))

    summary = {
        "tile": args.tile,
        "ceiling_masked_new": sorted(ceiling_flags),
        "masked_total": len(masked),
        "on_road_labelled": labelled,
        "numeric_rows": numeric_rows,
        "laeq_range": [round(min(levels), 3), round(max(levels), 3)],
    }
    print(json.dumps(summary, indent=1))
    if not args.write:
        return 0

    quality = {
        "physical_ceiling_margin_db": args.margin_db,
        "physical_ceiling_masked_ids": sorted(ceiling_flags),
        "on_road_distance_m": args.on_road_m,
        "on_road_count": labelled,
        "method": "science/qa/physical_ceiling.py and apply_release_flags.py",
    }
    meta = doc.get("metadata") or {}
    meta["masked_ids"] = masked
    meta["numeric_rows"] = numeric_rows
    if "numeric_laeq_max" in meta:
        meta["numeric_laeq_min"], meta["numeric_laeq_max"] = min(levels), max(levels)
    meta["quality_flags"] = quality
    doc["metadata"] = meta
    receivers_bytes = dump(receivers_path, doc, compact=True)

    manifest = json.loads(manifest_path.read_text())
    manifest["masked_ids"] = masked
    manifest["numeric_rows"] = numeric_rows
    if "max_laeq" in manifest:
        manifest["min_laeq"], manifest["max_laeq"] = min(levels), max(levels)
    manifest_bytes = dump(manifest_path, manifest, compact=False)

    tile["masked_ids"] = masked
    tile["numeric_rows"] = numeric_rows
    tile["quality_flags"] = quality
    for kind, data in (("receivers", receivers_bytes), ("manifest", manifest_bytes)):
        assets[kind]["sha256"] = hashlib.sha256(data).hexdigest()
        assets[kind]["bytes"] = len(data)
    dump(CONTRACT, contract, compact=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
