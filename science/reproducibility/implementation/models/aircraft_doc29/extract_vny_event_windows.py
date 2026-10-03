"""Extract bounded, deduplicated VNY candidate event windows from traces.

Path fragments are not movements. This tool requires temporal approach/surface
or surface/climb witness sequences, clips each event window to 12 km around a
runway threshold, collapses parallel-end copies of the same witness, and
deduplicates exact near-identical windows across fragments conservatively.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path

from .infer_vny_runway_operations import _angle_difference, _local_xy, load_runways
from .reclip_paths import _iter_json_paths

MAX_LATERAL_M=350.0
MAX_RANGE_M=12_000.0
MIN_RANGE_M=1_000.0
MAX_APPROACH_TIME_S=300.0
MAX_DEPARTURE_TIME_S=240.0
MIN_TRACK_ERROR_DEG=18.0
MIN_APPROACH_SPEED_KT=45.0
MIN_DEPARTURE_SPEED_KT=45.0
MIN_CLIMB_FT=150.0


def _utc(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def _sha(path: Path) -> str:
    h=hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda:f.read(1<<20),b""): h.update(block)
    return h.hexdigest()


def _metrics(points: list[dict], runway: dict) -> list[dict]:
    threshold=runway["threshold"]
    h=math.radians(runway["alignment_deg_true"])
    ux,uy=math.sin(h),math.cos(h)
    result=[]
    for i,p in enumerate(points):
        x,y=_local_xy(float(p["lat"]),float(p["lon"]),threshold)
        s=x*ux+y*uy; cross=x*uy-y*ux
        result.append({"i":i,"p":p,"s_m":s,"cross_m":cross,"range_m":math.hypot(x,y)})
    return result


def _ground_episodes(points: list[dict], runway: dict) -> list[list[dict]]:
    metrics=_metrics(points,runway); eligible=[]
    for m in metrics:
        p=m["p"]
        if p.get("altitude_state")=="ground" and abs(m["cross_m"])<=MAX_LATERAL_M and -1_200<=m["s_m"]<=800:
            eligible.append(m)
    groups=[]
    for m in eligible:
        if not groups:
            groups.append([m]); continue
        prev=groups[-1][-1]
        dt=(_utc(m["p"]["timestamp_utc"])-_utc(prev["p"]["timestamp_utc"])).total_seconds()
        if m["i"]==prev["i"]+1 and 0<dt<=60 and math.hypot(m["p"]["lat"]-prev["p"]["lat"],m["p"]["lon"]-prev["p"]["lon"])<0.004:
            groups[-1].append(m)
        else: groups.append([m])
    return groups


def _witness(m: dict) -> dict:
    p=m["p"]
    return {"point_index_within_internal_fragment":m["i"],"timestamp_utc":p["timestamp_utc"],
            "along_runway_axis_from_threshold_m":round(m["s_m"],2),"cross_track_m":round(m["cross_m"],2),
            "groundspeed_kt":p.get("groundspeed_kt"),"track_deg_true":p.get("track_deg"),
            "altitude_state":p.get("altitude_state"),"geometric_altitude_ft":p.get("geometric_altitude_ft"),
            "geometric_altitude_datum":p.get("geometric_altitude_datum"),"position_source":p.get("position_source")}


def _event_proposals(path: dict, runway: dict) -> list[dict]:
    points=path["points"]; metrics=_metrics(points,runway); episodes=_ground_episodes(points,runway)
    rows=[]; heading=runway["alignment_deg_true"]
    for episode in episodes:
        first,last=episode[0],episode[-1]
        ground_time=_utc(first["p"]["timestamp_utc"])
        # Each ground episode may be an arrival, departure, or touch-and-go.
        approaches=[]
        for m in metrics[:first["i"]]:
            p=m["p"]
            dt=(ground_time-_utc(p["timestamp_utc"])).total_seconds()
            if not (0<dt<=MAX_APPROACH_TIME_S and -MAX_RANGE_M<=m["s_m"]<=-MIN_RANGE_M and abs(m["cross_m"])<=MAX_LATERAL_M): continue
            if p.get("track_deg") is None or _angle_difference(float(p["track_deg"]),heading)>MIN_TRACK_ERROR_DEG: continue
            if p.get("groundspeed_kt") is None or float(p["groundspeed_kt"])<MIN_APPROACH_SPEED_KT: continue
            approaches.append(m)
        if approaches:
            approach=max(approaches,key=lambda m:m["s_m"])
            start=min(approaches,key=lambda m:m["i"])
            end=last
            rows.append({"operation":"arrival","type_code":path.get("type_code") or "missing",
                         "path_fragment_id_internal_only":path.get("path_id"),"runway_end_candidate":runway["runway_end"],
                         "runway_axis_heading_deg_true":heading,"approach_witness":_witness(approach),
                         "ground_witness":_witness(first),"_witness_coordinate_private":[first["p"]["lat"],first["p"]["lon"]],"event_window":{"start_utc":start["p"]["timestamp_utc"],
                         "end_utc":end["p"]["timestamp_utc"],"elapsed_seconds":(_utc(end["p"]["timestamp_utc"])-_utc(start["p"]["timestamp_utc"])).total_seconds()},
                         "classifier":"aligned approach within 1–12 km, speed>=45 kt, followed within 300 s by runway-corridor ground state"})
        # A departure's witness is the last surface report before aligned climb.
        airborne=[]
        for m in metrics[last["i"]+1:]:
            p=m["p"]
            dt=(_utc(p["timestamp_utc"])-_utc(last["p"]["timestamp_utc"])).total_seconds()
            if not (0<dt<=MAX_DEPARTURE_TIME_S and 400<=m["s_m"]<=MAX_RANGE_M and abs(m["cross_m"])<=MAX_LATERAL_M): continue
            if p.get("track_deg") is None or _angle_difference(float(p["track_deg"]),heading)>MIN_TRACK_ERROR_DEG: continue
            if p.get("groundspeed_kt") is None or float(p["groundspeed_kt"])<MIN_DEPARTURE_SPEED_KT: continue
            if p.get("geometric_altitude_state")!="numeric" or p.get("geometric_altitude_datum")!="geometric_WGS84_ellipsoid" or p.get("geometric_altitude_ft") is None: continue
            airborne.append(m)
        # Relative height uses only same-datum numeric ellipsoid heights. Need
        # a pair among post-surface numeric reports with >=150 ft positive rise.
        climb=None; climb_base=None
        for k,m in enumerate(airborne):
            p=m["p"]
            prior=[q for q in airborne[:k] if _utc(p["timestamp_utc"])-_utc(q["p"]["timestamp_utc"])]
            if prior and float(p["geometric_altitude_ft"])-float(prior[0]["p"]["geometric_altitude_ft"])>=MIN_CLIMB_FT:
                climb=m; climb_base=prior[0]; break
        if climb:
            rows.append({"operation":"departure","type_code":path.get("type_code") or "missing",
                         "path_fragment_id_internal_only":path.get("path_id"),"runway_end_candidate":runway["runway_end"],
                         "runway_axis_heading_deg_true":heading,"ground_witness":_witness(last),"_witness_coordinate_private":[last["p"]["lat"],last["p"]["lon"]],
                         "climb_base_witness":_witness(climb_base),"climb_witness":_witness(climb),
                         "relative_ellipsoid_height_rise_ft":float(climb["p"]["geometric_altitude_ft"])-float(climb_base["p"]["geometric_altitude_ft"]),
                         "event_window":{"start_utc":last["p"]["timestamp_utc"],"end_utc":climb["p"]["timestamp_utc"],
                         "elapsed_seconds":(_utc(climb["p"]["timestamp_utc"])-_utc(last["p"]["timestamp_utc"])).total_seconds()},
                         "classifier":"runway-corridor surface state followed within 240 s by aligned outbound >=400 m, speed>=45 kt and same-datum geometric climb>=150 ft"})
    return rows


def _dedupe_key(e: dict) -> tuple:
    anchor=e["ground_witness"]; p=e["type_code"]; lat,lon=e["_witness_coordinate_private"]
    return (e["operation"],p,e["runway_axis_heading_deg_true"],anchor["timestamp_utc"],round(float(lat),4),round(float(lon),4))


def extract(paths_file: Path, runway_file: Path) -> dict:
    runways=load_runways(runway_file); proposals=[]; paths_seen=0
    for path in _iter_json_paths(paths_file):
        paths_seen+=1
        for rw in runways: proposals.extend(_event_proposals(path,rw))
    # Collapse L/R runway-end copies with identical surface witness, then remove
    # exact same-window/type duplicates across fragments. Close but nonidentical
    # witnesses remain separate because aircraft identity was intentionally
    # stripped and merging them could erase real simultaneous operations.
    grouped={}
    for e in proposals:
        grouped.setdefault(_dedupe_key(e),[]).append(e)
    unique=[]; duplicate_end_copies=0; duplicate_fragment_copies=0
    for group in grouped.values():
        # Equivalent parallel-end rows have the same witnesses; retain one row
        # with both end candidates on the shared 16/34 axis.
        group.sort(key=lambda r:r["runway_end_candidate"])
        winner=group[0].copy()
        ends=sorted({r["runway_end_candidate"] for r in group})
        winner["runway_end_candidates_axis_ambiguous"]=(len(ends)>1)
        winner["runway_end_candidates"]=ends
        duplicate_end_copies+=max(0,len(ends)-1)
        fragments=sorted({r["path_fragment_id_internal_only"] for r in group})
        winner["duplicate_fragment_count_same_exact_witness"]=len(fragments)-1
        duplicate_fragment_copies+=len(fragments)-1
        winner.pop("_witness_coordinate_private",None)
        unique.append(winner)
    unique.sort(key=lambda e:(e["operation"],e["event_window"]["start_utc"],e["type_code"]))
    counts=Counter((e["operation"],e["type_code"]) for e in unique)
    return {"schema":"quiet_la_vny_candidate_event_windows_v1_internal",
            "activity_file":paths_file.name,"activity_sha256":_sha(paths_file),"runway_file":runway_file.name,"runway_sha256":_sha(runway_file),
            "date_local":"2026-01-15","utc_window":{"start_inclusive":"2026-01-15T08:00:00Z","end_exclusive":"2026-01-16T08:00:00Z"},
            "counts":{"continuous_regional_path_fragments_examined_not_flights":paths_seen,"raw_runway_end_proposals_before_dedupe":len(proposals),
                      "unique_event_windows_after_exact_witness_dedup_not_confirmed_movements":len(unique),
                      "candidate_event_windows_by_operation_not_confirmed_movements":dict(Counter(e["operation"] for e in unique)),
                      "candidate_event_windows_by_type_and_operation":{op:{tc:n for (o,tc),n in sorted(counts.items()) if o==op} for op in ("arrival","departure")},
                      "collapsed_parallel_runway_end_copies":duplicate_end_copies,"collapsed_exact_witness_duplicate_fragment_copies":duplicate_fragment_copies},
            "method":{"runway_geometry":"FAA-derived NTAD runway-end query, effective 2026-10-01; runway-end axis and displaced arrival thresholds used. No active-runway history assumed.",
                      "arrival":"A continuous fragment must show aligned >=45 kt inbound within 1–12 km, then runway-corridor surface-state within 300 s.",
                      "departure":"A continuous fragment must show runway-corridor surface state then aligned >=45 kt outbound >=400 m within 12 km and >=150 ft rise across same-datum numeric WGS84 ellipsoid heights within 240 s.",
                      "window_and_dedupe":"Each event window has witness UTC timestamps, point indices, range, cross-track, type code, and run-specific internal fragment ID. Parallel L/R ends with the exact same witness are one axis-level event. Cross-fragment deduplication requires exact timestamp, ICAO type, operation, axis, and 10 m-bucketed witness position; nearby nonidentical windows remain separate to avoid merging possible simultaneous flights. No transient ICAO identity is retained.",
                      "limits":"Inferred activity candidates only; no confirmed airport movement or complete activity coverage. Threshold crossing/grouping can miss/duplicate touch-and-go or fragment-split flights. Weather, track-source, height, engine variant, thrust, procedure, and runways are incomplete. WGS84 aircraft height is used only for same-datum relative climb, not converted to MSL."},
            "events_internal_only":unique}


def main():
    p=argparse.ArgumentParser(); p.add_argument("--paths",type=Path,required=True); p.add_argument("--runways",type=Path,required=True); p.add_argument("--output",type=Path,required=True); a=p.parse_args()
    result=extract(a.paths,a.runways); a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(result,indent=2)+"\n");print(json.dumps(result["counts"],indent=2))


if __name__=="__main__":main()
