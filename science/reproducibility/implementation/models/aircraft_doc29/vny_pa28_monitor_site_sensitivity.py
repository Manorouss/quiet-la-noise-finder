"""Internal PA28 subset sensitivity at historical VNY monitor coordinates.

This is not a monitor validation run: site positions are from a 2021 schedule,
heights and vertical alignment are unknown, observations are carried as context
only, and the predicted exposure includes only qualified inferred P28A departure
candidate windows. No aircraft identifiers are emitted.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime
import hashlib
import json
import math
from collections import Counter
from pathlib import Path
from zoneinfo import ZoneInfo
from zipfile import ZipFile

from .anp_loader import load_anp_npd_table
from .doc29 import energy_sum_db
from .segment_reference import FT_TO_M, KNOT_TO_MPS
from .vny_pa28_fixed_profile_scenario import (
    _event_metric, load_fixed_profile, segment_profile,
)

HEIGHTS_M = (1.5, 4.0, 6.0)
VERTICAL_OFFSETS_M = (-5.0, 0.0, 5.0)  # illustrative sensitivity grid, not an actual site-elevation bound
LA_TZ = ZoneInfo("America/Los_Angeles")
CNEL_PERIOD_WEIGHTS = {"day": 1.0, "evening": 3.0, "night": 10.0}
F = 1.0 / 298.257223563
A = 6378137.0
B = A * (1.0 - F)


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _wgs84_inverse(lat1: float, lon1: float, lat2: float, lon2: float) -> tuple[float, float]:
    """Vincenty inverse on WGS84; return initial azimuth degrees and metres."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    L = math.radians(lon2 - lon1)
    U1, U2 = math.atan((1-F)*math.tan(phi1)), math.atan((1-F)*math.tan(phi2))
    sinU1, cosU1, sinU2, cosU2 = math.sin(U1), math.cos(U1), math.sin(U2), math.cos(U2)
    lam = L
    for _ in range(100):
        sin_lam, cos_lam = math.sin(lam), math.cos(lam)
        sin_sigma = math.sqrt((cosU2*sin_lam)**2 + (cosU1*sinU2-sinU1*cosU2*cos_lam)**2)
        if sin_sigma == 0:
            return 0.0, 0.0
        cos_sigma = sinU1*sinU2 + cosU1*cosU2*cos_lam
        sigma = math.atan2(sin_sigma, cos_sigma)
        sin_alpha = cosU1*cosU2*sin_lam/sin_sigma
        cos2_alpha = 1.0 - sin_alpha*sin_alpha
        cos2_sigma_m = 0.0 if cos2_alpha < 1e-15 else cos_sigma - 2*sinU1*sinU2/cos2_alpha
        C = F/16*cos2_alpha*(4+F*(4-3*cos2_alpha))
        new_lam = L + (1-C)*F*sin_alpha*(sigma+C*sin_sigma*(cos2_sigma_m+C*cos_sigma*(-1+2*cos2_sigma_m**2)))
        if abs(new_lam-lam) < 1e-12:
            lam = new_lam
            break
        lam = new_lam
    else:
        raise ValueError("WGS84 inverse did not converge")
    u_sq = cos2_alpha*(A*A-B*B)/(B*B)
    cap_a = 1+u_sq/16384*(4096+u_sq*(-768+u_sq*(320-175*u_sq)))
    cap_b = u_sq/1024*(256+u_sq*(-128+u_sq*(74-47*u_sq)))
    delta_sigma = cap_b*sin_sigma*(cos2_sigma_m+cap_b/4*(cos_sigma*(-1+2*cos2_sigma_m**2)-
        cap_b/6*cos2_sigma_m*(-3+4*sin_sigma**2)*(-3+4*cos2_sigma_m**2)))
    distance = B*cap_a*(sigma-delta_sigma)
    azimuth = math.degrees(math.atan2(cosU2*math.sin(lam), cosU1*sinU2-sinU1*cosU2*math.cos(lam))) % 360
    return azimuth, distance


def _local_track_offsets(threshold: dict, site: dict, heading_deg: float) -> tuple[float, float, dict]:
    bearing, distance = _wgs84_inverse(float(threshold["lat"]), float(threshold["lon"]),
                                       float(site["latitude_decimal"]), float(site["longitude_decimal"]))
    delta = math.radians((bearing - heading_deg + 180.0) % 360.0 - 180.0)
    along = distance * math.cos(delta)
    right = distance * math.sin(delta)
    return along, right, {"initial_bearing_from_threshold_deg_true": bearing,
                           "geodesic_distance_m": distance}


