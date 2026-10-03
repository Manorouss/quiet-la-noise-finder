"""Internal single-day PA28 fixed-profile scenario at hypothetical VNY receivers.

Activity is used only to select and time geometry-inferred candidate windows;
actual aircraft positions/altitudes are not fitted to the acoustic profile.
The EASA ANP archive is used internally and no raw ANP rows are emitted.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
from zipfile import ZipFile
import csv
from io import TextIOWrapper

from .anp_loader import load_anp_npd_table
from .doc29 import energy_sum_db, npd_level
from .independent_profile_acoustics import _segment_geometry
from .profile_geometry import segment_profile_points
from .segment_reference import FT_TO_M, KNOT_TO_MPS, _finite_segment_correction, _impedance_adjustment, _lateral_attenuation


def _sha(path: Path) -> str:
    h=hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda:f.read(1<<20),b""):h.update(b)
    return h.hexdigest()


def load_fixed_profile(archive: Path, operation: str) -> tuple[list[dict],dict]:
    if operation not in ("A","D"):raise ValueError("operation must be A or D")
    with ZipFile(archive) as z:
        with z.open("ANP2.3_Default_fixed_point_profiles.csv") as raw:
            rows=list(csv.DictReader(TextIOWrapper(raw,encoding="cp1252",newline=""),delimiter=";"))
        with z.open("ANP2.3_Aircraft.csv") as raw:
            aircraft=list(csv.DictReader(TextIOWrapper(raw,encoding="cp1252",newline=""),delimiter=";"))
        with z.open("ANP2.3_Default_weights.csv") as raw:
            weights=list(csv.DictReader(TextIOWrapper(raw,encoding="cp1252",newline=""),delimiter=";"))
    selected=[r for r in rows if r["ACFT_ID"].strip()=="PA28" and r["Op Type"].strip()==operation and r["Profile_ID"].strip()=="DEFAULT" and r["Stage Length"].strip()=="1"]
    if not selected:raise ValueError(f"no PA28 default stage-1 fixed-point profile for {operation}")
    selected.sort(key=lambda r:int(r["Point Number"]))
    pts=[{"s_m":float(r["Distance (ft)"])*FT_TO_M,"z_m":float(r["Altitude AFE (ft)"])*FT_TO_M,
          "speed_mps":float(r["TAS (kt)"])*KNOT_TO_MPS,"power":float(r["Power Setting"]),
          "bank_deg":0.0,"source_point":int(r["Point Number"])} for r in selected]
    ac=next(r for r in aircraft if r["ACFT_ID"].strip()=="PA28")
    w=[r for r in weights if r["ACFT_ID"].strip()=="PA28" and r["Stage Length"].strip()=="1"]
    expected_op=operation
    w=next((float(r["Weight (lb)"]) for r in w if r.get("Operation",expected_op).strip() in (expected_op,"")),None)
    if w is None:raise ValueError(f"no stage-1 PA28 default weight for {operation}")
    if ac["Power Parameter"].strip()!="Other (RPM)":raise ValueError("expected PA28 NPD power variable RPM")
    return pts,{"aircraft_id":"PA28","description":ac["Description"],"engine_type":ac["Engine Type"],"number_of_engines":int(ac["Number Of Engines"]),
                "weight_lb":w,"power_parameter":"RPM per engine","profile_id":"DEFAULT","stage_length":1,"operation":operation,
                "fixed_profile_points":len(pts)}


def segment_profile(points: list[dict], operation: str) -> list[dict]:
    constructed=segment_profile_points(points, operation=="A")
    segments=[]
    for i,(a,b) in enumerate(zip(constructed,constructed[1:]),1):
        length=math.hypot(float(b["s_m"])-float(a["s_m"]),float(b["z_m"])-float(a["z_m"]))
        if length<=0:raise ValueError("profile has nonpositive segment length")
        segments.append({"segment_id":i,"start":a,"end":b,"length_3d_m":length})
    return segments


def _finite_delta_with_groundroll_case(sel: float, lamax: float, q_ft: float, length_ft: float, operation: str, ground_roll: bool) -> tuple[float,str]:
    # Doc 29 Vol. 2 §4.5.6 Eq. (4-21a/b) are algebraically the q=0/q=λ
    # limits of Eq. (4-20); use them only at those exact endpoint geometries.
    if ground_roll and operation=="D" and q_ft <= 1e-7:
        d_lambda=(2/math.pi)*270.05*10**((sel-lamax)/10)
        alpha=length_ft/d_lambda
        frac=(alpha/(1+alpha*alpha)+math.atan(alpha))/math.pi
        return 10*math.log10(frac),"Doc29_4-21a_takeoff_ground_roll_q_zero_reference_point"
    if ground_roll and operation=="A" and q_ft>=length_ft-1e-7:
        d_lambda=(2/math.pi)*270.05*10**((sel-lamax)/10)
        alpha=-length_ft/d_lambda
        frac=(-alpha/(1+alpha*alpha)-math.atan(alpha))/math.pi
        return 10*math.log10(frac),"Doc29_4-21b_q_equals_segment_length_landing_ground_roll"
    return _finite_segment_correction(sel,lamax,q_ft,length_ft),"Doc29_4-20_generic_finite_segment"


def _event_metric(event: dict, profile: list[dict], sel_table, max_table, receiver: tuple[float,float,float], operation: str) -> dict:
    calculated=[]
    for seg in profile:
        g=_segment_geometry(seg,receiver,operation)
        d=float(g["perpendicular_distance_m"]); power=float(g["power_lb_per_engine"]); speed=float(g["speed_mps"])
        if speed<=0:raise ValueError("profile has nonpositive true speed")
        npd_d=max(30.0,d)
        aircraft=sel_table.aircraft_id; npd=sel_table.npd_id
        base=npd_level(sel_table,aircraft_id=aircraft,npd_id=npd,metric="SEL",operation=operation,
                       power=power,power_unit=sel_table.power_unit,distance_m=d,allow_power_extrapolation=True,minimum_distance_m=30.0)
        lmax=npd_level(max_table,aircraft_id=aircraft,npd_id=max_table.npd_id,metric="LAmax",operation=operation,
                       power=power,power_unit=max_table.power_unit,distance_m=d,allow_power_extrapolation=True,minimum_distance_m=30.0)
        # For a straight equivalent level path, h=sqrt(dp²-λ²); β=acos(λ/dp).
        # Doc 29 §4.5.2 sets β=0 if the receiver is above the segment CPA.
        lateral=float(g["lateral_displacement_m"])
        # Use the shared Doc 29 §4.5.5 equivalent-level projection for beta.
        # An infinite-path dp shortcut is wrong for behind/ahead endpoints.
        beta=float(g["beta_deg"])
        lateral_db=_lateral_attenuation(beta,lateral)
        duration=10*math.log10((160.0*KNOT_TO_MPS)/speed)
        q_ft=float(g["q_m"])/FT_TO_M; length_ft=float(g["length_m"])/FT_TO_M
        ground=seg["start"]["z_m"]<=1.0 and seg["end"]["z_m"]<=1.0
        finite,finite_method=_finite_delta_with_groundroll_case(base,lmax,q_ft,length_ft,operation,ground)
        # Doc 29 Vol. 2 §4.5.3 Eq. (4-16): propeller lateral installation directivity is 0.
        install=0.0
        # Standard pressure/temperature still gives the Doc 29 impedance ratio
        # 416.86/409.81, i.e. +0.074077 dB (not zero).
        impedance=_impedance_adjustment(15.0,760.0)
        level=base+impedance+duration+install-lateral_db+finite
        calculated.append({"segment_id":seg["segment_id"],"npd_sel_db_internal":base,"npd_lamax_db_internal":lmax,
                           "q_m":g["q_m"],"length_m":g["length_m"],"perpendicular_distance_m":d,"npd_distance_used_m":npd_d,
                           "lateral_displacement_m":lateral,"beta_deg":beta,"speed_mps":speed,"power_rpm_per_engine":power,
                           "duration_correction_db":duration,"installation_correction_db":install,
                           "lateral_attenuation_db":lateral_db,"finite_segment_correction_db":finite,
                           "finite_segment_method":finite_method,"acoustic_impedance_adjustment_db":impedance,
                           "ground_roll_segment":ground,"segment_sel_db":level})
    total=energy_sum_db(r["segment_sel_db"] for r in calculated)
    return {"candidate_event_index_internal":event["event_index_internal"],"operation":event["operation"],
            "event_time_utc":event["event_time_utc"],"observed_candidate_window":event["event_window"],
            "runway_axis_heading_deg_true":event["runway_axis_heading_deg_true"],
            "parallel_runway_end_candidates":event["runway_end_candidates"],
            "event_sel_db":total,"segment_count":len(calculated),"segment_levels_internal":calculated}


RECEIVERS=(
    {"name":"HYP_R34_SIDE_100M","x_along_from_threshold_m":0.0,"y_right_of_track_m":100.0},
    {"name":"HYP_R34_SIDE_500M","x_along_from_threshold_m":0.0,"y_right_of_track_m":500.0},
    {"name":"HYP_R34_SIDE_1000M","x_along_from_threshold_m":0.0,"y_right_of_track_m":1000.0},
)
HEIGHTS_M=(1.5,10.0)


def run(events_path: Path, archive: Path) -> dict:
    events_doc=json.loads(events_path.read_text())
    all_p28=[e for e in events_doc["events_internal_only"] if e["type_code"]=="P28A"]
    # Use only ICAO ADS-B across the sequence required to infer the event.
    def witnesses(e):
        keys=("approach_witness","ground_witness") if e["operation"]=="arrival" else ("ground_witness","climb_base_witness","climb_witness")
        return [e[k] for k in keys]
    selected=[e for e in all_p28 if all(w.get("position_source")=="adsb_icao" for w in witnesses(e))]
    p28_pre_filter=Counter(e["operation"] for e in all_p28)
    p28_post_filter=Counter(e["operation"] for e in selected)
    p28_excluded=Counter(e["operation"] for e in all_p28 if e not in selected)
    # Event identity is the already deduplicated event window, with a local
    # stable index assigned only after internal identity/source ID was stripped.
    for i,e in enumerate(sorted(selected,key=lambda r:(r["operation"],r["event_window"]["start_utc"],r["event_window"]["end_utc"])),1):e["event_index_internal"]=i
    for e in selected:e["event_time_utc"]=e["ground_witness"]["timestamp_utc"]
    by_operation=Counter(e["operation"] for e in selected)
    modeled=[]; profile_summaries={}
    # The PA28 arrival fixed profile's final landing-roll segment falls below
    # the arrival NPD's minimum RPM and exceeds Doc 29's 5 dB extrapolation
    # caution. Keep that complete operation out of the numeric scenario rather
    # than clipping RPM or silently dropping its tail. Departures stay within
    # the tabulated NPD envelope and form the first fully supported scenario.
    modeled_operations=("D",)
    for op in modeled_operations:
        profile_raw,profile_info=load_fixed_profile(archive,op)
        profile=segment_profile(profile_raw,op)
        match,sel=load_anp_npd_table(archive,"PA28","SEL",op)
        maxmatch,lamax=load_anp_npd_table(archive,"PA28","LAmax",op)
        if sel.power_unit!="rpm_per_engine" or lamax.power_unit!="rpm_per_engine":raise ValueError("PA28 RPM units mismatch")
        profile_summaries[op]={"model_aircraft":"PA28","operation":op,"fixed_profile_point_count":profile_info["fixed_profile_points"],
                               "constructed_segment_count":len(profile),"distance_start_to_end_km":(profile_raw[-1]["s_m"]-profile_raw[0]["s_m"])/1000,
                               "height_range_AFE_m":[min(p["z_m"] for p in profile_raw),max(p["z_m"] for p in profile_raw)],
                               "airspeed_range_mps":[min(p["speed_mps"] for p in profile_raw),max(p["speed_mps"] for p in profile_raw)],
                               "power_range_rpm_per_engine":[min(p["power"] for p in profile_raw),max(p["power"] for p in profile_raw)],
                               "npd_aircraft_id_internal":match.requested_aircraft_id,"npd_id_internal":match.npd_id,
                               "npd_power_unit":sel.power_unit,"npd_power_settings_internal":list(sel.power_settings),
                               "npd_distance_min_m":min(sel.distances_m),"npd_distance_max_m":max(sel.distances_m),
                               "profile_data_usage":"EASA ANP v2.3 default fixed-point profile, stage length 1; no raw point rows emitted."}
        for e in selected:
            if e["operation"]!={"A":"arrival","D":"departure"}[op]:continue
            for rec in RECEIVERS:
                for z in HEIGHTS_M:
                    receiver=(rec["x_along_from_threshold_m"],rec["y_right_of_track_m"],z)
                    result=_event_metric(e,profile,sel,lamax,receiver,op)
                    result["receiver"]={**rec,"height_above_flat_runway_datum_m":z,"coordinates_are_hypothetical_local_track_offsets":True}
                    result["unit"]="A-weighted SEL, dB re 1 second"
                    modeled.append(result)
    aggregate={}
    for rec in RECEIVERS:
        for z in HEIGHTS_M:
            name=f"{rec['name']}_H{z:g}M"
            rows=[r for r in modeled if r["receiver"]["name"]==rec["name"] and r["receiver"]["height_above_flat_runway_datum_m"]==z]
            aggregate[name]={"candidate_event_window_count_not_confirmed_operations":len(rows),
                             "unique_arrival_window_count":sum(r["operation"]=="arrival" for r in rows),
                             "unique_departure_window_count":sum(r["operation"]=="departure" for r in rows),
                             "energy_sum_of_this_candidate_window_subset_sel_db":energy_sum_db(r["event_sel_db"] for r in rows) if rows else None}
    return {"schema":"quiet_la_vny_pa28_fixed_profile_scenario_v3_internal",
            "activity_input":{"file":events_path.name,"sha256":_sha(events_path),"source_activity_sha256":events_doc["activity_sha256"],
                              "candidate_event_windows_by_operation_after_quality_filter_not_confirmed_operations":dict(by_operation),
                              "event_window_inventory_before_P28A_filter":{"continuous_regional_fragments_examined_not_flights":events_doc["counts"]["continuous_regional_path_fragments_examined_not_flights"],
                                  "raw_runway_end_proposals_before_dedupe":events_doc["counts"]["raw_runway_end_proposals_before_dedupe"],
                                  "deduplicated_candidate_windows_not_confirmed_movements":events_doc["counts"]["unique_event_windows_after_exact_witness_dedup_not_confirmed_movements"],
                                  "parallel_runway_end_copies_collapsed":events_doc["counts"]["collapsed_parallel_runway_end_copies"],
                                  "all_type_operation_inferences_for_P28A":dict(p28_pre_filter),
                                  "P28A_windows_retained_after_ADSB_ICAO_required_witness_filter":dict(p28_post_filter),
                                  "P28A_windows_excluded_for_non_ADSB_ICAO_required_witness":dict(p28_excluded)},
                              "filter":"P28A events with ADS-B ICAO position source at every required approach/surface or surface/climb witness; exact/parallel duplicate windows already removed by event-window extractor."},
            "anp_input":{"file":archive.name,"sha256":_sha(archive),"license_status":"internal use only, subject to EASA terms; do not redistribute raw NPD/profile rows"},
            "mapping_assumption":{"activity_icao_type":"P28A","anp_model_variant":"PA28 Piper Warrior PA-28-161 / O-320-D3G","kind":"explicit manual family/variant scenario, not exact aircraft match and not official ICAO substitution-table row","variant_or_engine_delta_db":0.0,"delta_applied":False,"N_adjustment_applied":False,"reason":"Root authorized this bounded internal sensitivity scenario. No invented correction is added; the profile and NPD are the named PA28 model's own ANP values."},
            "profile_by_operation":profile_summaries,
            "operation_applicability":{
                "modeled_operations":list(modeled_operations),
                "excluded_arrivals":{"candidate_event_windows_after_quality_filter":by_operation.get("arrival",0),
                                     "status":"not modeled: PA28 arrival profile has final landing-roll RPM below the NPD table's tabulated range, and Doc 29's >5 dB extrapolation caution is exceeded",
                                     "affected_constructed_profile_segment_1_based":17,
                                     "power_range_rpm_per_engine_on_final_segment":[1000.0,1166.6666666667],
                                     "NPD_arrival_power_range_rpm_per_engine":[1500.0,1800.0],
                                     "midpoint_extrapolation_difference_from_nearest_curve_db_at_30m":-5.407124832454173,
                                     "endpoint_extrapolation_difference_from_nearest_curve_db_at_30m":-6.488549798945016,
                                     "policy":"No power clipping, extrapolation guard bypass, or partial-event segment omission."}
            },
            "receiver_height_sensitivity_m":list(HEIGHTS_M),"receivers":list(RECEIVERS),
            "assumptions":{"event_geometry":"Each candidate event selects operation and runway axis only. Acoustic path is the ANP default PA28 fixed-point profile aligned straight to FAA VNY runway-34 axis; ADS-B positions/heights are not fitted into the path.",
                            "runway_assignment":"All modeled P28A events infer runway-34 direction; where L/R endpoints are ambiguous, report the parallel candidates and model relative to each candidate threshold axis. Receptors are local relative offsets, not geocoded sites.",
                            "vertical_datum":"Source heights are ANP profile height AFE above a local flat runway zero; receiver height is 1.5 or 10 m above the same flat datum. No ADS-B ellipsoid altitude is converted to MSL/geoid and no real microphone height is asserted.",
                            "atmosphere":"15 °C and 760 mmHg; no station-weather correction or wind adjustment. Doc 29 impedance correction is +0.074077 dB relative to the 409.81 reference acoustic impedance.",
                            "propeller_terms":"PA28 is piston-propeller; Doc 29 Vol. 2 §4.5.3 Eq. (4-16) sets installation directivity to zero. No turbofan/turboprop start-of-roll directivity is applied; hypothetical receivers lie to the side of the modeled SOR axis.",
                            "ground_and_terrain":"ANP NPD data are used with Doc 29's 30 m distance floor. Acoustic profile ground source height is floored at 1 m. Flat soft-ground NPD assumption; no terrain/building blockage or local reflection is added.",
                            "operation_count":"Counts are unique deduplicated inferred event windows after witness and position-source quality filtering; each is a candidate, not a confirmed aircraft movement. Summed event SEL is only the modeled subset exposure sum, not daily SEL/LAeq/CNEL or a lower bound on actual noise."},
            "results":{"modeled_event_receiver_rows":len(modeled),"candidate_event_energy_sums_by_hypothetical_receiver":aggregate,
                      "event_sel_by_candidate_window_internal_only":modeled},
            "validation_status":"Scenario execution only for PA28 fixed-profile departures; no LAWA Jan 15 values used. The JETFAS/R18 full arrival reference case passed independently; additional official lateral/departure receptor qualification is recorded separately before calling this broad Doc 29 validation.",
            "model_scope":"Straight, unbanked, default fixed-profile propagation with Doc 29 NPD, duration, lateral attenuation, finite-segment and standard-air impedance terms. No atmosphere gradients, terrain, buildings, procedure synthesis, turns, or full activity coverage."}


def main():
    p=argparse.ArgumentParser();p.add_argument("--events",type=Path,required=True);p.add_argument("--anp",type=Path,required=True);p.add_argument("--output",type=Path,required=True);a=p.parse_args()
    result=run(a.events,a.anp);a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(result,indent=2)+"\n")
    print(json.dumps({"input_counts":result["activity_input"]["candidate_event_windows_by_operation_after_quality_filter_not_confirmed_operations"],"profiles":result["profile_by_operation"],"aggregate":result["results"]["candidate_event_energy_sums_by_hypothetical_receiver"]},indent=2))


if __name__=="__main__":main()
