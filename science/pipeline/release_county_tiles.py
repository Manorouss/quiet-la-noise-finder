#!/usr/bin/env python3
"""Build portal assets for every computed county tile, then the county map layers.

Finds each completed county run (phase1-county-*-g20a50f10-nv-*-v<N> with run_host.json),
keeps the newest completed run per 1 km cell, and runs build_tile_assets.py --study
county-v1 for cells whose assets are missing or came from another run. Tile ids are
cty-e<X>-n<Y> from the cell. Then build_county_layers.py turns all assets into PMTiles.
Safe to rerun: finished tiles are skipped.

Usage:
  release_county_tiles.py [--assets <dir>] [--layers <dir>] [--jobs 3] [--no-layers]
"""
from __future__ import annotations

import argparse
import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[4]
WORK = PROJECT / "implementation/work"
ATTEMPTS = WORK / "campaign/county_v1/attempts"
LAYOUT = "g20a50f10"


def completed_runs() -> dict[str, Path]:
    best: dict[str, tuple[float, Path]] = {}
    for run in ATTEMPTS.glob(f"phase1-county-*-{LAYOUT}-nv-*-v*"):
        done = run / "run_host.json"
        if not done.exists():
            continue
        tile = json.loads((run / "attempt_manifest.json").read_text())["tile"]
        if tile["x0"] % 1000 or tile["y0"] % 1000:
            continue
        tile_id = f"cty-e{int(tile['x0']) // 1000}-n{int(tile['y0']) // 1000}"
        stamp = done.stat().st_mtime
        if tile_id not in best or stamp > best[tile_id][0]:
            best[tile_id] = (stamp, run)
    return {tile_id: run for tile_id, (_, run) in sorted(best.items())}


def build(tile_id: str, run: Path, assets: Path) -> str:
    manifest = assets / tile_id / "build-manifest.json"
    if manifest.exists() and json.loads(manifest.read_text()).get("attempt_id") == run.name:
        return "skip"
    log = assets / f"{tile_id}.log"
    result = subprocess.run(["python3", str(HERE / "build_tile_assets.py"), "--tile", tile_id, "--attempt", str(run),
                             "--out-root", str(assets), "--study", "county-v1"], capture_output=True, text=True)
    log.write_text(result.stdout + result.stderr)
    return "built" if result.returncode == 0 else "FAILED"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--assets", type=Path, default=WORK / "pipeline_release/county_all")
    parser.add_argument("--layers", type=Path, default=WORK / "county_layers/current")
    parser.add_argument("--jobs", type=int, default=3)
    parser.add_argument("--no-layers", action="store_true")
    args = parser.parse_args()
    args.assets.mkdir(parents=True, exist_ok=True)
    runs = completed_runs()
    with ThreadPoolExecutor(args.jobs) as pool:
        results = dict(zip(runs, pool.map(lambda item: build(item[0], item[1], args.assets), runs.items())))
    for state in ("built", "skip", "FAILED"):
        names = [t for t, r in results.items() if r == state]
        print(f"{state}: {len(names)}" + (f" {' '.join(names)}" if state != "skip" and names else ""))
    if not args.no_layers:
        subprocess.run([str(HERE / "geo-python"), str(HERE / "build_county_layers.py"), "--tiles-root", str(args.assets),
                        "--out", str(args.layers)], check=True)
    return 1 if "FAILED" in results.values() else 0


if __name__ == "__main__":
    raise SystemExit(main())
