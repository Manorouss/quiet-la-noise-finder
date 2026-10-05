#!/usr/bin/env python3
"""Railway noise for one prepared rail attempt, with the same engine and propagation settings as the road tiles.

An attempt folder holds input/ (buildings.geojson, receivers.geojson, ground.geojson, optional terrain.asc,
rail_sections.geojson, rail_traffic.csv), data/ (vehicles.json, trainsets.json; rail_vehicles.py) and
rail_manifest.json ({"table_prefix": ...}). Steps, all through the engine's WPS (Mac, patched hf_v1 build as
run_attempt.py):

  1. import the tables; Railway_Emission_from_Traffic -> LW_RAILWAY (CNOSSOS-EU, third octaves, D/E/N)
  2. export LW_RAILWAY and correct the source heights (and add the horn sources of input/horn_sources.geojson,
     horns.py): NoiseModelling 6.0 writes the rolling-noise source
     at 4 m (EmissionTableGenerator, "heightSource = 4" for ROLLING); CNOSSOS-EU puts it on the low source
     A with traction A and aerodynamic A, which NoiseModelling places at 0.5 m. Re-import as the sources.
  3. Noise_level_from_source with the road propagation contract (run_attempt.py: vertical diffraction off,
     no reflections, 1500 m) and the built-in CNOSSOS train directivity; export RECEIVERS_LEVEL.

  run_rail_attempt.py <attempt dir> [--port 9140] [--threads 2] [--terrain-downscale 2]
"""
from __future__ import annotations

import argparse
import functools
import http.server
import json
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "pipeline"))
import run_attempt as RA  # noqa: E402

sys.path.insert(0, str(RA.RUNNER.parent))
import run_phase1_regional_attempt as runner  # noqa: E402

ROLLING_DIR_ID, LOW_SOURCE_M = 1, 0.5


def fix_heights(exported: Path, out: Path) -> dict:
    doc = json.loads(exported.read_text())
    changed = 0
    for f in doc["features"]:
        if int(f["properties"].get("DIR_ID", 0)) != ROLLING_DIR_ID:
            continue
        geom = f["geometry"]
        lines = [geom["coordinates"]] if geom["type"] == "LineString" else geom["coordinates"]
        for line in lines:
            for point in line:
                if len(point) > 2:
                    point[2] = LOW_SOURCE_M
                else:
                    point.append(LOW_SOURCE_M)
        changed += 1
    doc.setdefault("crs", {"type": "name", "properties": {"name": "EPSG:26911"}})
    out.write_text(json.dumps(doc))
    return {"sources": len(doc["features"]), "rolling_sources_lowered": changed}


