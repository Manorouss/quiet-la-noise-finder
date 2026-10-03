"""Normalize a single NOAA GHCNh Van Nuys METAR day window."""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import statistics
from zoneinfo import ZoneInfo

UTC = timezone.utc
START = datetime(2026, 1, 15, 8, tzinfo=UTC)
END = datetime(2026, 1, 16, 8, tzinfo=UTC)
SOURCE_URL = "https://www.ncei.noaa.gov/oa/global-historical-climatology-network/hourly/access/by-year/2026/psv/GHCNh_USW00023130_2026.psv"

VARIABLES = {
    "temperature": "degC",
    "dew_point_temperature": "degC",
    "station_level_pressure": "hPa",
    "sea_level_pressure": "hPa",
    "altimeter": "hPa",
    "wind_direction": "degree_true",
    "wind_speed": "m/s",
    "wind_gust": "m/s",
    "visibility": "km",
}
LA = ZoneInfo("America/Los_Angeles")


def parse(path: Path) -> dict:
    source_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    records = []
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter="|")
        expected = {"STATION", "Station_name", "DATE", "LATITUDE", "LONGITUDE", "ELEVATION"}
        if not expected.issubset(reader.fieldnames or []):
            raise ValueError("unexpected GHCNh PSV schema")
        for row in reader:
            when = datetime.fromisoformat(row["DATE"].replace("Z", "+00:00"))
            if when.tzinfo is None:
                when = when.replace(tzinfo=UTC)  # GHCNh documents DATE and component fields as UTC.
            if not START <= when < END:
                continue
            if row["STATION"] != "USW00023130":
                raise ValueError("annual input contains an unexpected station")
            # GHCNh contains regular five-minute time slots with blank values
            # between the station's actual FM-15/FM-16 source reports.
            if not any(row.get(name) not in (None, "") for name in VARIABLES):
                continue
            values = {}
            for name, unit in VARIABLES.items():
                raw = row.get(name, "")
                sentinel = raw in ("999", "9999", "99999", "-9999", "-999.9", "-9999.0")
                values[name] = {
                    "value": float(raw) if raw not in (None, "") and not sentinel else None,
                    "raw_value": raw or None,
                    "value_status": "missing_or_variable_code" if sentinel else "missing" if raw in (None, "") else "reported",
                    "units": unit,
                    "measurement_code": row.get(name + "_Measurement_Code") or None,
                    "quality_code": row.get(name + "_Quality_Code") or None,
                    "report_type": row.get(name + "_Report_Type") or None,
                    "source_code": row.get(name + "_Source_Code") or None,
                    "source_station_id": row.get(name + "_Source_Station_ID") or None,
                }
            qnh = values["altimeter"]["value"]
            # Standard-pressure to local altimeter-setting correction, using
            # the FAA table's ~1000 ft/inHg relation. The value is a field
            # correction only; it omits route pressure gradients and thermal
            # (non-standard-temperature) altitude error.
            correction = ((qnh - 1013.25) * 1000.0 / 33.8638866667) if qnh is not None else None
            records.append({
                "timestamp_utc": when.astimezone(UTC).isoformat().replace("+00:00", "Z"),
                "timestamp_local": when.astimezone(LA).isoformat(),
                "station_id": row["STATION"], "station_name": row["Station_name"],
                "station_latitude": float(row["LATITUDE"]), "station_longitude": float(row["LONGITUDE"]),
                "station_elevation_m": float(row["ELEVATION"]),
                "report_type": row.get("altimeter_Report_Type") or row.get("temperature_Report_Type") or None,
                "source_station_id": row.get("altimeter_Source_Station_ID") or row.get("temperature_Source_Station_ID") or None,
                "weather": values,
                "candidate_pressure_altitude_correction_ft_from_standard": correction,
            })
    if not records:
        raise ValueError("no observations in requested UTC/LA-day window")
    corrections = [r["candidate_pressure_altitude_correction_ft_from_standard"] for r in records if r["candidate_pressure_altitude_correction_ft_from_standard"] is not None]
    return {
        "schema": "quiet_la_ghcnh_kvny_weather_window_v1",
        "source_url": SOURCE_URL,
        "source_file": path.name,
        "source_bytes": path.stat().st_size,
        "source_sha256": source_hash,
        "source_format": "NOAA GHCNh v1.1.0 pipe-separated annual station file; report time in UTC",
        "station": {"id": "USW00023130", "name": "VAN NUYS AP", "lat": 34.2122, "lon": -118.4914, "elevation_m": 239.3, "icao": "KVNY"},
        "local_date": "2026-01-15", "local_timezone": "America/Los_Angeles",
        "utc_window": {"start_inclusive": START.isoformat().replace("+00:00", "Z"), "end_exclusive": END.isoformat().replace("+00:00", "Z")},
        "observation_count": len(records),
        "report_type_counts": _count(r["report_type"] for r in records),
        "source_station_id_counts": _count(r["source_station_id"] for r in records),
        "candidate_pressure_altitude_correction_ft_from_standard_summary": {
            "count": len(corrections), "minimum": min(corrections) if corrections else None,
            "median": statistics.median(corrections) if corrections else None,
            "maximum": max(corrections) if corrections else None,
            "formula": "(altimeter_hPa - 1013.25) * 1000 / 33.8638866667",
            "interpretation": "approximate pressure-setting-only conversion from standard-pressure altitude to a locally altimeter-set indicated-altitude basis; not full geometric altitude",
        },
        "limitations": [
            "Airport weather is an area proxy, not along-track aircraft pressure/temperature; pressure and temperature vary spatially and vertically.",
            "The pressure-setting correction omits nonstandard-temperature error, terrain/geoid conversion, vertical pressure structure, and ADS-B barometric altitude quantization.",
            "The aircraft model should prefer readsb geometric altitude where available; this weather correction is only a documented fallback for pressure-altitude traces and requires uncertainty bounds.",
            "The source is station observation context; it is not a noise measurement and does not validate acoustic predictions.",
        ],
        "records": records,
    }


def _count(values):
    output = {}
    for value in values:
        key = value or "missing"
        output[key] = output.get(key, 0) + 1
    return dict(sorted(output.items()))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()
    result = parse(args.input)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({k: v for k, v in result.items() if k not in ("records",)}, indent=2))


if __name__ == "__main__":
    main()
