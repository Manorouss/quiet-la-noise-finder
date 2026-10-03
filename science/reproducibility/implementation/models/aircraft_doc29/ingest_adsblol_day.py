"""Stream two ADSB.lol history tar releases into a minimized LA-day trace set.

Raw archives remain temporary and are never extracted wholesale. Output drops
ICAO addresses, registrations, callsigns, and unneeded aircraft metadata.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import subprocess
import tarfile
import tempfile
from typing import Iterable

UTC = timezone.utc
WINDOW_START = datetime(2026, 1, 15, 8, tzinfo=UTC).timestamp()
WINDOW_END = datetime(2026, 1, 16, 8, tzinfo=UTC).timestamp()
BBOX = (-118.9, 33.9, -118.1, 34.6)  # west, south, east, north
MAX_PAIR_GAP_S = 90.0


def _number(value):
    return isinstance(value, (int, float)) and math.isfinite(float(value))


def _clip_box(lon1, lat1, lon2, lat2):
    west, south, east, north = BBOX
    dx, dy = lon2 - lon1, lat2 - lat1
    lo, hi = 0.0, 1.0
    for p, q in ((-dx, lon1 - west), (dx, east - lon1),
                 (-dy, lat1 - south), (dy, north - lat1)):
        if p == 0:
            if q < 0:
                return None
        else:
            t = q / p
            if p < 0:
                lo = max(lo, t)
            else:
                hi = min(hi, t)
            if lo > hi:
                return None
    return lo, hi


def _interpolate_value(a, b, f):
    return (float(a) + f * (float(b) - float(a))) if _number(a) and _number(b) else None


def _interpolate_track(a, b, f):
    if not (_number(a) and _number(b)):
        return None
    delta = ((float(b) - float(a) + 180.0) % 360.0) - 180.0
    value = (float(a) + f * delta) % 360.0
    return 0.0 if math.isclose(value, 360.0, abs_tol=1e-10) else value


def _point_record(raw, epoch):
    # readsb trace row: [offset, lat, lon, baro altitude, GS, track, flags,
    # vertical rate, extra, source, geometric altitude, geometric rate, IAS, roll]
    offset, lat, lon = raw[0], raw[1], raw[2]
    flags = int(raw[6] or 0) if len(raw) > 6 else 0
    source = raw[9] if len(raw) > 9 and isinstance(raw[9], str) else "unknown"
    extra = raw[8] if len(raw) > 8 and isinstance(raw[8], dict) else {}
    # In readsb JSON the flags describe the primary altitude in raw[3]. The
    # separate raw[10] field is independently supplied geometric altitude;
    # its presence does not depend on flags&8 (see readsb README-json.md).
    primary_alt_value = raw[3] if len(raw) > 3 else None
    primary_geometric = bool(flags & 8)

    def altitude(value, geometric):
        if value == "ground":
            return None, "ground", "surface-state"
        if _number(value):
            datum = "geometric_WGS84_ellipsoid" if geometric else "barometric_pressure_reference_unspecified"
            return float(value), "numeric", datum
        return None, "unknown", "unknown"

    altitude_ft, altitude_state, altitude_datum = altitude(primary_alt_value, primary_geometric)
    geometric_altitude = raw[10] if len(raw) > 10 else None
    geometric_altitude_ft, geometric_altitude_state, geometric_altitude_datum = altitude(geometric_altitude, True)
    return {
        "epoch": float(epoch) + float(offset),
        "lat": float(lat), "lon": float(lon),
        "altitude_ft": altitude_ft, "altitude_state": altitude_state,
        "altitude_datum": altitude_datum,
        "geometric_altitude_ft": geometric_altitude_ft,
        "geometric_altitude_state": geometric_altitude_state,
        "geometric_altitude_datum": geometric_altitude_datum,
        "groundspeed_kt": float(raw[4]) if len(raw) > 4 and _number(raw[4]) else None,
        "track_deg": float(raw[5]) if len(raw) > 5 and _number(raw[5]) else None,
        "vertical_rate_fpm": float(raw[7]) if len(raw) > 7 and _number(raw[7]) else None,
        "vertical_rate_datum": "geometric" if flags & 4 else "barometric_or_unknown",
        "geometric_vertical_rate_fpm": float(raw[11]) if len(raw) > 11 and _number(raw[11]) else None,
        "indicated_airspeed_kt": float(raw[12]) if len(raw) > 12 and _number(raw[12]) else None,
        "position_source": source,
        "stale": bool(flags & 1),
        "new_leg": bool(flags & 2),
        "altitude_is_geometric": primary_geometric,
        "position_quality": {
            k: extra.get(k) for k in ("nic", "rc", "nac_p", "nac_v", "sil", "sil_type", "gva")
            if extra.get(k) is not None
        },
    }


def _clip_segment(p1, p2):
    if p1["stale"] or p2["stale"]:
        return None
    dt = p2["epoch"] - p1["epoch"]
    if dt <= 0 or dt > MAX_PAIR_GAP_S:
        return None
    spatial = _clip_box(p1["lon"], p1["lat"], p2["lon"], p2["lat"])
    if spatial is None:
        return None
    lo = max(spatial[0], 0.0, (WINDOW_START - p1["epoch"]) / dt)
    hi = min(spatial[1], 1.0, (WINDOW_END - p1["epoch"]) / dt)
    if lo > hi:
        return None

    def at(f):
        source = p1 if f <= 1e-12 else p2 if f >= 1.0 - 1e-12 else None
        if source is not None:
            point = {k: v for k, v in source.items() if k != "epoch"}
            point["timestamp_utc"] = datetime.fromtimestamp(p1["epoch"] + dt * f, UTC).isoformat().replace("+00:00", "Z")
            point["lat"] = p1["lat"] + (p2["lat"] - p1["lat"]) * f
            point["lon"] = p1["lon"] + (p2["lon"] - p1["lon"]) * f
            return point
        point = {
            "timestamp_utc": datetime.fromtimestamp(p1["epoch"] + dt * f, UTC).isoformat().replace("+00:00", "Z"),
            "lat": p1["lat"] + (p2["lat"] - p1["lat"]) * f,
            "lon": p1["lon"] + (p2["lon"] - p1["lon"]) * f,
            "altitude_ft": _interpolate_value(p1["altitude_ft"], p2["altitude_ft"], f) if p1["altitude_datum"] == p2["altitude_datum"] else None,
            "altitude_state": p1["altitude_state"] if p1["altitude_state"] == p2["altitude_state"] else "transition_or_unknown",
            "altitude_datum": p1["altitude_datum"] if p1["altitude_datum"] == p2["altitude_datum"] else "mixed_or_unknown",
            "geometric_altitude_ft": _interpolate_value(p1["geometric_altitude_ft"], p2["geometric_altitude_ft"], f) if p1["geometric_altitude_datum"] == p2["geometric_altitude_datum"] else None,
            "geometric_altitude_state": p1["geometric_altitude_state"] if p1["geometric_altitude_state"] == p2["geometric_altitude_state"] else "transition_or_unknown",
            "geometric_altitude_datum": p1["geometric_altitude_datum"] if p1["geometric_altitude_datum"] == p2["geometric_altitude_datum"] else "mixed_or_unknown",
            "groundspeed_kt": _interpolate_value(p1["groundspeed_kt"], p2["groundspeed_kt"], f),
            "track_deg": _interpolate_track(p1["track_deg"], p2["track_deg"], f),
            "vertical_rate_fpm": _interpolate_value(p1["vertical_rate_fpm"], p2["vertical_rate_fpm"], f) if p1["vertical_rate_datum"] == p2["vertical_rate_datum"] else None,
            "vertical_rate_datum": p1["vertical_rate_datum"] if p1["vertical_rate_datum"] == p2["vertical_rate_datum"] else "mixed_or_unknown",
            "geometric_vertical_rate_fpm": _interpolate_value(p1["geometric_vertical_rate_fpm"], p2["geometric_vertical_rate_fpm"], f),
            "indicated_airspeed_kt": _interpolate_value(p1["indicated_airspeed_kt"], p2["indicated_airspeed_kt"], f),
            "position_source": p1["position_source"] if p1["position_source"] == p2["position_source"] else "source_transition",
            "stale": False,
            "position_quality": p1["position_quality"] if p1["position_quality"] == p2["position_quality"] else {},
        }
        return point
    return at(lo), at(hi)


def _pair_intersects_window_and_box(raw1, raw2, base_epoch):
    if not all(_number(raw[i]) for raw in (raw1, raw2) for i in (0, 1, 2)):
        return False
    flags1 = int(raw1[6] or 0) if len(raw1) > 6 else 0
    flags2 = int(raw2[6] or 0) if len(raw2) > 6 else 0
    if (flags1 & 1) or (flags2 & 1) or (flags2 & 2):
        return False
    t1, t2 = base_epoch + float(raw1[0]), base_epoch + float(raw2[0])
    dt = t2 - t1
    if dt <= 0 or dt > MAX_PAIR_GAP_S or t2 < WINDOW_START or t1 >= WINDOW_END:
        return False
    return _clip_box(float(raw1[2]), float(raw1[1]), float(raw2[2]), float(raw2[1])) is not None


class SegmentSpool:
    """Disk-backed transient join by source ICAO; identifiers never enter output."""
    def __init__(self, max_bytes: int = 8 * 1024**3):
        fd, name = tempfile.mkstemp(prefix="quietla_adsb_segments_", suffix=".sqlite", dir="/private/tmp")
        os.close(fd)
        self.path = Path(name)
        self.max_bytes = max_bytes
        self.db = sqlite3.connect(name)
        self.db.execute("PRAGMA journal_mode=OFF")
        self.db.execute("PRAGMA synchronous=OFF")
        self.db.execute("CREATE TABLE segments (ident TEXT NOT NULL, start_epoch REAL NOT NULL, type_code TEXT, payload TEXT NOT NULL)")
        self.db.execute("CREATE INDEX by_ident_time ON segments (ident, start_epoch)")
        self.db.commit()
        self.pending = 0

    def add(self, ident: str, type_code: str | None, points: tuple[dict, dict]):
        self.db.execute("INSERT INTO segments VALUES (?,?,?,?)", (
            ident, datetime.fromisoformat(points[0]["timestamp_utc"].replace("Z", "+00:00")).timestamp(),
            type_code, json.dumps(points, separators=(",", ":"))))
        self.pending += 1
        if self.pending >= 10000:
            self.db.commit()
            self.pending = 0
            self._check_size()

    def finish_writes(self):
        self.db.commit()
        self.pending = 0
        self._check_size()

    def _check_size(self):
        size = self.path.stat().st_size
        if size > self.max_bytes:
            raise RuntimeError(f"temporary accepted-segment spool exceeded {self.max_bytes} byte cap")

    @staticmethod
    def _shared_endpoint(first: dict, previous: dict) -> bool:
        return (first["timestamp_utc"] == previous["timestamp_utc"]
                and abs(first["lat"] - previous["lat"]) <= 1e-10
                and abs(first["lon"] - previous["lon"]) <= 1e-10)

    def iter_paths(self):
        self.finish_writes()
        cursor = self.db.execute("SELECT ident,type_code,payload FROM segments ORDER BY ident,start_epoch,rowid")
        active_ident = None
        active_type = None
        active_points = []
        for ident, type_code, payload in cursor:
            points = json.loads(payload)
            if active_points:
                continuous = ident == active_ident and self._shared_endpoint(points[0], active_points[-1])
                same_type = active_type == type_code
                if not continuous or not same_type:
                    yield active_type, active_points
                    active_points = []
                    active_type = None
            if not active_points:
                active_ident = ident
                active_type = type_code
                active_points = list(points)
            else:
                if points[1]["timestamp_utc"] != active_points[-1]["timestamp_utc"]:
                    active_points.append(points[1])
                if active_type is None:
                    active_type = type_code
        if active_points:
            yield active_type, active_points

    def close(self, *, remove=True):
        self.db.close()
        if remove:
            self.path.unlink(missing_ok=True)


def _stream_one_tar(archive_parts: list[Path], stats: Counter, spool: SegmentSpool):
    proc = subprocess.Popen(["cat", *map(str, archive_parts)], stdout=subprocess.PIPE)
    assert proc.stdout is not None
    try:
        with tarfile.open(fileobj=proc.stdout, mode="r|") as tar:
            for member in tar:
                if not member.isfile() or not member.name.endswith(".json"):
                    continue
                stats["trace_members"] += 1
                f = tar.extractfile(member)
                if f is None:
                    stats["unreadable_members"] += 1
                    continue
                try:
                    with gzip.GzipFile(fileobj=f) as gz:
                        record = json.load(gz)
                except (OSError, EOFError, json.JSONDecodeError):
                    stats["invalid_trace_files"] += 1
                    continue
                internal = str(record.get("icao", ""))
                epoch = record.get("timestamp")
                trace = record.get("trace", [])
                if not internal or not _number(epoch) or not isinstance(trace, list):
                    stats["invalid_trace_files"] += 1
                    continue
                # This ICAO key exists only in memory to join the two adjacent UTC days.
                type_code = record.get("t") if isinstance(record.get("t"), str) else None
                stats["trace_rows"] += len(trace)
                invalid_rows = [row for row in trace if not isinstance(row, list) or len(row) < 7 or not all(_number(row[i]) for i in (0, 1, 2))]
                if invalid_rows:
                    stats["invalid_points"] += len(invalid_rows)
                    stats["trace_records_with_invalid_points_skipped"] += 1
                    continue
                offsets = [row[0] for row in trace]
                if any(b < a for a, b in zip(offsets, offsets[1:])):
                    trace = sorted(trace, key=lambda row: row[0])
                    stats["out_of_order_trace_records_sorted"] += 1
                if any(b == a for a, b in zip((row[0] for row in trace), (row[0] for row in trace[1:]))):
                    stats["duplicate_time_trace_records_skipped"] += 1
                    continue
                prev = None
                for row in trace:
                    stats["valid_points"] += 1
                    flags = int(row[6] or 0)
                    source = row[9] if len(row) > 9 and isinstance(row[9], str) else "unknown"
                    stats["source:" + source] += 1
                    raw_alt = row[3] if len(row) > 3 else None
                    primary_state = "ground" if raw_alt == "ground" else "numeric" if _number(raw_alt) else "unknown"
                    primary_datum = "geometric_WGS84_ellipsoid" if flags & 8 and primary_state == "numeric" else "barometric_pressure_reference_unspecified" if primary_state == "numeric" else "surface-state" if primary_state == "ground" else "unknown"
                    geom_alt = row[10] if len(row) > 10 else None
                    geom_state = "ground" if geom_alt == "ground" else "numeric" if _number(geom_alt) else "unknown"
                    geom_datum = "geometric_WGS84_ellipsoid" if geom_state == "numeric" else "surface-state" if geom_state == "ground" else "unknown"
                    stats["altitude:" + primary_state] += 1
                    stats["altitude_datum:" + primary_datum] += 1
                    stats["geometric_altitude:" + geom_state] += 1
                    stats["geometric_altitude_datum:" + geom_datum] += 1
                    stats["geometric_vertical_rate_available" if len(row) > 11 and _number(row[11]) else "geometric_vertical_rate_missing"] += 1
                    stats["indicated_airspeed_available" if len(row) > 12 and _number(row[12]) else "indicated_airspeed_missing"] += 1
                    if flags & 1:
                        stats["stale_positions"] += 1
                    if source not in ("adsb_icao", "adsb_icao_nt", "adsb_other", "adsr_icao", "adsr_other"):
                        stats["non_primary_adsb_source_positions"] += 1
                    if prev is not None and _pair_intersects_window_and_box(prev, row, float(epoch)):
                        p1, p2 = _point_record(prev, float(epoch)), _point_record(row, float(epoch))
                        fragment = _clip_segment(p1, p2)
                        if fragment is not None:
                            stats["corridor_intersecting_segments"] += 1
                            stats["type_code_known_tracks" if type_code else "type_code_missing_tracks"] += 1
                            spool.add(internal, type_code, fragment)
                    prev = row
                if stats["trace_members"] % 50000 == 0:
                    print(json.dumps({"progress": "archive scan", "members": stats["trace_members"], "corridor_segments": stats["corridor_intersecting_segments"]}), flush=True)
    finally:
        proc.stdout.close()
    rc = proc.wait()
    if rc:
        raise RuntimeError(f"cat exited with status {rc}")


def stream_archives(archive_parts: list[Path], stats: Counter, spool: SegmentSpool):
    # Each UTC day's split pieces form a separate tar archive with its own end
    # markers. Stream the dates separately; concatenating the two tar streams
    # would make tarfile correctly stop at the first archive's end marker.
    grouped: dict[str, list[Path]] = defaultdict(list)
    for part in archive_parts:
        day = part.name.split("-planes-", 1)[0]
        grouped[day].append(part)
    for day in sorted(grouped):
        _stream_one_tar(sorted(grouped[day]), stats, spool)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _verify_source_parts(parts: list[Path], capture_receipt: Path) -> list[dict]:
    capture = json.loads(capture_receipt.read_text())
    if capture.get("schema") != "quiet_la_adsblol_jan15_16_source_capture_v1":
        raise ValueError("unrecognized ADSB source capture receipt")
    expected = {item["file"]: item for item in capture.get("assets", [])}
    verified = []
    for path in parts:
        item = expected.get(path.name)
        if item is None:
            raise ValueError(f"source asset absent from capture receipt: {path.name}")
        size = path.stat().st_size
        digest = _sha256_file(path)
        if size != item["actual_bytes"] or digest != item["actual_sha256"]:
            raise ValueError(f"source archive no longer matches verified capture: {path.name}")
        verified.append({"file": path.name, "bytes": size, "sha256": digest,
                         "official_release_sha256": item["expected_sha256"]})
    return verified


def _assemble_output(traces_by_internal_id: dict) -> list[dict]:
    paths = []
    for internal, fragments in traces_by_internal_id.items():
        fragments.sort(key=lambda f: f["points"][0]["timestamp_utc"])
        current = []
        current_type = None
        for fragment in fragments:
            typ = fragment["type_code"]
            pts = fragment["points"]
            if current:
                prev = current[-1]
                first = pts[0]
                contiguous = (first["timestamp_utc"] == prev["timestamp_utc"]
                              and abs(first["lat"] - prev["lat"]) <= 1e-10
                              and abs(first["lon"] - prev["lon"]) <= 1e-10)
                if not contiguous or (current_type and typ and current_type != typ):
                    paths.append({"type_code": current_type, "points": current})
                    current = []
            current_type = typ or current_type
            for point in pts:
                if not current or point["timestamp_utc"] != current[-1]["timestamp_utc"]:
                    current.append(point)
        if current:
            paths.append({"type_code": current_type, "points": current})
    # Stable source identifiers and ICAO addresses do not leave the in-memory join.
    paths.sort(key=lambda p: p["points"][0]["timestamp_utc"])
    return [{"path_id": f"la_day_path_{i:06d}", **p} for i, p in enumerate(paths, 1)]


def run(parts: list[Path], out_path: Path, receipt_path: Path, *,
        max_output_bytes: int = 2_000_000_000,
        max_spool_bytes: int = 8 * 1024**3,
        source_capture_receipt: Path) -> dict:
    source_parts = _verify_source_parts(parts, source_capture_receipt)
    stats = Counter()
    spool = SegmentSpool(max_spool_bytes)
    try:
        stream_archives(parts, stats, spool)
        spool.finish_writes()
        unique_keys = spool.db.execute("SELECT COUNT(DISTINCT ident) FROM segments").fetchone()[0]
        path_count = sum(1 for _ in spool.iter_paths())
        retained_quality = Counter()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        header = {
            "schema": "quiet_la_adsblol_regional_activity_paths_v2",
            "license": "ODbL-1.0; internal transformed activity subset, no source aircraft identifiers",
            "date_local": "2026-01-15", "timezone": "America/Los_Angeles",
            "utc_window": {"start_inclusive": "2026-01-15T08:00:00Z", "end_exclusive": "2026-01-16T08:00:00Z"},
            "corridor_bbox_wgs84": {"west": BBOX[0], "south": BBOX[1], "east": BBOX[2], "north": BBOX[3]},
            "path_count": path_count,
        }
        written_positions = 0
        with out_path.open("wb") as output:
            output.write((json.dumps(header, separators=(",", ":"))[:-1] + ',"paths":[').encode("utf-8"))
            for index, (type_code, points) in enumerate(spool.iter_paths(), 1):
                if index > 1:
                    output.write(b",")
                path = {"path_id": f"la_day_path_{index:06d}", "type_code": type_code, "points": points}
                output.write(json.dumps(path, separators=(",", ":")).encode("utf-8"))
                written_positions += len(points)
                for point in points:
                    for key, value in (("primary_altitude_state", point["altitude_state"]),
                                       ("primary_altitude_datum", point["altitude_datum"]),
                                       ("geometric_altitude_state", point["geometric_altitude_state"]),
                                       ("geometric_altitude_datum", point["geometric_altitude_datum"]),
                                       ("geometric_vertical_rate", "available" if point["geometric_vertical_rate_fpm"] is not None else "missing"),
                                       ("indicated_airspeed", "available" if point["indicated_airspeed_kt"] is not None else "missing")):
                        retained_quality[f"{key}:{value}"] += 1
                if output.tell() > max_output_bytes:
                    raise RuntimeError(f"minimized output exceeded {max_output_bytes} byte limit")
            output.write(b"]}\n")
        output_bytes = out_path.stat().st_size
        if output_bytes > max_output_bytes:
            raise RuntimeError(f"minimized output exceeded {max_output_bytes} byte limit")
        digest = hashlib.sha256()
        with out_path.open("rb") as f:
            for block in iter(lambda: f.read(1024 * 1024), b""):
                digest.update(block)
        output_sha256 = digest.hexdigest()
    except BaseException:
        out_path.unlink(missing_ok=True)
        raise
    finally:
        spool.close(remove=True)
    receipt = {
        "schema": "quiet_la_adsblol_regional_ingest_receipt_v1",
        "date_local": "2026-01-15", "timezone": "America/Los_Angeles",
        "utc_window": {"start_inclusive": "2026-01-15T08:00:00Z", "end_exclusive": "2026-01-16T08:00:00Z"},
        "corridor_bbox_wgs84": {"west": BBOX[0], "south": BBOX[1], "east": BBOX[2], "north": BBOX[3]},
        "input_parts": [p.name for p in parts],
        "source_capture_receipt": source_capture_receipt.name,
        "source_capture_receipt_sha256": _sha256_file(source_capture_receipt),
        "verified_source_parts": source_parts,
        "parsed_trace_files": stats["trace_members"], "valid_trace_positions": stats["valid_points"],
        "corridor_intersecting_segments": stats["corridor_intersecting_segments"],
        "unique_transient_aircraft_keys_with_corridor_data": unique_keys,
        "paths_after_minimization": path_count,
        "retained_path_position_count": written_positions,
        "normalized_output_bytes": output_bytes,
        "normalized_output_sha256": output_sha256,
        "global_quality_availability_counts": {k: v for k, v in sorted(stats.items()) if k.startswith(("source:", "altitude:", "altitude_datum:", "geometric_altitude:", "geometric_altitude_datum:")) or k in ("geometric_vertical_rate_available", "geometric_vertical_rate_missing", "indicated_airspeed_available", "indicated_airspeed_missing")},
        "retained_path_point_quality_availability_counts": dict(sorted(retained_quality.items())),
        "quality_gaps": {k: stats[k] for k in ("invalid_trace_files", "invalid_points", "unreadable_members", "stale_positions", "non_primary_adsb_source_positions", "type_code_missing_tracks")},
        "limitations": [
            "Archive release contains global aircraft traces; no VNY-specific or regional completeness guarantee is available.",
            "Paths are regional activity observations, not confirmed airport movements or sound measurements.",
            "Primary readsb altitude preserves its raw datum flag; independent geometric altitude preserves readsb's WGS84-ellipsoid datum. Neither is converted to MSL/airport datum.",
            "No aircraft owner, registration, or callsign lookup is performed; source ICAO identifiers exist only as transient in-memory joins.",
            "This output is a preselected validation candidate and has not been used for model tuning.",
        ],
        "stats": {k: v for k, v in sorted(stats.items())},
        "normalized_output": out_path.name,
        "transient_spool_max_bytes": max_spool_bytes,
        "transient_spool_removed_after_completion": True,
        "scan_terminal_state": "completed",
        "parser_sha256": _sha256_file(Path(__file__)),
    }
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
    return receipt


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input-dir", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--receipt", type=Path, required=True)
    ap.add_argument("--bbox", nargs=4, type=float,
                    metavar=("WEST", "SOUTH", "EAST", "NORTH"),
                    help="override default corridor bounds (WGS84 degrees)")
    ap.add_argument("--max-output-bytes", type=int, default=2_000_000_000)
    ap.add_argument("--max-spool-bytes", type=int, default=8 * 1024**3)
    default_capture = Path(__file__).resolve().parents[3] / "implementation/work/autonomous_delivery_2026_10_02/source_evidence/adsblol_jan15_16_archive_capture_receipt.json"
    ap.add_argument("--source-capture-receipt", type=Path, default=default_capture)
    args = ap.parse_args()
    if args.bbox:
        global BBOX
        BBOX = tuple(args.bbox)
    names = [
        "v2026.01.15-planes-readsb-prod-0.tar.aa",
        "v2026.01.15-planes-readsb-prod-0.tar.ab",
        "v2026.01.16-planes-readsb-prod-0.tar.aa",
        "v2026.01.16-planes-readsb-prod-0.tar.ab",
    ]
    try:
        result = run([args.input_dir / name for name in names], args.output, args.receipt,
                     max_output_bytes=args.max_output_bytes, max_spool_bytes=args.max_spool_bytes,
                     source_capture_receipt=args.source_capture_receipt)
    except BaseException as error:
        args.receipt.parent.mkdir(parents=True, exist_ok=True)
        failure = {"schema": "quiet_la_adsblol_regional_ingest_failure_v1",
                   "scan_terminal_state": "interrupted_or_failed",
                   "error_type": type(error).__name__, "error": str(error),
                   "date_local": "2026-01-15", "corridor_bbox_wgs84": {"west": BBOX[0], "south": BBOX[1], "east": BBOX[2], "north": BBOX[3]},
                   "input_parts": [p.name for p in [args.input_dir/name for name in names]],
                   "source_capture_receipt": args.source_capture_receipt.name,
                   "parser_sha256": _sha256_file(Path(__file__))}
        args.receipt.parent.mkdir(parents=True, exist_ok=True)
        args.receipt.write_text(json.dumps(failure, indent=2) + "\n")
        raise
    print(json.dumps({
        "parsed_trace_files": result["parsed_trace_files"],
        "regional_paths": result["paths_after_minimization"],
        "unique_transient_aircraft_keys_with_corridor_data": result["unique_transient_aircraft_keys_with_corridor_data"],
        "retained_path_position_count": result["retained_path_position_count"],
        "normalized_output_bytes": result["normalized_output_bytes"],
        "receipt": str(args.receipt),
    }, indent=2))


if __name__ == "__main__":
    main()
