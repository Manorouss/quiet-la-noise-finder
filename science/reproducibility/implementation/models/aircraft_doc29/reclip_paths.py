"""Derive a narrow pilot corridor from a previously verified broad trace set."""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from . import ingest_adsblol_day as ingest

UTC = timezone.utc
PILOT_BBOX = (-118.9, 33.9, -118.1, 34.6)
STREAM_CHUNK_BYTES = 1 << 20


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _iter_json_paths(path: Path):
    decoder = json.JSONDecoder()
    with path.open(encoding="utf-8") as f:
        buffer = f.read(STREAM_CHUNK_BYTES)
        marker = '"paths":['
        while marker not in buffer:
            more = f.read(STREAM_CHUNK_BYTES)
            if not more:
                raise ValueError("JSON activity file has no paths array")
            buffer += more
        marker_at = buffer.index(marker)
        header = json.loads(buffer[:marker_at] + '"paths":[]}')
        buffer = buffer[marker_at + len(marker):]
        while True:
            buffer = buffer.lstrip(" \t\r\n,")
            if buffer.startswith("]"):
                return header
            if not buffer:
                buffer = f.read(STREAM_CHUNK_BYTES)
                if not buffer:
                    raise ValueError("truncated activity paths array")
                continue
            try:
                path_obj, end = decoder.raw_decode(buffer)
            except json.JSONDecodeError:
                more = f.read(STREAM_CHUNK_BYTES)
                if not more:
                    raise ValueError("truncated activity path object")
                buffer += more
                continue
            buffer = buffer[end:]
            yield path_obj


def _internal_point(point):
    epoch = datetime.fromisoformat(point["timestamp_utc"].replace("Z", "+00:00")).timestamp()
    return {
        "epoch": epoch, "lat": point["lat"], "lon": point["lon"],
        "altitude_ft": point["altitude_ft"], "altitude_state": point["altitude_state"],
        "altitude_datum": point["altitude_datum"],
        "geometric_altitude_ft": point["geometric_altitude_ft"],
        "geometric_altitude_state": point["geometric_altitude_state"],
        "geometric_altitude_datum": point["geometric_altitude_datum"],
        "groundspeed_kt": point["groundspeed_kt"], "track_deg": point["track_deg"],
        "vertical_rate_fpm": point["vertical_rate_fpm"],
        "vertical_rate_datum": point["vertical_rate_datum"],
        "geometric_vertical_rate_fpm": point["geometric_vertical_rate_fpm"],
        "indicated_airspeed_kt": point["indicated_airspeed_kt"],
        "position_source": point["position_source"], "stale": False,
        "new_leg": False, "position_quality": point["position_quality"],
    }


