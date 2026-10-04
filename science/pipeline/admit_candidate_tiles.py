#!/usr/bin/env python3
"""Copy built candidate tiles into the pilot release and register them.

For each tile directory produced by build_tile_assets.py, copies
benchmark.geojson / buildings.geojson / build-manifest.json into
src/data/pilot-release-v1/tiles/<tile>/ and adds (or replaces) its entry in
src/data/pilot-release-contract.json with counts, masks, asset hashes and
provenance, so stage-pilot-local-data / verify / the portal accept it.
This only changes local release data; deploying stays a separate, owner-approved step.

Usage:
  admit_candidate_tiles.py --candidates <out-root> --tiles r02-c04 r02-c05 ...
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

APP = Path(__file__).resolve().parents[2]
RELEASE = APP / "src/data/pilot-release-v1"
CONTRACT = APP / "src/data/pilot-release-contract.json"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--tiles", nargs="+", required=True)
    args = parser.parse_args()
    contract = json.loads(CONTRACT.read_text())
    for tile_id in args.tiles:
        src = args.candidates / tile_id
        manifest = json.loads((src / "build-manifest.json").read_text())
        receipt = json.loads((src / "build-receipt.json").read_text())
        dst = RELEASE / "tiles" / tile_id
        dst.mkdir(parents=True, exist_ok=True)
        assets = []
        for kind, name in (("receivers", "benchmark.geojson"), ("buildings", "buildings.geojson"), ("manifest", "build-manifest.json")):
            shutil.copyfile(src / name, dst / name)
            assets.append({"kind": kind, "path": f"tiles/{tile_id}/{name}", "sha256": sha(dst / name), "bytes": (dst / name).stat().st_size})
        shared = json.loads((src / "buildings.geojson").read_text())["metadata"].get("shared_building_source_ids", [])
        entry = {
            "tile_id": tile_id,
            "status": "accepted_expansion",
            "model": manifest.get("study_model", "tarzana-pilot"),
            "source_attempt": manifest["attempt_id"],
            "bbox_wgs84": manifest["bbox_wgs84"],
            "receiver_count": manifest["receiver_count"],
            "numeric_rows": manifest["numeric_rows"],
            "building_count": manifest["building_count"],
            "facade_receiver_count": manifest["facade_receiver_count"],
            "receiver_height_counts_m": manifest["receiver_height_counts_m"],
            "masked_ids": manifest["masked_ids"],
            "source_csv_sha256": manifest["source_csv_sha256"],
            "provenance": {"attempt_id": manifest["attempt_id"], "builder": "science/pipeline/build_tile_assets.py",
                           "cross_tile_duplicate_building_source_ids": shared, "build_receipt_outputs": receipt["outputs"]},
            "quality_flags": manifest["quality_flags"],
            "assets": assets,
        }
        tiles = [t for t in contract["tiles"] if t["tile_id"] != tile_id]
        contract["tiles"] = tiles + [entry]
        print(f"admitted {tile_id}: {entry['receiver_count']} receivers, {entry['building_count']} buildings, {len(entry['masked_ids'])} masked")
    CONTRACT.write_text(json.dumps(contract, indent=2, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