def _local_daypart(timestamp_utc: str) -> str:
    instant = datetime.fromisoformat(timestamp_utc.replace("Z", "+00:00"))
    if instant.tzinfo is None:
        raise ValueError("event timestamp must include an offset")
    local = instant.astimezone(LA_TZ)
    minute = local.hour * 60 + local.minute
    if 7 * 60 <= minute < 19 * 60:
        return "day"
    if 19 * 60 <= minute < 22 * 60:
        return "evening"
    return "night"


def _subset_cnel(event_rows: list[dict]) -> dict:
    """Compute candidate-subset CNEL with local dayparts and 24-hour normalization."""
    energies = {part: 0.0 for part in CNEL_PERIOD_WEIGHTS}
    counts = Counter()
    for row in event_rows:
        part = _local_daypart(row["event_time_utc"])
        counts[part] += 1
        energies[part] += CNEL_PERIOD_WEIGHTS[part] * 10.0 ** (row["event_sel_db_internal"] / 10.0)
    weighted = sum(energies.values())
    if not weighted:
        raise ValueError("cannot compute CNEL from no events")
    return {
        "method": "10log10(sum(10^(event_SEL/10) * daypart_weight) / 86400); event timestamp classified in America/Los_Angeles local time",
        "normalization_seconds": 86400,
        "candidate_event_counts_by_daypart": dict(counts),
        "weighted_energy_by_daypart_internal": energies,
        "weighted_energy_fraction_by_daypart": {k: v / weighted for k, v in energies.items()},
        "subset_event_cnel_db_internal": 10.0 * math.log10(weighted / 86400.0),
        "interpretation": "partial P28A candidate-event subset only; not a complete daily CNEL estimate or a lower bound",
    }