def add_horns(horns: Path, sources: Path) -> int:
    """Append the horn line sources (horns.py; prepared with the inputs) to the corrected rail sources."""
    if not horns.exists():
        return 0
    doc = json.loads(sources.read_text())
    extra = json.loads(horns.read_text())["features"]
    pk = max((int(f["properties"]["PK"]) for f in doc["features"]), default=0)
    for f in extra:
        pk += 1
        f["properties"]["PK"] = pk
        doc["features"].append(f)
    sources.write_text(json.dumps(doc))
    return len(extra)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("attempt", type=Path)
    parser.add_argument("--port", type=int, default=9140)
    parser.add_argument("--threads", type=int, default=2)
    parser.add_argument("--terrain-downscale", type=int, default=2)
    args = parser.parse_args()
    attempt = args.attempt.resolve()
    prefix = json.loads((attempt / "rail_manifest.json").read_text())["table_prefix"]
    inp, data, export, runtime = attempt / "input", attempt / "data", attempt / "export", attempt / "runtime"
    if export.exists():
        raise SystemExit("attempt already has exports; prepare a new attempt instead of reusing it")
    export.mkdir()
    provenance = attempt / "provenance"
    provenance.mkdir(exist_ok=True)
    events = attempt / "events.jsonl"
    runtime.mkdir(exist_ok=True)
    command = [str(RA.MAC_JAVA), "-cp", f"{RA.HF_OVERLAY}:{RA.HELPER}:{RA.MAC_LIB}/*", RA.MAIN, "--port", str(args.port),
               "--working-dir", str(runtime), "--unsecure", "--browser-skip"]
    log = (provenance / "engine.log").open("wb")
    # cwd = runtime: the engine loads its WPS scripts from ./scripts if that folder exists (the app has one).
    engine = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, cwd=str(runtime))
    endpoint = runner.wps_endpoint(args.port)
    wps = lambda label, process, params: runner.execute_wps(endpoint, provenance, events, label, process, params)
    started = time.time()
    try:
        deadline = time.monotonic() + 120
        while True:
            if engine.poll() is not None:
                raise RuntimeError("engine exited during startup (port in use?)")
            try:
                capabilities = urllib.request.urlopen(endpoint + "?service=WPS&request=GetCapabilities", timeout=2).read().decode("utf-8", "replace")
                if all(p in capabilities for p in ("Import_and_Export:Import_File", "NoiseModelling:Railway_Emission_from_Traffic",
                                                    "NoiseModelling:Noise_level_from_source", "Import_and_Export:Export_Table")):
                    break  # (the engine answers before its scripts are loaded)
                if time.monotonic() > deadline:
                    raise TimeoutError("engine started without the needed WPS processes")
                time.sleep(0.5)
            except OSError:
                if time.monotonic() > deadline:
                    raise TimeoutError("engine did not start")
                time.sleep(0.5)
        for name, table in (("buildings.geojson", "BUILDINGS"), ("receivers.geojson", "RECEIVERS"), ("ground.geojson", "GROUND"),
                            ("rail_sections.geojson", "RAIL_SECTIONS"), ("rail_traffic.csv", "RAIL_TRAFFIC")):
            wps(f"import_{table.lower()}", "Import_and_Export:Import_File",
                {"pathFile": str(inp / name), "tableName": f"{prefix}_{table}", "inputSRID": "26911", "ifTableExists": "Overwrite"})
        has_dem = (inp / "terrain.asc").exists()
        if has_dem:
            wps("import_terrain", "Import_and_Export:Import_Asc_File", {"pathFile": str(inp / "terrain.asc"), "inputSRID": "26911", "downscale": str(args.terrain_downscale)})
        # The custom vehicle / train-set files: NoiseModelling 6.0 accepts file: URIs but opens them with an HTTP
        # client ("invalid URI scheme file"), so they are served on 127.0.0.1 for the emission step.
        handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(data))
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            wps("rail_emission", "NoiseModelling:Railway_Emission_from_Traffic",
                {"tableRailwayTrack": f"{prefix}_RAIL_SECTIONS", "tableRailwayTraffic": f"{prefix}_RAIL_TRAFFIC",
                 "vehicleDataFile": f"{base}/vehicles.json", "trainSetDataFile": f"{base}/trainsets.json"})
        finally:
            server.shutdown()
        wps("export_emission", "Import_and_Export:Export_Table", {"tableToExport": "LW_RAILWAY", "exportPath": str(export / "lw_railway_engine.geojson")})
        heights = fix_heights(export / "lw_railway_engine.geojson", inp / "rail_sources.geojson")
        heights["horn_sources"] = add_horns(inp / "horn_sources.geojson", inp / "rail_sources.geojson")
        wps("import_sources", "Import_and_Export:Import_File",
            {"pathFile": str(inp / "rail_sources.geojson"), "tableName": f"{prefix}_RAILSRC", "inputSRID": "26911", "ifTableExists": "Overwrite"})
        params = dict(runner.propagation(prefix))
        params.pop("tableSourcesEmission", None)
        params.update(tableSources=f"{prefix}_RAILSRC", confThreadNumber=str(args.threads), confMaxError="0.0", confDiffVertical="false")
        if not has_dem:
            params.pop("tableDEM", None)
        # NoiseModelling 6.0 can write a receiver twice when it lies exactly on the edge of two of its computation
        # cells ("Unique index or primary key violation" on RECEIVERS_LEVEL); the cells follow the source extent and
        # the maximum distance, so a retry with 1499.7 / 1500.3 m moves the edges (no visible change in levels).
        for attempt_no, reach in enumerate(("1500", "1499.7", "1500.3")):
            params["confMaxSrcDist"] = reach
            try:
                wps("propagation" if attempt_no == 0 else f"propagation_retry{attempt_no}", "NoiseModelling:Noise_level_from_source", params)
                break
            except RuntimeError:
                if attempt_no == 2 or "Unique index or primary key violation" not in (provenance / "engine.log").read_text(errors="replace"):
                    raise
        wps("export_levels", "Import_and_Export:Export_Table",
            {"tableToExport": "(SELECT * FROM RECEIVERS_LEVEL ORDER BY IDRECEIVER, PERIOD)", "exportPath": str(export / "receivers_level_rail.csv")})
        (attempt / "rail_run.json").write_text(json.dumps({"status": "completed", "seconds": round(time.time() - started), "heights": heights,
                                                           "propagation": params, "terrain_downscale": args.terrain_downscale if has_dem else None}, indent=1))
    finally:
        engine.terminate()
        try:
            engine.wait(timeout=15)
        except subprocess.TimeoutExpired:
            engine.kill()
    print(json.dumps({"attempt": attempt.name, "seconds": round(time.time() - started), **heights}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
