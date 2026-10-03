"""Extract the published JETFAS/R18 reference inputs/expected outputs."""
from __future__ import annotations

import argparse
import hashlib
import json
import warnings
from pathlib import Path

import openpyxl

EXPECTED_WORKBOOK = "ecac_doc29_5e_volume3_part1_workbook.xlsx"
CASE_ID = "JETFAS"
RECEPTOR_ID = "R18"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def table(path: Path, sheet_name: str):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb[sheet_name]
    rows = list(ws.iter_rows(values_only=True))
    return rows[0], rows[1:]


def extract(workbook: Path) -> dict:
    aircraft_header, aircraft_rows = table(workbook, "A-1_Aircraft")
    aircraft_row = next(r for r in aircraft_rows if str(r[0]).strip() == "JETF")
    aircraft = dict(zip(aircraft_header, aircraft_row))
    profile_header, profile_rows = table(workbook, "A-6_Fixed_Point_Profiles")
    profile = [dict(zip(profile_header, r)) for r in profile_rows
               if str(r[0]).strip() == "JETF" and r[1] == "A" and r[2] == "FPP" and r[3] == 1]
    npd_header, npd_rows = table(workbook, "A-7_NPD_Curves")
    npd = [dict(zip(npd_header, r)) for r in npd_rows
           if r[0] == "JETF" and r[1] == "SEL" and r[2] == "A"]
    npd_lamax = [dict(zip(npd_header, r)) for r in npd_rows
                 if r[0] == "JETF" and r[1] == "LAmax" and r[2] == "A"]
    route_header, route_rows = table(workbook, "A-11_Routes")
    route = [dict(zip(route_header, r)) for r in route_rows if r[0] == "AS"]
    receptor_header, receptor_rows = table(workbook, "A-12_Receptors")
    receptor = next(dict(zip(receptor_header, r)) for r in receptor_rows if r[0] == RECEPTOR_ID)
    met_header, met_rows = table(workbook, "A-9_Meteorological")
    meteorology = dict(zip(met_header, next(r for r in met_rows if r[0] is not None)))
    runway_header, runway_rows = table(workbook, "A-10_Runway")
    runway = dict(zip(runway_header, next(r for r in runway_rows if r[0] == "09")))
    sel_header, sel_rows = table(workbook, "B-1_SEL_Results")
    expected_row = next(r for r in sel_rows if r[0] == CASE_ID and r[1] == RECEPTOR_ID)
    segment_header, segment_rows = table(workbook, "B-2_Segment_Results")
    segments = [dict(zip(segment_header, r)) for r in segment_rows if r[0] == CASE_ID and r[1] == RECEPTOR_ID]
    if not all((profile, npd, route, segments)):
        raise ValueError("official reference workbook does not contain all expected JETFAS/R18 inputs")
    return {
        "schema": "ecac_doc29_5e_part1_jetfas_r18_reference_v1",
        "official_test_case": {
            "case_id": CASE_ID,
            "aircraft_id": "JETF",
            "operation": "A",
            "route_id": "AS",
            "route_description": "straight arrival",
            "receptor_id": RECEPTOR_ID,
            "receptor_description": receptor["Receptor Description"],
            "expected_metric": "A-weighted SEL (dB re 1 second)",
            "expected_total_sel_db": expected_row[2],
        },
        "aircraft": aircraft,
        "units": {
            "route_coordinates": "metres in a flat-earth x/y grid",
            "profile_distance_altitude": "metres",
            "profile_speed": "metres/second",
            "profile_thrust": "corrected net thrust, pounds per engine",
            "npd_power": "corrected net thrust, pounds per engine",
            "npd_distance": "feet in official workbook; converted to metres by loader",
            "npd_levels": "A-weighted SEL dB re 1 second",
            "segment_results": "official workbook segment output; unit-bearing column headings retained",
        },
        "meteorology": meteorology,
        "runway_09": runway,
        "route_AS": route,
        "receptor": receptor,
        "fixed_point_profile": profile,
        "npd_arrival_sel_curves": npd,
        "npd_arrival_lamax_curves": npd_lamax,
        "published_segment_results": segments,
        "source": {
            "source_url": "https://ecac-ceac.org/images/documents/Doc29-5th_Edition-Volume_3_Part_1-Workbook.xlsx",
            "workbook_file": workbook.name,
            "workbook_sha256": sha256(workbook),
            "source_sheet_inputs": ["A-1_Aircraft", "A-6_Fixed_Point_Profiles", "A-7_NPD_Curves", "A-9_Meteorological", "A-10_Runway", "A-11_Routes", "A-12_Receptors"],
            "source_sheet_results": ["B-1_SEL_Results", "B-2_Segment_Results"],
            "license_note": "Official ECAC-published workbook; retain internal test/provenance use, check ECAC terms before redistribution.",
            "data_role": "Published independent reference inputs/results, not local measured aircraft data and not a full independent implementation test until this module computes segments itself.",
        },
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workbook", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()
    result = extract(args.workbook)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps({"case": CASE_ID, "receptor": RECEPTOR_ID, "segments": len(result["published_segment_results"]), "expected_sel_db": result["official_test_case"]["expected_total_sel_db"], "output": str(args.output)}))


if __name__ == "__main__":
    main()
