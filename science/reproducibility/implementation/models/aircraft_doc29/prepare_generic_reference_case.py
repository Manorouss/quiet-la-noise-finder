"""Extract a named published straight-flight ECAC Doc 29 test case/receptor.

Supports the published JETFAS/JETFDS and PROPDS-family workbook cases.
Published B-2 rows remain comparison targets and are not model inputs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import warnings
from pathlib import Path

import openpyxl


def table(path: Path, sheet_name: str):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb[sheet_name]
    rows = list(ws.iter_rows(values_only=True))
    return rows[0], rows[1:]


def extract(workbook: Path, case_id: str, receptor_id: str) -> dict:
    case_id = case_id.strip().upper()
    if len(case_id) != 6 or case_id[:4] not in ("JETF", "PROP") or case_id[4] not in "AD" or case_id[5] not in "CS":
        raise ValueError("case_id must be a published straight JETF/PROP test such as JETFDS or PROPDS")
    operation, route_id = case_id[4], case_id[4:]
    aircraft_id = case_id[:4]
    aircraft_header, aircraft_rows = table(workbook, "A-1_Aircraft")
    aircraft_row = next(r for r in aircraft_rows if str(r[0]).strip() == aircraft_id)
    aircraft = dict(zip(aircraft_header, aircraft_row))
    profile_header, profile_rows = table(workbook, "A-6_Fixed_Point_Profiles")
    profile = [dict(zip(profile_header, r)) for r in profile_rows
               if str(r[0]).strip() == aircraft_id and r[1] == operation and r[2] == "FPP" and r[3] == 1]
    npd_header, npd_rows = table(workbook, "A-7_NPD_Curves")
    npd = [dict(zip(npd_header, r)) for r in npd_rows
           if str(r[0]).strip() == aircraft_id and r[1] == "SEL" and r[2] == operation]
    npd_lamax = [dict(zip(npd_header, r)) for r in npd_rows
                 if str(r[0]).strip() == aircraft_id and r[1] == "LAmax" and r[2] == operation]
    route_header, route_rows = table(workbook, "A-11_Routes")
    route = [dict(zip(route_header, r)) for r in route_rows if r[0] == route_id]
    receptor_header, receptor_rows = table(workbook, "A-12_Receptors")
    receptor = next(dict(zip(receptor_header, r)) for r in receptor_rows if r[0] == receptor_id)
    met_header, met_rows = table(workbook, "A-9_Meteorological")
    meteorology = dict(zip(met_header, next(r for r in met_rows if r[0] is not None)))
    runway_header, runway_rows = table(workbook, "A-10_Runway")
    # The reference workbook defines one runway axis whose origin is the SOR
    # shared by its straight arrival/departure profiles.
    runway_id = "09"
    runway = dict(zip(runway_header, next(r for r in runway_rows if str(r[0]).strip() == runway_id)))
    sel_header, sel_rows = table(workbook, "B-1_SEL_Results")
    expected_row = next(r for r in sel_rows if r[0] == case_id and r[1] == receptor_id)
    segment_header, segment_rows = table(workbook, "B-2_Segment_Results")
    segments = [dict(zip(segment_header, r)) for r in segment_rows if r[0] == case_id and r[1] == receptor_id]
    if not all((profile, npd, route, segments)):
        raise ValueError(f"official workbook lacks required inputs/results for {case_id}/{receptor_id}")
    return {
        "schema": "ecac_doc29_5e_part1_named_reference_case_v1",
        "official_test_case": {
            "case_id": case_id, "aircraft_id": aircraft_id, "operation": operation,
            "route_id": route_id, "route_description": "straight arrival" if operation == "A" else "straight departure",
            "receptor_id": receptor_id, "receptor_description": receptor["Receptor Description"],
            "expected_metric": "A-weighted SEL (dB re 1 second)", "expected_total_sel_db": expected_row[2],
        },
        "aircraft": aircraft,
        "units": {"route_coordinates":"metres in a flat-earth x/y grid", "profile_distance_altitude":"metres",
                  "profile_speed":"metres/second", "profile_thrust":"corrected net thrust or shaft horsepower percent per engine, as specified by aircraft metadata",
                  "npd_power":str(aircraft.get("Power Parameter")), "npd_distance":"feet in workbook, metres in model",
                  "npd_levels":"A-weighted SEL dB re 1 second", "segment_results":"published workbook outputs"},
        "meteorology": meteorology, "runway_09": runway,
        f"route_{route_id}": route, "receptor": receptor, "fixed_point_profile": profile,
        "npd_arrival_sel_curves": npd, "npd_arrival_lamax_curves": npd_lamax,
        "published_segment_results": segments,
        "source": {"source_url":"https://ecac-ceac.org/images/documents/Doc29-5th_Edition-Volume_3_Part_1-Workbook.xlsx",
                   "workbook_file":workbook.name,"workbook_sha256":hashlib.sha256(workbook.read_bytes()).hexdigest(),
                   "source_sheet_inputs":["A-1_Aircraft","A-6_Fixed_Point_Profiles","A-7_NPD_Curves","A-9_Meteorological","A-10_Runway","A-11_Routes","A-12_Receptors"],
                   "source_sheet_results":["B-1_SEL_Results","B-2_Segment_Results"],
                   "data_role":"Published independent reference inputs/results; B-2/B-1 are comparison targets only."},
    }


def main():
    p=argparse.ArgumentParser(); p.add_argument("--workbook",type=Path,required=True); p.add_argument("--case",required=True); p.add_argument("--receptor",required=True); p.add_argument("--output",type=Path,required=True); a=p.parse_args()
    result=extract(a.workbook,a.case,a.receptor); a.output.parent.mkdir(parents=True,exist_ok=True); a.output.write_text(json.dumps(result,indent=2,ensure_ascii=False)+"\n")
    print(json.dumps({"case":a.case,"receptor":a.receptor,"segments":len(result["published_segment_results"]),"expected_sel_db":result["official_test_case"]["expected_total_sel_db"],"output":str(a.output)}))


if __name__ == "__main__": main()