def derive(input_path: Path, output_path: Path, receipt_path: Path, *, max_output_bytes=2_000_000_000) -> dict:
    ingest.BBOX = PILOT_BBOX

    def clipped_fragments(source_path):
        points = source_path["points"]
        current = []
        for p1, p2 in zip(points, points[1:]):
            clipped = ingest._clip_segment(_internal_point(p1), _internal_point(p2))
            if clipped is None:
                if len(current) >= 2:
                    yield {"type_code": source_path.get("type_code"), "points": current}
                current = []
                continue
            first, last = clipped
            # A boundary tangency has lo == hi and serializes as two points at
            # one instant. Do not let it become a zero-duration edge/path.
            t_first = datetime.fromisoformat(first["timestamp_utc"].replace("Z", "+00:00"))
            t_last = datetime.fromisoformat(last["timestamp_utc"].replace("Z", "+00:00"))
            if t_last <= t_first:
                if len(current) >= 2:
                    yield {"type_code": source_path.get("type_code"), "points": current}
                current = []
                continue
            if current and ingest.SegmentSpool._shared_endpoint(first, current[-1]):
                if last["timestamp_utc"] != current[-1]["timestamp_utc"]:
                    current.append(last)
            else:
                if len(current) >= 2:
                    yield {"type_code": source_path.get("type_code"), "points": current}
                current = [first, last]
        if len(current) >= 2:
            yield {"type_code": source_path.get("type_code"), "points": current}

    # Read only the small JSON header; path records are decoded incrementally.
    with input_path.open(encoding="utf-8") as f:
        prefix = f.read(STREAM_CHUNK_BYTES)
    marker_at = prefix.index('"paths":[')
    broad_header = json.loads(prefix[:marker_at] + '"paths":[]}')
    if broad_header.get("schema") not in ("quiet_la_adsblol_regional_activity_paths_v1", "quiet_la_adsblol_regional_activity_paths_v2"):
        raise ValueError("unsupported input schema")

    path_count = 0
    segment_count = 0
    source_path_count = 0
    for source_path in _iter_json_paths(input_path):
        source_path_count += 1
        for fragment in clipped_fragments(source_path):
            path_count += 1
            segment_count += len(fragment["points"]) - 1

    output_path.parent.mkdir(parents=True, exist_ok=True)
    header = {
        "schema": "quiet_la_adsblol_regional_activity_paths_v2",
        "license": "ODbL-1.0; internal transformed activity subset, no source aircraft identifiers",
        "date_local": "2026-01-15", "timezone": "America/Los_Angeles",
        "utc_window": broad_header["utc_window"],
        "corridor_bbox_wgs84": {"west": PILOT_BBOX[0], "south": PILOT_BBOX[1],
                                 "east": PILOT_BBOX[2], "north": PILOT_BBOX[3]},
        "path_count": path_count,
    }
    from collections import Counter
    quality = Counter()
    positions = 0
    with output_path.open("wb") as output:
        output.write((json.dumps(header, separators=(",", ":"))[:-1] + ',"paths":[').encode())
        output_index = 0
        for source_path in _iter_json_paths(input_path):
            for fragment in clipped_fragments(source_path):
                output_index += 1
                if output_index > 1:
                    output.write(b",")
                path_obj = {"path_id": f"la_day_path_{output_index:06d}", **fragment}
                output.write(json.dumps(path_obj, separators=(",", ":")).encode())
                positions += len(fragment["points"])
                for point in fragment["points"]:
                    quality[f"primary_altitude:{point['altitude_datum']}:{point['altitude_state']}"] += 1
                    quality[f"geometric_altitude:{point['geometric_altitude_datum']}:{point['geometric_altitude_state']}"] += 1
                    quality[f"geometric_vertical_rate:{'available' if point['geometric_vertical_rate_fpm'] is not None else 'missing'}"] += 1
                    quality[f"indicated_airspeed:{'available' if point['indicated_airspeed_kt'] is not None else 'missing'}"] += 1
                if output.tell() > max_output_bytes:
                    output_path.unlink(missing_ok=True)
                    raise RuntimeError(f"pilot output exceeded {max_output_bytes} byte limit")
        output.write(b"]}\n")
    if output_path.stat().st_size > max_output_bytes:
        output_path.unlink(missing_ok=True)
        raise RuntimeError(f"pilot output exceeded {max_output_bytes} byte limit")
    out_hash = _sha256(output_path)
    receipt = {
        "schema": "quiet_la_adsblol_regional_reclip_receipt_v3",
        "input_file": input_path.name, "input_sha256": _sha256(input_path),
        "output_file": output_path.name, "output_sha256": out_hash,
        "output_bytes": output_path.stat().st_size, "date_local": "2026-01-15",
        "utc_window": broad_header["utc_window"], "derived_from_same_day_broad_corridor": True,
        "region_bbox_wgs84": header["corridor_bbox_wgs84"], "source_paths_examined": source_path_count,
        "retained_path_count": path_count, "retained_adjacent_segments": segment_count,
        "retained_position_count": positions,
        "retained_path_point_quality_availability_counts": dict(sorted(quality.items())),
        "limitations": [
            "Derived by streaming and clipping the verified same-day broad-corridor activity subset; no global trace rescan.",
            "Noncontiguous exit/reentry segments remain separate paths; no missing or stale interval is bridged.",
            "Zero-duration boundary tangencies and serialized timestamp pairs with nonpositive duration are rejected.",
            "Trace paths are not confirmed airport movements and do not establish complete receiver activity.",
            "Primary barometric and independent geometric altitude channels remain separately labeled; no conversion to MSL or airport datum is applied.",
            "Aircraft source identifiers remain absent; no owner lookup or flight-level product is produced.",
        ],
    }
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
    return receipt


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(derive(args.input, args.output, args.receipt), indent=2))


if __name__ == "__main__":
    main()
