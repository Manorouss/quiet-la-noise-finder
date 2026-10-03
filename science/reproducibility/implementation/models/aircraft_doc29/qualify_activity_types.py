"""Summarize ADS-B type-designator coverage against FAA and internal ANP data.

Outputs only type/coverage counts and ANP candidate identifiers. It never emits
track IDs or any ANP NPD rows; candidate mappings are not auto-used acoustically.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
from zipfile import ZipFile
import csv
from io import TextIOWrapper

from openpyxl import load_workbook


# Family-level candidates, manually matched from FAA designator model strings
# to the ANP aircraft descriptions. The family codes do not identify engine,
# weight, configuration, or exact variant, so none is a validated match.
FAMILY_CANDIDATES = {
    "E75L": ("EMB175", "ERJ170-200 family; long-wing/engine configuration not established"),
    "B738": ("737800", "737-800 family; installed engine variant not identified by trace"),
    "B38M": ("7378MAX", "737 MAX 8 family; installed engine variant not identified by trace"),
    "B737": ("737700", "737-700 family; installed engine variant not identified by trace"),
    "B763": ("767300", "767-300 family; exact engine/ER variant unresolved"),
    "B77W": ("7773ER", "777-300ER family; engine variant unresolved"),
    "B77L": ("777200", "777-200 family proxy for 777-200LR; engine/configuration unresolved"),
    "A359": ("A350-941", "A350-900 family; engine/variant unresolved"),
    "A319": ("A319-131", "A319 family; engine variant unresolved"),
    "A320": ("A320-232", "A320 family; engine variant unresolved"),
    "A321": ("A321-232", "A321 family; engine variant unresolved"),
    "P28A": ("PA28", "PA-28-161 type family; candidate engine/model match needs review"),
    "C172": ("CNA172", "172 family; FAA row is 172S, ANP row is 172R"),
    "GLF4": ("GIV", "Gulfstream IV family; specific variant/engine unresolved"),
    "GLF5": ("GV", "Gulfstream V/G550 family; specific variant/engine unresolved"),
}


def _count_types(paths_file: Path) -> Counter:
    import subprocess
    proc = subprocess.Popen(
        ["jq", "-r", ".paths[] | if .type_code == null then \"missing\" else .type_code end", str(paths_file)],
        stdout=subprocess.PIPE, text=True,
    )
    assert proc.stdout is not None
    counts = Counter(line.rstrip("\n") for line in proc.stdout)
    rc = proc.wait()
    if rc:
        raise RuntimeError(f"jq type count failed with status {rc}")
    return counts


def run(paths_file: Path, faa_workbook: Path, anp_archive: Path) -> dict:
    counts = _count_types(paths_file)
    workbook = load_workbook(faa_workbook, read_only=True, data_only=True)
    sheet = workbook["ACD_Data"]
    header = [cell.value for cell in sheet[1]]
    faa = {str(row[0]).strip().upper(): dict(zip(header, row))
           for row in sheet.iter_rows(min_row=2, values_only=True) if row[0]}
    with ZipFile(anp_archive) as archive:
        with archive.open("ANP2.3_Aircraft.csv") as raw:
            anp_rows = list(csv.DictReader(TextIOWrapper(raw, encoding="cp1252", newline=""), delimiter=";"))
        with archive.open("ANP2.3_NPD_data.csv") as raw:
            npd_rows = list(csv.DictReader(TextIOWrapper(raw, encoding="cp1252", newline=""), delimiter=";"))
    anp_ids = {row["ACFT_ID"].strip().upper() for row in anp_rows}
    anp_by_id = {row["ACFT_ID"].strip().upper(): row for row in anp_rows}
    npd_available = {
        (row["NPD_ID"].strip(), row["Noise Metric"].strip(), row["Op Mode"].strip())
        for row in npd_rows
    }

    types = []
    status_counts = Counter()
    for code, fragment_count in sorted(counts.items(), key=lambda item: (-item[1], item[0])):
        row = faa.get(code)
        exact = code in anp_ids
        candidate = FAMILY_CANDIDATES.get(code)
        if exact:
            status = "direct_code_equals_anp_id_requires_aircraft_noise_review"
            candidate_id, rationale = code, "designator string equals ANP ACFT_ID; not proof of configuration equivalence"
        elif candidate and candidate[0].upper() in anp_ids:
            status = "family_proxy_candidate_only_not_accepted"
            candidate_id, rationale = candidate
        else:
            status = "unsupported_no_validated_anp_mapping"
            candidate_id, rationale = None, None
        table_availability = None
        if candidate_id:
            anp_row = anp_by_id[candidate_id.upper()]
            npd_id = anp_row["NPD_ID"].strip()
            available = sorted(f"{metric}/{op}" for mid, metric, op in npd_available if mid == npd_id)
            table_availability = {
                "anp_aircraft_id_internal_only": candidate_id,
                "npd_id_internal_only": npd_id,
                "power_parameter_internal_only": anp_row["Power Parameter"].strip(),
                "available_metric_operation_pairs": available,
                "has_sel_and_lamax_for_arrival_and_departure": all(
                    (npd_id, metric, op) in npd_available
                    for metric in ("SEL", "LAmax") for op in ("A", "D")
                ),
            }
        faa_info = None
        if row:
            faa_info = {
                "manufacturer": row.get("Manufacturer"),
                "model_faa": row.get("Model_FAA"),
                "model_bada": row.get("Model_BADA"),
                "engine_class": row.get("Physical_Class_Engine"),
                "engine_count": row.get("Num_Engines"),
                "source_date": "October 2024",
            }
        types.append({
            "icao_type_code": code,
            "regional_path_fragment_count_not_flight_count": fragment_count,
            "faa_designator_record_found": row is not None,
            "faa_aircraft_metadata": faa_info,
            "mapping_status": status,
            "anp_candidate_id_internal_only": candidate_id,
            "candidate_rationale": rationale,
            "anp_table_availability_internal_only": table_availability,
        })
        status_counts[status] += fragment_count
    return {
        "schema": "quiet_la_activity_type_anp_coverage_internal_v1",
        "source_paths": str(paths_file),
        "date_local": "2026-01-15", "timezone": "America/Los_Angeles",
        "regional_paths_are_not_flight_movements": True,
        "path_fragment_count": sum(counts.values()),
        "faa_source_url": "https://www.faa.gov/airports/engineering/aircraft_char_database/aircraft_data",
        "faa_workbook_sha256": hashlib.sha256(faa_workbook.read_bytes()).hexdigest(),
        "faa_workbook_download_license_note": "Public official download; the captured page describes data, but an explicit reuse license was not present in the checked page. Keep cited and internal pending terms review.",
        "distinct_type_codes_including_missing": len(counts),
        "faa_designator_matched_path_fragments": sum(t["regional_path_fragment_count_not_flight_count"] for t in types if t["faa_designator_record_found"]),
        "family_proxy_candidate_path_fragments_not_accepted": status_counts["family_proxy_candidate_only_not_accepted"],
        "unsupported_path_fragments": status_counts["unsupported_no_validated_anp_mapping"],
        "status_path_fragment_counts": dict(status_counts),
        "method": [
            "Trace type_code is compared exactly to FAA Aircraft Characteristics Database ICAO_Code (October 2024).",
            "No aircraft registration, callsign, owner, or operator lookup is performed.",
            "Candidate ANP IDs are manually selected family-level comparators; none is accepted for model use without aircraft engine/configuration review.",
            "A missing FAA designator record or ANP candidate is treated as unsupported, not as zero noise.",
        ],
        "rights": "Internal evidence only. ADS-B path counts derive from ODbL-1.0 activity data. ANP IDs are derived from the locally held EASA ANP v2.3 archive under its terms and must not be redistributed as a public aircraft database or NPD table.",
        "type_codes": types,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--paths", type=Path, required=True)
    parser.add_argument("--faa-workbook", type=Path, required=True)
    parser.add_argument("--anp-archive", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run(args.paths, args.faa_workbook, args.anp_archive)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({k: result[k] for k in (
        "path_fragment_count", "distinct_type_codes_including_missing",
        "faa_designator_matched_path_fragments", "family_proxy_candidate_path_fragments_not_accepted",
        "unsupported_path_fragments", "status_path_fragment_counts",
    )}, indent=2))


if __name__ == "__main__":
    main()