def _read_sites(site_csv: Path, obs_csv: Path) -> tuple[list[dict], dict]:
    with site_csv.open(newline="", encoding="utf-8") as f:
        sites = {r["station_id"]: r for r in csv.DictReader(f)}
    obs = {}
    with obs_csv.open(newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["report_calendar_date"] == "2026-01-15":
                obs[r["station_id"]] = r
    result = []
    for sid, row in sites.items():
        if row["coordinate_status"] != "valid_as_printed":
            result.append({"station_id": sid, "coordinate_status": row["coordinate_status"],
                           "coordinate_vintage": row["source_vintage"], "spatial_modeling_status": "excluded_unresolved_coordinate",
                           "observed_daily_cnel_context_db": int(obs[sid]["daily_local_aircraft_cnel_db"]) if sid in obs and obs[sid]["daily_local_aircraft_cnel_db"] else None})
            continue
        result.append({"station_id":sid,"latitude_decimal":float(row["latitude_decimal"]),
                       "longitude_decimal":float(row["longitude_decimal"]),"coordinate_status":row["coordinate_status"],
                       "coordinate_vintage":row["source_vintage"],"active_in_2021_schedule":row["active_in_2021_schedule"],
                       "microphone_height_m":None,
                       "observed_daily_cnel_context_db":int(obs[sid]["daily_local_aircraft_cnel_db"]) if sid in obs and obs[sid]["daily_local_aircraft_cnel_db"] else None,
                       "observation_metric":None if sid not in obs else obs[sid]["metric_name"],
                       "observation_timezone":None if sid not in obs else obs[sid]["timezone"]})
    return result, obs


def run(events_path: Path, archive: Path, site_csv: Path, obs_csv: Path,
        runway_ends_path: Path) -> dict:
    events_doc=json.loads(events_path.read_text())
    runway_doc=json.loads(runway_ends_path.read_text())
    runway_ends={str(f["attributes"]["RWY_END_ID"]):f["attributes"] for f in runway_doc["features"]
                 if f["attributes"].get("ARPT_ID")=="VNY"}
    if not {"16L","16R","34L","34R"}.issubset(runway_ends):
        raise ValueError("FAA runway-end capture lacks VNY parallel runway thresholds")
    for attr in runway_ends.values():
        if attr.get("EFF_DATE") != "2026/10/01":
            raise ValueError("unexpected FAA runway geometry vintage")
    sites, observations = _read_sites(site_csv, obs_csv)
    candidate=[e for e in events_doc["events_internal_only"] if e["type_code"]=="P28A" and e["operation"]=="departure"]
    def required_witnesses(e): return [e[k] for k in ("ground_witness","climb_base_witness","climb_witness")]
    selected=[e for e in candidate if all(w.get("position_source")=="adsb_icao" for w in required_witnesses(e))]
    if len(selected)!=26:
        raise ValueError(f"expected 26 source-qualified P28A departure candidates, found {len(selected)}")
    selected.sort(key=lambda e:(e["event_window"]["start_utc"],e["event_window"]["end_utc"]))
    for i,e in enumerate(selected,1):
        if not e["runway_end_candidates"] or any(x not in runway_ends for x in e["runway_end_candidates"]):
            raise ValueError("event candidate runway end missing from FAA runway-end capture")
        e["event_index_internal"]=i
        e["event_time_utc"]=e["ground_witness"]["timestamp_utc"]
    raw_profile, profile_info=load_fixed_profile(archive,"D")
    profile=segment_profile(raw_profile,"D")
    _,sel=load_anp_npd_table(archive,"PA28","SEL","D")
    _,lamax=load_anp_npd_table(archive,"PA28","LAmax","D")
    receivers=[]
    for site in sites:
        if site.get("spatial_modeling_status"):
            continue
        receiver_offsets={}
        for end_id in sorted({x for e in selected for x in e["runway_end_candidates"]}):
            attr=runway_ends[end_id]
            threshold={"lat":float(attr["LAT_DECIMAL"]),"lon":float(attr["LONG_DECIMAL"])}
            heading=float(attr["TRUE_ALIGNMENT"])
            x,y,position=_local_track_offsets(threshold,site,heading)
            receiver_offsets[end_id]={"runway_end_id":end_id,"runway_axis_heading_deg_true":heading,
                                      "threshold_coordinate_source":"FAA NASR runway-end capture effective 2026-10-01; FAA NGS position date 2011-11-29",
                                      "threshold_latitude":threshold["lat"],"threshold_longitude":threshold["lon"],
                                      "receiver_along_axis_m":x,"receiver_right_of_track_m":y,**position}
        site_result={**site,"site_currentness":"2021 coordinates; current 2026 position not independently confirmed",
                     "receiver_offsets_by_FAA_runway_end":receiver_offsets,"assumed_microphone_heights_m":list(HEIGHTS_M),
                     "receiver_vertical_reference":"flat runway-relative datum: aircraft profile heights are ANP AFE; receiver z is explicitly assumed metres above the same local flat plane. Site ground elevation and mic height are unknown; no geoid/DEM correction is inferred.",
                     "runway_axis_scenarios":[]}
        for choice in ("L","R"):
            per_height=[]
            assigned_ends=Counter()
            chosen_end_by_index={}
            for e in selected:
                candidates=e["runway_end_candidates"]
                if len(candidates)==1:
                    end_id=candidates[0]
                else:
                    matching=[c for c in candidates if c.endswith(choice)]
                    if len(matching)!=1:
                        raise ValueError(f"cannot resolve {choice}-side parallel runway candidate")
                    end_id=matching[0]
                chosen_end_by_index[e["event_index_internal"]]=end_id
                assigned_ends[end_id]+=1
            for height in HEIGHTS_M:
                for vertical_offset in VERTICAL_OFFSETS_M:
                    event_rows=[]
                    for e in selected:
                        candidates=e["runway_end_candidates"]
                        end_id=chosen_end_by_index[e["event_index_internal"]]
                        variant=receiver_offsets[end_id]
                        receiver=(variant["receiver_along_axis_m"],variant["receiver_right_of_track_m"],height+vertical_offset)
                        row=_event_metric(e,profile,sel,lamax,receiver,"D")
                        daypart=_local_daypart(row["event_time_utc"])
                        event_rows.append({"candidate_event_index_internal":row["candidate_event_index_internal"],
                                           "event_time_utc":row["event_time_utc"],"operation":"departure",
                                           "local_daypart":daypart,"cnel_event_weight":CNEL_PERIOD_WEIGHTS[daypart],
                                           "runway_end_used_under_parallel_assignment_scenario":end_id,
                                           "candidate_runway_ends":candidates,
                                           "event_sel_db_internal":row["event_sel_db"]})
                    subset=energy_sum_db(r["event_sel_db_internal"] for r in event_rows)
                    cnel=_subset_cnel(event_rows)
                    observed=site["observed_daily_cnel_context_db"]
                    per_height.append({"assumed_microphone_height_m_above_site_plane":height,
                                       "assumed_site_plane_vertical_offset_from_runway_plane_m":vertical_offset,
                                       "unique_inferred_candidate_windows_not_confirmed_movements":len(event_rows),
                                       "subset_event_energy_sum_sel_db_internal":subset,
                                       "subset_daily_cnel_sensitivity":cnel,
                                       "observed_total_aircraft_daily_cnel_context_db":observed,
                                       "subset_cnel_minus_observed_total_db_exploratory_only":None if observed is None else cnel["subset_event_cnel_db_internal"]-observed,
                                       "comparison_interpretation":"exploratory consistency diagnostic only; activity, type/profile, site datum/currentness, microphone height and report conventions are incomplete or uncertain; this is not validation, calibration, or a lower bound",
                                       "event_rows_internal_only":event_rows})
            site_result["runway_axis_scenarios"].append({"scenario":"assign ambiguous parallel candidates to runway-end suffix "+choice,
                "assignment_rule":"single candidate keeps its inferred end; paired left/right candidate events use L or R respectively",
                "assigned_candidate_counts_by_end":dict(assigned_ends),"predictions_by_height":per_height})
        receivers.append(site_result)
    return {
        "schema":"quiet_la_vny_pa28_monitor_site_height_datum_sensitivity_v1_internal",
        "date_local":"2026-01-15","utc_window":{"start_inclusive":"2026-01-15T08:00:00Z","end_exclusive":"2026-01-16T08:00:00Z"},
        "activity_input":{"file":events_path.name,"sha256":_sha(events_path),"source_activity_sha256":events_doc["activity_sha256"],
                          "source_qualified_P28A_departure_candidate_windows":len(selected),
                          "P28A_departure_candidates_before_witness_quality_filter":len(candidate),
                          "P28A_departure_candidates_excluded_for_non_ADSB_ICAO_required_witness":len(candidate)-len(selected),
                          "candidate_classifier_status":"inferred runway-corridor operations, not confirmed movements; observed geometric altitude datum and full path coverage remain partial",
                          "activity_completeness":"not established; no whole-day movement or aircraft-source completeness claim"},
        "aircraft_model":{"model_variant":"PA28 Piper Warrior PA-28-161 / O-320-D3G","type_mapping":"explicit P28A→PA28 scenario assumption; not exact type match, no Δ/N correction","profile":"EASA ANP v2.3 default fixed-point departure profile, stage length 1","profile_point_count":profile_info["fixed_profile_points"],"profile_segment_count":len(profile),
                          "input_archive_sha256":_sha(archive),"NPD_power_unit":sel.power_unit,"atmosphere":"15 C, 760 mmHg; no hourly weather correction","Doc29_reference_status":"JETFAS/R18, JETFDS/R02, and PROPDS/R02 cases reproduced separately; not a full Doc29 certification"},
        "measurement_context":{"source_observation_file":obs_csv.name,"source_observation_sha256":_sha(obs_csv),
             "report_label":"LAWA Daily Local A/C CNEL; values are carried untouched as context and exploratory consistency check",
             "comparison_performed":True,"reason":"candidate-subset CNEL is compared as an exploratory diagnostic against total aircraft CNEL; activity, type/profile, site datum/currentness, microphone height, and report conventions remain incomplete or uncertain",
             "observations_are_not_used_for_fitting_or_tuning":True,"valid_coordinate_sites":sum(1 for s in sites if "latitude_decimal" in s),
             "unresolved_coordinate_sites":sum(1 for s in sites if s.get("spatial_modeling_status")),"microphone_heights_documented":0},
        "receiver_height_datum_sensitivity":{"microphone_height_scenarios_m_above_site_plane":list(HEIGHTS_M),"site_plane_offset_scenarios_m_relative_to_runway_plane":list(VERTICAL_OFFSETS_M),
             "vertical_offset_limitation":"Offsets -5/0/+5 m are an illustrative sensitivity grid, not site elevations or a statistical bound. Actual site ground elevation, runway/site datum relation and microphone height remain unknown; no absolute AFE-to-site alignment is asserted."},
        "coverage":{"modeled_site_count":len(receivers),"excluded_site_ids":[s["station_id"] for s in sites if s.get("spatial_modeling_status")],
             "runway_axis_scenarios_per_site":2,"height_scenarios_per_axis":len(HEIGHTS_M),"vertical_offset_scenarios_per_height":len(VERTICAL_OFFSETS_M),"candidate_window_rows":len(receivers)*2*len(HEIGHTS_M)*len(VERTICAL_OFFSETS_M)*len(selected),
             "candidate_windows_per_site_runway_height":len(selected),"arrival_candidates_modeled":0,"other_aircraft_types_modeled":0,
             "metric":"subset A-weighted event SEL energy sum and Title 21 weighted candidate-subset CNEL sensitivity; not a complete aircraft/noise exposure estimate"},
        "receivers":receivers,
        "source_hashes":{"monitor_coordinates_csv_sha256":_sha(site_csv),"runway_end_capture_sha256":_sha(runway_ends_path),"event_windows_sha256":_sha(events_path)}
    }


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--events",type=Path,required=True);p.add_argument("--anp",type=Path,required=True)
    p.add_argument("--sites",type=Path,required=True);p.add_argument("--observations",type=Path,required=True)
    p.add_argument("--runway-ends",type=Path,required=True);p.add_argument("--output",type=Path,required=True)
    a=p.parse_args(); result=run(a.events,a.anp,a.sites,a.observations,a.runway_ends)
    a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(result,indent=2)+"\n")
    print(json.dumps({"modeled_site_count":result["coverage"]["modeled_site_count"],"candidate_window_rows":result["coverage"]["candidate_window_rows"],"status":result["measurement_context"]["reason"]},indent=2))


if __name__=="__main__":main()
