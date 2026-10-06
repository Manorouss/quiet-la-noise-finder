#!/usr/bin/env python3
# Model v3 copy of implementation/work/campaign/run_phase1_regional_attempt.py (run_attempt.py --atmo selects it):
# identical except that the attempt may carry any whole number of period rows per source (one period per road
# class plus the night weather range) and the optional input/atmospheric.geojson. The v2 runner stays byte-identical
# for the attempts it authorized.
"""Execute exactly one separately-authorized Phase 1 regional attempt.

This is deliberately independent of the historical Tarzana v2 runner.  It
owns an attempt-local NoiseModelling 6.0 working directory/database and never
talks to a pre-existing server.  The authorization sidecar is the final gate
which makes the otherwise immutable Phase 1 input packages runnable.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

IMPLEMENTATION = Path(__file__).resolve().parents[4]   # this copy lives in <app>/science/pipeline, the original in implementation/work/campaign
CAMPAIGN = IMPLEMENTATION / "work" / "campaign" / "corrected_freeway_shard"
ENGINE = Path("/Volumes/NoiseModelling/NoiseModelling.app/Contents/MacOS/NoiseModelling")
PUBLIC_MAP = IMPLEMENTATION / "outputs" / "quiet-la-regional-exposure.html"
INPUT_NAMES = {"buildings.geojson", "ground.geojson", "periods.geojson", "receivers.geojson", "sources.geojson", "terrain.asc"}
SAFE_PREFIX = re.compile(r"^[A-Z][A-Z0-9_]{2,62}$")


def now() -> str: return datetime.now(timezone.utc).isoformat()


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""): h.update(block)
    return h.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict): raise ValueError(f"expected JSON object: {path}")
    return value


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def snapshot(path: Path) -> dict[str, Any]:
    canonical = str(path.resolve())
    return {"canonical_path": canonical, "exists": path.is_file(), "bytes": path.stat().st_size if path.is_file() else 0,
            "sha256": digest(path) if path.is_file() else None, "captured_at_utc": now()}


def append_event(log_path: Path, event: str, **details: Any) -> None:
    record = {"at_utc": now(), "event": event, **details}
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, sort_keys=True) + "\n"); f.flush(); os.fsync(f.fileno())


def resource_snapshot(path: Path) -> dict[str, int]:
    usage = shutil.disk_usage(path)
    return {"free_bytes": usage.free, "used_bytes": usage.used, "total_bytes": usage.total}


def contained(child: Path, parent: Path) -> bool:
    try: child.resolve().relative_to(parent.resolve()); return True
    except ValueError: return False


def feature_count(path: Path) -> int:
    value = read_json(path)
    features = value.get("features")
    if value.get("type") != "FeatureCollection" or not isinstance(features, list): raise ValueError(f"invalid GeoJSON FeatureCollection: {path}")
    return len(features)


def validate_attempt(attempt: Path, authorization: Path, runner_path: Path = Path(__file__)) -> tuple[dict[str, Any], dict[str, Path]]:
    """Validate only the generic regional schema; this function has no I/O network side effects."""
    attempt = attempt.resolve()
    manifest_path = attempt / "attempt_manifest.json"
    manifest = read_json(manifest_path)
    required = {"schema_version", "attempt_id", "region_key", "table_prefix", "counts", "inputs", "write_scope", "propagation_authorized", "public_output_writes_authorized"}
    if not required <= set(manifest): raise ValueError("attempt manifest missing required schema fields")
    if manifest["schema_version"] != 1 or not isinstance(manifest["attempt_id"], str) or not re.fullmatch(r"phase1-[a-z0-9-]+-v[1-9][0-9]*", manifest["attempt_id"]): raise ValueError("unsupported Phase 1 attempt schema")
    if not isinstance(manifest["region_key"], str) or not re.fullmatch(r"[a-z][a-z0-9_]*", manifest["region_key"]): raise ValueError("invalid region_key")
    prefix = manifest["table_prefix"]
    if not isinstance(prefix, str) or not SAFE_PREFIX.fullmatch(prefix): raise ValueError("table_prefix must be SQL-safe uppercase identifier")
    counts = manifest["counts"]
    if set(counts) != {"receivers", "sources", "period_rows"} or any(type(counts[k]) is not int or counts[k] <= 0 for k in counts) or counts["period_rows"] % counts["sources"]: raise ValueError("invalid receiver/source/period counts")
    if manifest["propagation_authorized"] is not False or manifest["public_output_writes_authorized"] is not False: raise ValueError("attempt package itself must remain propagation/public-write unauthorized")
    inputs = manifest["inputs"]
    if not isinstance(inputs, dict) or not INPUT_NAMES <= set(inputs) or not set(inputs) <= INPUT_NAMES | {"atmospheric.geojson"}: raise ValueError("unexpected input inventory")
    resolved: dict[str, Path] = {}
    for name, record in inputs.items():
        if not isinstance(record, dict) or set(record) != {"path", "sha256", "bytes"}: raise ValueError(f"invalid input record: {name}")
        path = attempt / str(record["path"])
        if not contained(path, attempt / "input") or not path.is_file() or type(record["bytes"]) is not int or record["bytes"] != path.stat().st_size or not isinstance(record["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", record["sha256"]) or digest(path) != record["sha256"]: raise ValueError(f"immutable input mismatch: {name}")
        resolved[name] = path
    if feature_count(resolved["receivers.geojson"]) != counts["receivers"] or feature_count(resolved["sources.geojson"]) != counts["sources"] or feature_count(resolved["periods.geojson"]) != counts["period_rows"]: raise ValueError("declared counts do not match inputs")
    scope = manifest["write_scope"]
    output = attempt / str(scope.get("raw_export_path_reserved_only", ""))
    if not contained(output, attempt) or output.resolve() != (attempt / "export" / "receivers_level_d_e_n.csv").resolve(): raise ValueError("export path must be the canonical attempt-local path")
    for other in CAMPAIGN.glob("*/attempt_manifest.json"):
        if other.resolve() != manifest_path.resolve() and read_json(other).get("table_prefix") == prefix: raise ValueError("duplicate table_prefix in campaign")
    auth = read_json(authorization)
    expected_auth = {"schema_version", "attempt_id", "region_key", "table_prefix", "attempt_manifest_sha256", "runner_sha256", "propagation_authorized", "public_output_writes_authorized"}
    if set(auth) != expected_auth or auth["schema_version"] != 1 or auth["attempt_id"] != manifest["attempt_id"] or auth["region_key"] != manifest["region_key"] or auth["table_prefix"] != prefix or auth["attempt_manifest_sha256"] != digest(manifest_path) or auth["runner_sha256"] != digest(runner_path.resolve()) or auth["propagation_authorized"] is not True or auth["public_output_writes_authorized"] is not False: raise ValueError("authorization sidecar does not exactly authorize this runner and manifest")
    return manifest, resolved


class AttemptLock:
    def __init__(self, path: Path) -> None: self.path, self.file = path, None
    def __enter__(self) -> "AttemptLock":
        self.path.parent.mkdir(parents=True, exist_ok=True); self.file = self.path.open("a+")
        try: fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.file.close(); raise RuntimeError(f"runtime database lock is held: {self.path}")
        self.file.seek(0); self.file.truncate(); self.file.write(json.dumps({"pid": os.getpid(), "acquired_at_utc": now()}) + "\n"); self.file.flush(); os.fsync(self.file.fileno()); return self
    def __exit__(self, *_: object) -> None:
        if self.file: fcntl.flock(self.file.fileno(), fcntl.LOCK_UN); self.file.close()


def propagation(prefix: str) -> dict[str, object]:
    """The complete accepted Tarzana v2 propagation contract, prefix excepted."""
    return {
        "tableSources": f"{prefix}_SOURCES",
        "tableSourcesEmission": "LW_ROADS",
        "tableReceivers": f"{prefix}_RECEIVERS",
        "tableBuilding": f"{prefix}_BUILDINGS",
        "tableGroundAbs": f"{prefix}_GROUND",
        "tableDEM": "DEM",
        "confMaxSrcDist": "1500",
        "confDiffHorizontal": "true",
        "confDiffVertical": "true",
        "confReflOrder": "0",
        "confMinWallReflDist": "0.0",
        "confMaxReflDist": "50.0",
        "confTemperature": "15.0",
        "confHumidity": "70.0",
        "confFavourableOccurrencesDefault": ", ".join(["0.5"] * 16),
        "confThreadNumber": "2",
        "confMaxError": "0.1",
        "frequencyFieldPrepend": "HZ",
        "confExportSourceId": "false",
        "paramWallAlpha": "0.1",
    }


def engine_command(port: int, runtime: Path) -> list[str]:
    """Launch an attempt-local, non-interactive WPS runtime.

    A fresh NoiseModelling database otherwise redirects WPS calls to the
    administrator-registration page. ``--unsecure`` is bounded to this local
    process/port and is required for unattended stock-WPS execution.
    """
    return [
        str(ENGINE),
        "--port", str(port),
        "--working-dir", str(runtime.resolve()),
        "--unsecure",
        "--browser-skip",
    ]


def wps_endpoint(port: int) -> str:
    # NoiseModelling emits localhost in statusLocation even when the request
    # was made through 127.0.0.1. Use its canonical host so provenance and the
    # local-only status-location guard agree exactly.
    return f"http://localhost:{port}/noisemodelling/builder/ows"


def execute_wps(endpoint: str, provenance: Path, events: Path, label: str, process: str, parameters: dict[str, object]) -> int:
    """Submit one request and wait for its final response before returning."""
    ns = {"wps": "http://www.opengis.net/wps/1.0.0", "ows": "http://www.opengis.net/ows/1.1"}
    root = ET.Element("{%s}Execute" % ns["wps"], {"service": "WPS", "version": "1.0.0"})
    ET.SubElement(root, "{%s}Identifier" % ns["ows"]).text = process
    inputs = ET.SubElement(root, "{%s}DataInputs" % ns["wps"])
    for key, value in parameters.items():
        item = ET.SubElement(inputs, "{%s}Input" % ns["wps"])
        ET.SubElement(item, "{%s}Identifier" % ns["ows"]).text = key
        data = ET.SubElement(item, "{%s}Data" % ns["wps"])
        ET.SubElement(data, "{%s}LiteralData" % ns["wps"]).text = str(value)
    response = ET.SubElement(root, "{%s}ResponseForm" % ns["wps"])
    ET.SubElement(response, "{%s}ResponseDocument" % ns["wps"], {"storeExecuteResponse": "true", "status": "true"})
    payload = ET.tostring(root, encoding="utf-8", xml_declaration=True); payload_path = provenance / f"request_{label}.xml"; payload_path.write_bytes(payload)
    append_event(events, "stage_request_recorded", stage=label, process=process, payload_sha256=digest(payload_path))
    request = urllib.request.Request(endpoint, data=payload, headers={"Content-Type": "text/xml"})
    with urllib.request.urlopen(request, timeout=60) as result: submitted = result.read()
    location = ET.fromstring(submitted).attrib.get("statusLocation")
    if not location or not location.startswith(endpoint.rsplit("/ows", 1)[0] + "/jobs/"): raise RuntimeError(f"{label}: invalid local WPS status location")
    job = int(location.rstrip("/").rsplit("/", 1)[-1]); append_event(events, "stage_submitted", stage=label, job=job, process=process)
    deadline = time.monotonic() + 24 * 60 * 60
    while time.monotonic() < deadline:
        with urllib.request.urlopen(location, timeout=60) as result: final = result.read()
        doc = ET.fromstring(final); status = doc.find(".//wps:Status", ns)
        if status is None or not list(status): raise RuntimeError(f"{label}: WPS response has no status")
        item = list(status)[0]; name = item.tag.rsplit("}", 1)[-1]
        if name == "ProcessSucceeded":
            final_path = provenance / f"final_{label}_job_{job}.xml"; final_path.write_bytes(final)
            append_event(events, "stage_completed", stage=label, job=job, final_sha256=digest(final_path)); return job
        if name in {"ProcessFailed", "ProcessPaused"}:
            (provenance / f"final_{label}_job_{job}_failed.xml").write_bytes(final); raise RuntimeError(f"{label}: {name}")
        time.sleep(2)
    raise TimeoutError(f"{label}: 24-hour WPS timeout")


def main_run(options: argparse.Namespace) -> Path:
    attempt = options.attempt_dir.resolve(); auth = attempt / "phase1_authorization.json"; manifest, inputs = validate_attempt(attempt, auth)
    state, events, provenance = attempt / "phase1_run_state.json", attempt / "phase1_events.jsonl", attempt / "phase1_provenance"
    if state.exists():
        prior = read_json(state).get("status", "")
        if str(prior).startswith(("completed", "failed")): raise FileExistsError(f"attempt is terminal ({prior}); never reuse it")
        raise FileExistsError("attempt already started; generic runner never resumes")
    if provenance.exists() or (attempt / "export").exists(): raise FileExistsError("attempt already has artifacts; refusing reuse")
    runtime = attempt / "runtime"; database = runtime / "server.mv.db"; lock_path = database.with_name(database.name + ".lock")
    provenance.mkdir(); write_json(state, {"status": "starting", "updated_at_utc": now()}); append_event(events, "attempt_started", resources=resource_snapshot(attempt))
    process: subprocess.Popen[bytes] | None = None; engine_log: Any = None; last_stage: str | None = None; jobs: dict[str, Any] = {}; started_at = now(); started_clock = time.monotonic(); database_before: dict[str, Any] | None = None
    try:
        with AttemptLock(lock_path):
            database_before = snapshot(database)
            append_event(events, "database_lock_acquired", lock_path=str(lock_path.resolve()), pid=os.getpid(), database_before=database_before)
            if options.port < 1024 or options.port > 65535: raise ValueError("port must be a non-privileged TCP port")
            if not ENGINE.is_file(): raise FileNotFoundError(f"pinned NoiseModelling 6.0 binary unavailable: {ENGINE}")
            command = engine_command(options.port, runtime)
            engine_log = (provenance / "engine.log").open("wb")
            process = subprocess.Popen(command, stdout=engine_log, stderr=subprocess.STDOUT)
            append_event(events, "runtime_started", pid=process.pid, command=command, runtime_directory=str(runtime.resolve()))
            # Intentionally no request is made until the local runtime is proven responsive.
            endpoint = wps_endpoint(options.port)
            deadline = time.monotonic() + options.startup_timeout
            while time.monotonic() < deadline:
                if process.poll() is not None: raise RuntimeError("NoiseModelling exited during startup")
                try:
                    with urllib.request.urlopen(endpoint + "?service=WPS&request=GetCapabilities", timeout=2) as response: capabilities = response.read().decode("utf-8", "replace")
                    break
                except OSError: time.sleep(.2)
            else: raise TimeoutError("attempt-local NoiseModelling did not become ready")
            if not all(x in capabilities for x in ("Import_and_Export:Import_File", "Import_and_Export:Import_Asc_File", "NoiseModelling:Road_Emission_from_Traffic", "NoiseModelling:Noise_level_from_source", "Import_and_Export:Export_Table")): raise RuntimeError("pinned runtime lacks required WPS processes")
            prefix = manifest["table_prefix"]
            vectors = (("import_buildings", "buildings.geojson", "BUILDINGS"), ("import_receivers", "receivers.geojson", "RECEIVERS"), ("import_ground", "ground.geojson", "GROUND"), ("import_sources", "sources.geojson", "SOURCES"), ("import_periods", "periods.geojson", "PERIODS"))
            for label, name, suffix in vectors:
                last_stage = label; write_json(state, {"status": "running", "stage": label, "updated_at_utc": now(), "wps_jobs": jobs})
                jobs[label] = execute_wps(endpoint, provenance, events, label, "Import_and_Export:Import_File", {"pathFile": str(inputs[name].resolve()), "tableName": f"{prefix}_{suffix}", "inputSRID": "26911", "ifTableExists": "Overwrite"})
            last_stage = "import_terrain"; jobs[last_stage] = execute_wps(endpoint, provenance, events, last_stage, "Import_and_Export:Import_Asc_File", {"pathFile": str(inputs["terrain.asc"].resolve()), "inputSRID": "26911", "downscale": "2"})
            last_stage = "road_emissions"; jobs[last_stage] = execute_wps(endpoint, provenance, events, last_stage, "NoiseModelling:Road_Emission_from_Traffic", {"tableRoads": f"{prefix}_PERIODS", "coefficientVersion": "2"})
            last_stage = "propagation"; jobs[last_stage] = execute_wps(endpoint, provenance, events, last_stage, "NoiseModelling:Noise_level_from_source", propagation(prefix))
            output = attempt / "export" / "receivers_level_d_e_n.csv"; output.parent.mkdir(); last_stage = "export"; jobs[last_stage] = execute_wps(endpoint, provenance, events, last_stage, "Import_and_Export:Export_Table", {"tableToExport": "(SELECT * FROM RECEIVERS_LEVEL ORDER BY IDRECEIVER, PERIOD)", "exportPath": str(output.resolve())})
            if not output.is_file() or not output.stat().st_size: raise RuntimeError("successful export stage did not produce a CSV")
            # Keep the database lock while stopping the server and handing off the final manifest.
            process.terminate()
            try: process.wait(timeout=10)
            except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=10)
            append_event(events, "runtime_stopped", pid=process.pid, returncode=process.returncode, database_after=snapshot(database), resources=resource_snapshot(attempt))
            process = None
            xmls = {path.name: {"bytes": path.stat().st_size, "sha256": digest(path)} for path in sorted(provenance.glob("*.xml"))}
            expected_labels = {"import_buildings", "import_receivers", "import_ground", "import_sources", "import_periods", "import_terrain", "road_emissions", "propagation", "export"}
            if set(jobs) != expected_labels or len(xmls) not in (18, 20) or any(f"request_{label}.xml" not in xmls for label in expected_labels) or any(not any(name.startswith(f"final_{label}_job_") and name.endswith(".xml") for name in xmls) for label in expected_labels):
                raise RuntimeError("incomplete nine-stage (plus optional atmospheric) WPS provenance")
            artifacts = {name: {"path": str(path.resolve()), "bytes": path.stat().st_size, "sha256": digest(path)} for name, path in inputs.items()}
            artifacts["export"] = {"path": str(output.resolve()), "bytes": output.stat().st_size, "sha256": digest(output)}
            artifacts["engine_binary"] = {"path": str(ENGINE.resolve()), "bytes": ENGINE.stat().st_size, "sha256": digest(ENGINE)}
            write_json(attempt / "phase1_run_manifest.json", {"schema_version": 1, "attempt_id": manifest["attempt_id"], "region_key": manifest["region_key"], "table_prefix": prefix, "status": "completed_export_unvalidated", "engine_version": "NoiseModelling 6.0.0", "endpoint": endpoint, "runtime_port": options.port, "authorization_sha256": digest(auth), "attempt_manifest_sha256": digest(attempt / "attempt_manifest.json"), "frozen_public_sha256": digest(PUBLIC_MAP), "artifacts": artifacts, "wps_jobs": jobs, "wps_xml": xmls, "database_before": database_before, "database_after": snapshot(database), "lock_path": str(lock_path.resolve()), "runtime_pid": None, "started_at_utc": started_at, "ended_at_utc": now(), "elapsed_seconds": round(time.monotonic() - started_clock, 3), "resources": resource_snapshot(attempt), "public_output_writes_authorized": False})
            write_json(state, {"status": "completed_export_unvalidated", "updated_at_utc": now(), "wps_jobs": jobs})
            append_event(events, "run_manifest_handed_off", path=str((attempt / "phase1_run_manifest.json").resolve()))
            return output
    except Exception as error:
        write_json(state, {"status": "failed", "updated_at_utc": now(), "last_completed_stage": last_stage, "wps_jobs": jobs, "error": f"{type(error).__name__}: {error}"}); append_event(events, "attempt_failed", error=f"{type(error).__name__}: {error}")
        raise
    finally:
        if process:
            process.terminate()
            try: process.wait(timeout=10)
            except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=10)
            append_event(events, "runtime_stopped", pid=process.pid, returncode=process.returncode, database_after=snapshot(database), resources=resource_snapshot(attempt))
        if engine_log:
            engine_log.close()
    raise AssertionError("unreachable")


def arguments() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__); p.add_argument("--attempt-dir", required=True, type=Path); p.add_argument("--port", required=True, type=int); p.add_argument("--startup-timeout", type=float, default=30); return p.parse_args()


if __name__ == "__main__":
    try: main_run(arguments())
    except Exception as error: print(f"PHASE1 REFUSED/FAILED: {type(error).__name__}: {error}"); raise SystemExit(2)
