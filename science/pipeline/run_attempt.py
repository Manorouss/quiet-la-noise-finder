#!/usr/bin/env python3
"""Run one NoiseModelling attempt on the Mac or on the Windows PC.

Stages a fresh attempt from an existing one (same immutable inputs, new id),
then drives the unchanged Phase 1 runner (implementation/work/campaign/
run_phase1_regional_attempt.py). Only the engine launch, thread count,
diffraction flags and file paths are swapped:

  --host mac  engine runs locally on overlay:loopback-helper:installed-lib
  --host pc   inputs are copied to D:\\quietla\\attempts\\<id>, the engine runs
              on the PC (identical jars, Temurin 21.0.7) behind an SSH port
              forward, and the export CSV is copied back.

A rerun of the same source and label stages the next free -v<N>; existing
attempts are never overwritten. While implementation/work/pipeline_control/
pause-<host> exists the runner holds between WPS polls (see PausableClock),
so compute_dashboard.py can freeze and later resume the engine.

Usage:
  run_attempt.py --source-attempt <dir> --label t16 --host pc --threads 16 --port 9101
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[5]
WORK = PROJECT / "implementation/work"
CAMPAIGN = WORK / "campaign"
CONTROL = WORK / "pipeline_control"
RUNNER = CAMPAIGN / "run_phase1_regional_attempt.py"
RUNNER_V3 = Path(__file__).resolve().parent / "phase1_runner_v3.py"   # --atmo (model v3): class periods and the atmospheric table
# The engine loads its WPS scripts from ./scripts of its working directory when that folder exists (else from its jar):
# model v3 runs start it there, where the stock scripts sit next to Quietla/Atmospheric_Settings.groovy.
ENGINE_SCRIPTS = WORK / "engine_scripts_v3"
HELPER = CAMPAIGN / "noisemodelling_loopback_launcher/noisemodelling-loopback-launcher.jar"
HF_OVERLAY = CAMPAIGN / "tarzana_full_mixed_road_v1/sentinel_forensics/r02_c04_source_diagnostic_v1/engine_overlay_hf_v1/runtime_overlay/classes"
MAC_LIB = Path("/Volumes/NoiseModelling/NoiseModelling.app/Contents/app/lib")
MAC_JAVA = Path("/Library/Java/JavaVirtualMachines/temurin-21.jdk/Contents/Home/bin/java")
MAIN = "org.quietla.noisemodelling.LoopbackNoiseModellingServer"

SSH_CONFIG = Path.home() / ".ssh/quietla_pc_config"
PC = "quietla-pc"
PC_ROOT = "D:/quietla"
# Rented Linux hosts (--host <name>, any name but mac/pc): an entry "Host <name>" in ~/.ssh/quietla_cloud_config,
# provisioned by science/pipeline/cloud/provision_host.sh into CLOUD_ROOT (java 21, engine jars, helper, overlay, v3 scripts).
CLOUD_SSH_CONFIG = Path.home() / ".ssh/quietla_cloud_config"
CLOUD_ROOT = "/opt/quietla"
HOST_GONE = 75   # exit status when the rented host stopped answering (the worker re-queues the tile instead of failing it)
PC_ENGINE_SCRIPTS = f"{PC_ROOT}/engine_scripts_v3"   # copy of ENGINE_SCRIPTS on the PC (scp -r)
PC_JAVA = f"{PC_ROOT}/jdk21/jdk-21.0.7+6/bin/java.exe"
PC_LIB = f"{PC_ROOT}/nm-lib"
PC_HELPER = f"{PC_ROOT}/helper/noisemodelling-loopback-launcher.jar"
PC_OVERLAY = f"{PC_ROOT}/overlays/hf_v1"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def ssh(command: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["ssh", "-F", str(SSH_CONFIG), PC, command], capture_output=True, text=True, check=check)


def win(path: str) -> str:
    return path.replace("/", "\\")


def cssh(host: str, command: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["ssh", "-F", str(CLOUD_SSH_CONFIG), "-o", "BatchMode=yes", host, command], capture_output=True, text=True, check=check, stdin=subprocess.DEVNULL)


def host_up(host: str) -> bool:
    return subprocess.run(["ssh", "-F", str(CLOUD_SSH_CONFIG), "-o", "ConnectTimeout=10", "-o", "BatchMode=yes", host, "true"],
                          capture_output=True, stdin=subprocess.DEVNULL).returncode == 0


def wait_for_pc(reason: str, limit_s: int = 12 * 3600) -> bool:
    """Block until the PC answers over SSH (it may be rebooting for Windows Update); False after limit_s."""
    waited = 0
    while subprocess.run(["ssh", "-F", str(SSH_CONFIG), "-o", "ConnectTimeout=10", "-o", "BatchMode=yes", PC, "echo ok"],
                         capture_output=True, stdin=subprocess.DEVNULL).returncode != 0:
        if waited == 0:
            print(f"PC unreachable ({reason}); waiting for it to come back", file=sys.stderr, flush=True)
        if waited >= limit_s:
            return False
        time.sleep(30)
        waited += 30
    if waited:
        print(f"PC reachable again after {waited} s", file=sys.stderr, flush=True)
    return True


def stage(source: Path, label: str) -> Path:
    manifest = json.loads((source / "attempt_manifest.json").read_text())
    base = f"{manifest['attempt_id'].rsplit('-v', 1)[0]}-{label}"
    version = 1
    while (source.parent / f"{base}-v{version}").exists():
        version += 1
    attempt_id = f"{base}-v{version}"
    target = source.parent / attempt_id
    (target / "input").mkdir(parents=True)
    for record in manifest["inputs"].values():
        shutil.copy2(source / record["path"], target / record["path"])
    manifest["attempt_id"] = attempt_id
    manifest["region_key"] = f"{manifest['region_key']}_{label.replace('-', '_')}"
    manifest["table_prefix"] = f"{manifest['table_prefix']}_{label.upper().replace('-', '_')}"[:56]
    manifest["staged_from"] = {"attempt": source.name, "manifest_sha256": sha(source / "attempt_manifest.json")}
    (target / "attempt_manifest.json").write_text(json.dumps(manifest, indent=1) + "\n")
    auth = {
        "schema_version": 1, "attempt_id": attempt_id, "region_key": manifest["region_key"],
        "table_prefix": manifest["table_prefix"], "attempt_manifest_sha256": sha(target / "attempt_manifest.json"),
        "runner_sha256": sha(RUNNER), "propagation_authorized": True, "public_output_writes_authorized": False,
    }
    (target / "phase1_authorization.json").write_text(json.dumps(auth, indent=1) + "\n")
    return target


class PausableClock:
    """Stands in for the runner's time module while a host can be paused.

    sleep() is where the runner waits between WPS polls, so no request is in
    flight there. While pause-<host> or frozen-<host>-<port> exists it writes
    held-<host>-<port> (the dashboard freezes the engine only after seeing it)
    and waits; monotonic() leaves the held time out so no runner timeout fires.
    """

    def __init__(self, host: str, port: int):
        self.pause = CONTROL / f"pause-{host}"
        self.frozen = CONTROL / f"frozen-{host}-{port}"
        self.held = CONTROL / f"held-{host}-{port}"
        self.held_seconds = 0.0

    def __getattr__(self, name: str):
        return getattr(time, name)

    def monotonic(self) -> float:
        return time.monotonic() - self.held_seconds

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)
        if not (self.pause.exists() or self.frozen.exists()):
            return
        start = time.monotonic()
        self.held.write_text(f"{os.getpid()}\n")
        try:
            while self.pause.exists() or self.frozen.exists():
                time.sleep(1)
        finally:
            self.held.unlink(missing_ok=True)
            self.held_seconds += time.monotonic() - start


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--source-attempt", type=Path, required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--host", default="mac", help="mac, pc, or the name of a rented Linux host (~/.ssh/quietla_cloud_config)")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--threads", type=int, default=8)
    parser.add_argument("--no-horizontal", action="store_true", help="disable diffraction over horizontal edges (roofs, terrain)")
    parser.add_argument("--no-vertical", action="store_true",
                        help="disable diffraction around vertical edges (lateral paths); CNOSSOS-EU / NoiseModelling: off for road sources")
    parser.add_argument("--max-error-db", default="0.0", help="NoiseModelling confMaxError source pruning")
    parser.add_argument("--atmo", action="store_true",
                        help="import input/atmospheric.geojson (per-period favourable share, temperature, humidity) and use it in the propagation")
    parser.add_argument("--refl-order", type=int, default=None, help="confReflOrder override (reflections off by default)")
    parser.add_argument("--refl-dist", type=float, default=None, help="confMaxReflDist override in metres")
    parser.add_argument("--terrain-downscale", type=int, default=None, help="Import_Asc_File downscale override (runner default 2)")
    parser.add_argument("--keep-runtime", action="store_true",
                        help="keep the engine database (by default it is deleted after a successful run; exports and manifests stay)")
    args = parser.parse_args()
    if args.atmo:
        global RUNNER
        RUNNER = RUNNER_V3

    # A PC that is down (e.g. a Windows Update restart) must not burn through the queue: wait before
    # staging, and after a failure wait until it is back so the worker's single retry can succeed.
    if args.host == "pc" and not wait_for_pc("before staging"):
        raise SystemExit("PC unreachable for 12 h")
    cloud = args.host not in ("mac", "pc")
    if cloud and not host_up(args.host):
        print(f"{args.host} does not answer", file=sys.stderr)
        raise SystemExit(HOST_GONE)
    try:
        return run(args)
    except BaseException:
        if args.host == "pc":
            wait_for_pc("after a failed run")
        if cloud and not host_up(args.host):
            # a Spot machine was reclaimed (or the network dropped): not a failure of the tile
            print(f"{args.host} stopped answering during the run", file=sys.stderr)
            raise SystemExit(HOST_GONE)
        raise


def run(args: argparse.Namespace) -> int:
    attempt = stage(args.source_attempt.resolve(), args.label)
    cloud = args.host not in ("mac", "pc")
    pc_attempt = f"{PC_ROOT}/attempts/{attempt.name}"
    cloud_attempt = f"{CLOUD_ROOT}/attempts/{attempt.name}"
    # An engine left over from a run whose Mac side died (lid closed, network gone) still holds the port: clear it first.
    if args.host == "pc":
        ssh(f'powershell -NoProfile -Command "Get-NetTCPConnection -LocalPort {args.port} -State Listen -ErrorAction SilentlyContinue | ForEach-Object {{ Stop-Process -Id $_.OwningProcess -Force }}"', check=False)
    elif cloud:
        cssh(args.host, f"pkill -f -- '--port {args.port} --working-dir' || true", check=False)
    if args.host == "pc":
        ssh(f'powershell -NoProfile -Command "New-Item -ItemType Directory -Force {win(pc_attempt)}\\input,{win(pc_attempt)}\\export | Out-Null"')
        subprocess.run(["scp", "-F", str(SSH_CONFIG), "-q", *[str(p) for p in sorted((attempt / "input").iterdir())],
                        f"{PC}:{pc_attempt}/input/"], check=True)
    elif cloud:
        cssh(args.host, f"mkdir -p {cloud_attempt}/input {cloud_attempt}/export {cloud_attempt}/runtime")
        subprocess.run(["scp", "-F", str(CLOUD_SSH_CONFIG), "-q", *[str(p) for p in sorted((attempt / "input").iterdir())],
                        f"{args.host}:{cloud_attempt}/input/"], check=True, stdin=subprocess.DEVNULL)

    sys.path.insert(0, str(RUNNER.parent))
    runner = importlib.import_module(RUNNER.stem)

    def engine_command(port: int, runtime: Path) -> list[str]:
        runtime.mkdir(parents=True, exist_ok=True)
        if args.host == "mac":
            classpath = f"{HF_OVERLAY}:{HELPER}:{MAC_LIB}/*"
            return [str(MAC_JAVA), "-cp", classpath, MAIN, "--port", str(port), "--working-dir", str(runtime.resolve()),
                    "--unsecure", "--browser-skip"]
        if cloud:
            classpath = f"{CLOUD_ROOT}/overlays/hf_v1:{CLOUD_ROOT}/helper/noisemodelling-loopback-launcher.jar:{CLOUD_ROOT}/nm-lib/*"
            remote = (f"cd {CLOUD_ROOT}/engine_scripts_v3 && exec java -cp '{classpath}' {MAIN} --port {port} "
                      f"--working-dir {cloud_attempt}/runtime --unsecure --browser-skip")
            return ["ssh", "-F", str(CLOUD_SSH_CONFIG), "-o", "ExitOnForwardFailure=yes", "-o", "BatchMode=yes",
                    "-L", f"{port}:127.0.0.1:{port}", args.host, remote]
        classpath = ";".join([win(PC_OVERLAY), win(PC_HELPER), win(PC_LIB) + "\\*"])
        remote = f'"{win(PC_JAVA)}" -cp "{classpath}" {MAIN} --port {port} --working-dir "{win(pc_attempt)}\\runtime" --unsecure --browser-skip'
        if args.atmo:
            remote = f"cd /d {win(PC_ENGINE_SCRIPTS)} && " + remote
        return ["ssh", "-F", str(SSH_CONFIG), "-o", "ExitOnForwardFailure=yes", "-L", f"{port}:127.0.0.1:{port}", PC, remote]

    original_propagation = runner.propagation

    def propagation(prefix: str) -> dict[str, object]:
        values = dict(original_propagation(prefix))
        values.update(confMaxError=str(args.max_error_db), confThreadNumber=str(args.threads))
        if args.atmo:
            values["tablePeriodAtmosphericSettings"] = f"{prefix}_ATMO"
        if args.no_horizontal:
            values["confDiffHorizontal"] = "false"
        if args.no_vertical:
            values["confDiffVertical"] = "false"
        if args.refl_order is not None:
            values["confReflOrder"] = str(args.refl_order)
        if args.refl_dist is not None:
            values["confMaxReflDist"] = str(args.refl_dist)
        return values

    original_execute = runner.execute_wps
    unthrottled = []

    def execute_wps(endpoint, provenance, events, label, process, parameters):
        if args.host == "pc" and not unthrottled:
            # Engines started over SSH run as background processes, which Windows keeps on the efficiency
            # cores (P-cores idle, ~1/3 of the CPU used). Opt them out of power throttling once the engine
            # is up (science/pipeline/pc/unthrottle.ps1, copied to D:/quietla/tools).
            ssh(f'powershell -NoProfile -ExecutionPolicy Bypass -File {win(PC_ROOT)}\\tools\\unthrottle.ps1', check=False)
            unthrottled.append(True)
        if label == "propagation" and args.atmo:
            # Per-period atmospheric settings (model v3), written right before the propagation from the attempt's own
            # input/atmospheric.geojson by scripts/Quietla/Atmospheric_Settings.groovy (ENGINE_SCRIPTS).
            rows = json.loads((attempt / "input/atmospheric.geojson").read_text())["features"]
            settings = ";".join(f'{r["properties"]["PERIOD"]}:{r["properties"]["WINDROSE"][0]}:{r["properties"]["TEMPERATURE"]}:{r["properties"]["HUMIDITY"]}:{r["properties"]["PRESSURE"]}' for r in rows)
            original_execute(endpoint, provenance, events, "atmospheric", "Quietla:Atmospheric_Settings",
                             {"tableName": str(parameters["tableSources"]).replace("_SOURCES", "_ATMO"), "settings": settings})
        if args.terrain_downscale is not None and "downscale" in parameters:
            parameters = {**parameters, "downscale": str(args.terrain_downscale)}
        if args.host == "mac":
            return original_execute(endpoint, provenance, events, label, process, parameters)
        remote = dict(parameters)
        for key in ("pathFile", "exportPath"):
            if key in remote:
                rel = Path(str(remote[key])).relative_to(attempt.resolve()).as_posix()
                remote[key] = f"{cloud_attempt}/{rel}" if cloud else win(f"{pc_attempt}/{rel}")
        job = original_execute(endpoint, provenance, events, label, process, remote)
        if "exportPath" in parameters:
            rel = Path(str(parameters["exportPath"])).relative_to(attempt.resolve()).as_posix()
            source = f"{args.host}:{cloud_attempt}/{rel}" if cloud else f"{PC}:{pc_attempt}/{rel}"
            subprocess.run(["scp", "-F", str(CLOUD_SSH_CONFIG if cloud else SSH_CONFIG), "-q", source, str(parameters["exportPath"])],
                           check=True, stdin=subprocess.DEVNULL)
        return job

    runner.engine_command, runner.propagation, runner.execute_wps = engine_command, propagation, execute_wps
    if args.atmo and args.host == "mac":
        os.chdir(ENGINE_SCRIPTS)   # the engine inherits this working directory and loads ./scripts from it
    CONTROL.mkdir(parents=True, exist_ok=True)
    runner.time = PausableClock(args.host, args.port)
    try:
        runner.main_run(argparse.Namespace(attempt_dir=attempt, port=args.port, startup_timeout=120.0))
    finally:
        if args.host == "pc":
            ssh(f'powershell -NoProfile -Command "Get-NetTCPConnection -LocalPort {args.port} -State Listen -ErrorAction SilentlyContinue | ForEach-Object {{ Stop-Process -Id $_.OwningProcess -Force }}"', check=False)
        elif cloud:
            cssh(args.host, f"pkill -f -- '--port {args.port} --working-dir' || true", check=False)
    if not args.keep_runtime:
        # The H2 database and the remote input copy are only needed while the engine runs.
        shutil.rmtree(attempt / "runtime", ignore_errors=True)
        if args.host == "pc":
            ssh(f'powershell -NoProfile -Command "Remove-Item -Recurse -Force {win(pc_attempt)} -ErrorAction SilentlyContinue"', check=False)
        elif cloud:
            cssh(args.host, f"rm -rf {cloud_attempt}", check=False)
    (attempt / "run_host.json").write_text(json.dumps({
        "host": args.host, "threads": args.threads, "horizontal_diffraction": not args.no_horizontal, "vertical_diffraction": not args.no_vertical,
        "max_error_db": args.max_error_db, "atmo": args.atmo, "overlay": "hf_v1", "pc_attempt": pc_attempt if args.host == "pc" else None,
        "cloud_attempt": cloud_attempt if cloud else None,
        "refl_order": args.refl_order if args.refl_order is not None else 0, "refl_dist_m": args.refl_dist, "terrain_downscale": args.terrain_downscale or 2,
    }, indent=1) + "\n")
    print(attempt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
