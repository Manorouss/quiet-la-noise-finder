"""Bounded geometry-based candidate classifier for ADS-B paths near VNY.

Outputs candidate path fragments only, never confirmed operations or flight
counts. Parallel-runway ambiguity is deliberately retained.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path

from .reclip_paths import _iter_json_paths

EARTH_M = 6_371_000.0
MAX_TRACK_ERROR_DEG = 18.0
MAX_LATERAL_M = 350.0
MIN_APPROACH_RANGE_M = 1_000.0
MAX_APPROACH_RANGE_M = 12_000.0
MAX_TOUCHDOWN_DISTANCE_M = 900.0
MAX_RUNWAY_GROUND_DISTANCE_M = 1_200.0
MIN_AIRBORNE_SPEED_KT = 45.0
MIN_GROUND_SPEED_KT = 8.0


def _sha256(path: Path) -> str:
    h=hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda:f.read(1<<20),b""): h.update(chunk)
    return h.hexdigest()


def _local_xy(lat: float, lon: float, origin: tuple[float,float]) -> tuple[float,float]:
    lat0,lon0=origin
    return (math.radians(lon-lon0)*EARTH_M*math.cos(math.radians(lat0)),
            math.radians(lat-lat0)*EARTH_M)


def _angle_difference(a: float,b: float) -> float:
    return abs((a-b+180.0)%360.0-180.0)


def _dmsp(attrs: dict, stem: str, hemis: str) -> float | None:
    deg=attrs.get(f"{stem}_DEG"); minute=attrs.get(f"{stem}_MIN"); sec=attrs.get(f"{stem}_SEC")
    h=attrs.get(hemis)
    if deg is None or minute is None or sec is None or h not in ("N","S","E","W"):
        return None
    value=float(deg)+float(minute)/60+float(sec)/3600
    return -value if h in ("S","W") else value


def load_runways(query_path: Path) -> list[dict]:
    """Load FAA runway-end query; prefer displaced landing threshold if present."""
    doc=json.loads(query_path.read_text())
    result=[]
    for feature in doc["features"]:
        a=feature["attributes"]
        runway=a["RWY_END_ID"]
        alignment=float(a["TRUE_ALIGNMENT"])
        end=(float(a["LAT_DECIMAL"]),float(a["LONG_DECIMAL"]))
        displaced=(a.get("LAT_DISPLACED_THR_DECIMAL"),a.get("LONG_DISPLACED_THR_DECIMAL"))
        threshold=(float(displaced[0]),float(displaced[1])) if all(x is not None for x in displaced) else end
        result.append({"runway_end":runway,"alignment_deg_true":alignment,"threshold":threshold,
                       "end":end,"landing_threshold_is_displaced":threshold!=end,
                       "elevation_ft_msl":a.get("DSPL_THR_ELEV") if threshold!=end else a.get("RWY_END_ELEV"),
                       "elevation_source":a.get("DSPL_THR_ELEV_SOURCE") if threshold!=end else a.get("RWY_END_ELEV_SOURCE"),
                       "elevation_date":a.get("RWY_END_DSPL_THR_ELEV_DATE") if threshold!=end else a.get("RWY_END_ELEV_DATE")})
    if len(result)!=4: raise ValueError(f"Expected four VNY runway ends; received {len(result)}")
    return result


def _point_metrics(points: list[dict], runway: dict) -> list[dict]:
    threshold=runway["threshold"]
    ux,uy=_local_xy(*threshold,threshold)
    del ux,uy
    angle=math.radians(runway["alignment_deg_true"])
    along=(math.sin(angle),math.cos(angle))
    out=[]
    for p in points:
        x,y=_local_xy(float(p["lat"]),float(p["lon"]),threshold)
        s=x*along[0]+y*along[1]
        cross=x*along[1]-y*along[0]
        out.append({**p,"along_m":s,"cross_m":cross,"distance_to_threshold_m":math.hypot(x,y)})
    return out


def _candidate_for_runway(path: dict, rw: dict) -> dict:
    pts=_point_metrics(path["points"],rw)
    near=[(i,p) for i,p in enumerate(pts) if abs(p["cross_m"])<=MAX_LATERAL_M]
    corridor=[(i,p) for i,p in near if -MAX_APPROACH_RANGE_M<=p["along_m"]<=MAX_RUNWAY_GROUND_DISTANCE_M]
    aligned=[p for _,p in corridor if p.get("track_deg") is not None and _angle_difference(float(p["track_deg"]),rw["alignment_deg_true"])<=MAX_TRACK_ERROR_DEG]
    surface=[p for _,p in corridor if p.get("altitude_state")=="ground"]
    low_numeric=[p for _,p in corridor if p.get("geometric_altitude_state")=="numeric" and p.get("geometric_altitude_datum")=="geometric_WGS84_ellipsoid" and p.get("geometric_altitude_ft") is not None and p.get("geometric_altitude_ft")<1600]
    airborne_fast=[p for p in aligned if p.get("groundspeed_kt") is not None and float(p["groundspeed_kt"])>=MIN_AIRBORNE_SPEED_KT]
    inbound=[p for p in aligned if p["along_m"]<=-MIN_APPROACH_RANGE_M]
    # Arrival: aligned, fast approach and a ground-state point near the landing
    # threshold, with temporal order toward the threshold. Absolute altitude is
    # not compared to FAA MSL elevations because it is ellipsoid height.
    arrival=False; arrival_reasons=[]; arrival_conf="none"
    for i,a in enumerate(pts):
        if not (a.get("track_deg") is not None and _angle_difference(float(a["track_deg"]),rw["alignment_deg_true"])<=MAX_TRACK_ERROR_DEG and a["along_m"]<=-MIN_APPROACH_RANGE_M and abs(a["cross_m"])<=MAX_LATERAL_M): continue
        for b in pts[i+1:]:
            if b.get("altitude_state")=="ground" and abs(b["cross_m"])<=MAX_LATERAL_M and -250<=b["along_m"]<=MAX_TOUCHDOWN_DISTANCE_M and b["along_m"]>a["along_m"]:
                if a.get("groundspeed_kt") is not None and float(a["groundspeed_kt"])>=MIN_AIRBORNE_SPEED_KT:
                    arrival=True; arrival_conf="high_candidate"; break
        if arrival: break
    if not arrival:
        if inbound and low_numeric: arrival_reasons.append("aligned_inbound_and_low_ellipsoid_height_but_no_ground_state_touchdown")
        elif inbound: arrival_reasons.append("aligned_inbound_approach_fragment_ends_before_touchdown")
        elif surface: arrival_reasons.append("surface_state_near_runway_without_inbound_aligned_approach")
        else: arrival_reasons.append("no_runway_corridor_approach_sequence")

    # Departure: ground-state near runway followed by aligned, fast outbound
    # points; geometric-height trend is used only as a relative ellipsoid-height
    # difference, never compared with MSL threshold elevation.
    departure=False; departure_reasons=[]; departure_conf="none"
    ground_indices=[i for i,p in enumerate(pts) if p.get("altitude_state")=="ground" and abs(p["cross_m"])<=MAX_LATERAL_M and -MAX_RUNWAY_GROUND_DISTANCE_M<=p["along_m"]<=500]
    for i in ground_indices:
        g=pts[i]
        for j in range(i+1,len(pts)):
            p=pts[j]
            if p["along_m"]<g["along_m"]+400 or p["along_m"]>MAX_APPROACH_RANGE_M or abs(p["cross_m"])>MAX_LATERAL_M: continue
            if p.get("track_deg") is None or _angle_difference(float(p["track_deg"]),rw["alignment_deg_true"])>MAX_TRACK_ERROR_DEG: continue
            if p.get("groundspeed_kt") is None or float(p["groundspeed_kt"])<MIN_AIRBORNE_SPEED_KT: continue
            # Need positive relative height rise if both ends have compatible geometric datum.
            before=[q for q in pts[i:j+1] if q.get("geometric_altitude_state")=="numeric" and q.get("geometric_altitude_datum")=="geometric_WGS84_ellipsoid" and q.get("geometric_altitude_ft") is not None]
            rise=len(before)>=2 and float(before[-1]["geometric_altitude_ft"])-float(before[0]["geometric_altitude_ft"])>=150
            if rise:
                departure=True; departure_conf="high_candidate"; break
        if departure: break
    if not departure:
        if ground_indices: departure_reasons.append("runway_surface_state_without_aligned_fast_outbound_climb")
        elif any(p["along_m"]>0 and p in aligned for _,p in corridor): departure_reasons.append("aligned_outbound_fragment_without_surface_anchor_or_height_rise")
        else: departure_reasons.append("no_runway_corridor_departure_sequence")
    return {"runway_end":rw["runway_end"],"axis_heading_deg_true":rw["alignment_deg_true"],
            "corridor_point_count":len(corridor),"aligned_point_count":len(aligned),"ground_state_point_count":len(surface),
            "inbound_aligned_point_count":len(inbound),"low_numeric_geometric_height_point_count":len(low_numeric),
            "departure_ground_anchor_count":len(ground_indices),"arrival_candidate":arrival,"arrival_confidence":arrival_conf,
            "arrival_rejection_reasons":arrival_reasons,"departure_candidate":departure,"departure_confidence":departure_conf,
            "departure_rejection_reasons":departure_reasons}


def classify(paths_path: Path, runway_path: Path) -> dict:
    runways=load_runways(runway_path)
    by_type_op=Counter(); per_op=Counter(); reasons=Counter(); selected=[]
    path_count=0; paths_with_runway_corridor=0; ambiguity=Counter(); quality=Counter()
    for path in _iter_json_paths(paths_path):
        path_count+=1
        assessments=[_candidate_for_runway(path,rw) for rw in runways]
        candidates=[x for x in assessments if x["arrival_candidate"] or x["departure_candidate"]]
        if any(x["corridor_point_count"] for x in assessments): paths_with_runway_corridor+=1
        if not candidates:
            why=";".join(sorted({r for x in assessments for r in x["arrival_rejection_reasons"]+x["departure_rejection_reasons"]}))
            reasons[why]+=1
            continue
        # Prefer a single direction/runway result. Parallel L/R axes are too
        # close to distinguish using this source; retain as axis-ambiguous.
        operation_sets={"arrival": [x for x in candidates if x["arrival_candidate"]],
                        "departure": [x for x in candidates if x["departure_candidate"]]}
        for op, rows in operation_sets.items():
            if not rows: continue
            per_op[op]+=1
            axis_ids=sorted({x["runway_end"][:2] for x in rows})
            if len(rows)>1:
                ambiguity[f"{op}:parallel_or_duplicate_runway_end_candidates"]+=1
            src=Counter(p.get("position_source", "unknown") for p in path["points"])
            quality["all_ADSB_ICAO"]+=int(all(s in ("adsb_icao","adsb_icao_nt") for s in src))
            by_type_op[(path.get("type_code") or "missing",op)]+=1
            selected.append({"path_id_internal_only":path.get("path_id"),"type_code":path.get("type_code"),
                             "operation_candidate":op,"candidate_runway_ends":[x["runway_end"] for x in rows],
                             "runway_assignment":"axis_only_parallel_L_R_ambiguous" if len(rows)>1 else "single_axis_candidate_end_unconfirmed",
                             "classification":"inferred_candidate_not_confirmed_movement",
                             "position_source_counts":dict(src),"source_quality_all_adsb_icao":all(s in ("adsb_icao","adsb_icao_nt") for s in src),
                             "geometry_checks":rows})
    return {"schema":"quiet_la_vny_geometry_inferred_operation_candidates_v1_internal",
            "activity_file":paths_path.name,"activity_sha256":_sha256(paths_path),"runway_query_file":runway_path.name,"runway_query_sha256":_sha256(runway_path),
            "date_local":"2026-01-15","utc_window":{"start_inclusive":"2026-01-15T08:00:00Z","end_exclusive":"2026-01-16T08:00:00Z"},
            "method":{"source":"FAA-derived USDOT/BTS/NTAD Runway Ends, effective 2026-10-01; end and displaced-threshold geometry, true alignment; endpoint elevations source NGS 2011-11-29.",
                      "arrival_rule":"same continuous path has a track-aligned point ≥1km inbound within 350m lateral, speed ≥45kt, followed by a ground-state point within the runway corridor and touchdown zone; no pressure/geoid altitude comparison.",
                      "departure_rule":"same continuous path has ground-state anchor in runway corridor followed by aligned outbound point ≥400m with speed ≥45kt and ≥150ft relative rise in same-datum geometric WGS84 ellipsoid heights.",
                      "limits":"Heuristic candidate rules, not confirmed operations. No explicit movement ID, operation/airport, runway assignment, historical active runway, aircraft config/thrust, mic-height, or geoid conversion. Parallel runway axes are unresolved; fragments/duplicates may represent portions of one movement. Track filtering, local thresholds and exact points may miss true operations.",
                      "identity_policy":"internal minimized ADS-B path IDs only; do not publish paths, IDs or owner lookup; path candidate counts are not flight counts."},
            "counts":{"regional_path_fragments_examined_not_flights":path_count,"fragments_with_any_runway_corridor_point":paths_with_runway_corridor,
                      "candidate_path_fragments_by_operation_not_flights":dict(per_op),
                      "candidate_type_operation_path_fragments_not_flights":{t:{op:n for (tc,op),n in sorted(by_type_op.items()) if tc==t} for t in sorted({tc for tc,_ in by_type_op})},
                      "overlapping_parallel_or_duplicate_candidates":dict(ambiguity),"rejection_reason_combinations":dict(reasons),"all_adsb_icao_source_candidate_count":quality["all_ADSB_ICAO"]},
            "runways":runways,"candidate_rows_internal_only":selected,
            "model_status":"candidate inventory only; no acoustic calculations or comparison to Jan15 LAWA CNEL observations"}


def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--paths",type=Path,required=True); ap.add_argument("--runways",type=Path,required=True); ap.add_argument("--output",type=Path,required=True); a=ap.parse_args()
    result=classify(a.paths,a.runways); a.output.parent.mkdir(parents=True,exist_ok=True); a.output.write_text(json.dumps(result,indent=2)+"\n")
    print(json.dumps(result["counts"],indent=2))


if __name__=="__main__": main()
