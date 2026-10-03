from datetime import date, datetime, timedelta, timezone
import json
import math
from pathlib import Path
import unittest
from zoneinfo import ZoneInfo

from implementation.models.aircraft_doc29.doc29 import (
    Doc29InputError, NPDTable, SELObservation, daily_cnel_from_sel,
    energy_sum_db, laeq_from_sel, npd_level,
)
from implementation.models.aircraft_doc29.reference_case import run
from implementation.models.aircraft_doc29.anp_loader import load_anp_npd_table
from implementation.models.aircraft_doc29.segment_reference import run as run_segment_reference
from implementation.models.aircraft_doc29.geometry_assisted_case import run as run_geometry_assisted_case
from implementation.models.aircraft_doc29.segment_reference import _finite_segment_correction
from implementation.models.aircraft_doc29.profile_geometry_reference import run as run_profile_geometry_reference
from implementation.models.aircraft_doc29.independent_profile_acoustics import run as run_independent_profile_acoustics
from implementation.models.aircraft_doc29.independent_profile_acoustics import _segment_geometry
from implementation.models.aircraft_doc29.vny_pa28_fixed_profile_scenario import run as run_vny_pa28_scenario
from implementation.models.aircraft_doc29.vny_pa28_monitor_site_sensitivity import (
    run as run_vny_pa28_monitor_site_sensitivity,
    _wgs84_inverse, _local_track_offsets, _local_daypart, _subset_cnel,
)
from implementation.models.aircraft_doc29.vny_fixed_profile_event_adapter import (
    load_fixed_profile as load_generic_fixed_profile,
    run as run_generic_fixed_profile_event_adapter,
    _event_sel as generic_event_sel, _power_unit as generic_power_unit,
)
from implementation.models.aircraft_doc29.profile_geometry import (
    _climb_height_fractions, _deduplicate, _interpolate_profile,
    _speed_subsegment_positions, segments_from_fixed_profile, segment_profile_points,
)

REPO = Path(__file__).resolve().parents[4]
ANP_ARCHIVE = REPO/"implementation"/"work"/"autonomous_delivery_2026_10_02"/"source_evidence"/"anp_archive_v2_3.zip"


def event(when, level=60.0, metric="SEL", *, ident="TEST", op="A", provenance="unit-test"):
    return SELObservation(when, level, metric, ident, op, provenance)


class NPDTests(unittest.TestCase):
    def setUp(self):
        # Deliberate test curve: -20 dB per decade distance, +10 dB per 100 units power.
        self.table = NPDTable("TEST", "TEST-NPD", "SEL", "A", "lb_per_engine",
                              (100.0, 200.0), (30.48, 304.8),
                              ((60.0, 40.0), (70.0, 50.0)))

    def test_doc29_power_then_log_distance_interpolation(self):
        result = npd_level(self.table, aircraft_id="TEST", npd_id="TEST-NPD",
                           metric="SEL", operation="A", power=150,
                           power_unit="lb_per_engine", distance_m=math.sqrt(30.48*304.8))
        self.assertAlmostEqual(result, 55.0, places=10)

    def test_doc29_minimum_distance_clamp(self):
        result = npd_level(self.table, aircraft_id="TEST", npd_id="TEST-NPD",
                           metric="SEL", operation="A", power=100,
                           power_unit="lb_per_engine", distance_m=0.5)
        expected = 60.0 + (40.0-60.0) * (math.log10(30.0)-math.log10(30.48)) / (math.log10(304.8)-math.log10(30.48))
        self.assertAlmostEqual(result, expected, places=12)  # 0.5 m clamps to Doc 29's 30 m, then edge-extrapolates.

    def test_rejects_metric_aircraft_operation_or_unit_mismatch(self):
        for kwargs in [
            {"aircraft_id":"OTHER", "npd_id":"TEST-NPD", "metric":"SEL", "operation":"A", "power_unit":"lb_per_engine"},
            {"aircraft_id":"TEST", "npd_id":"TEST-NPD", "metric":"EPNL", "operation":"A", "power_unit":"lb_per_engine"},
            {"aircraft_id":"TEST", "npd_id":"TEST-NPD", "metric":"SEL", "operation":"D", "power_unit":"lb_per_engine"},
            {"aircraft_id":"TEST", "npd_id":"TEST-NPD", "metric":"SEL", "operation":"A", "power_unit":"percent_per_engine"},
        ]:
            with self.subTest(kwargs=kwargs), self.assertRaises(Doc29InputError):
                npd_level(self.table, **kwargs, power=150, distance_m=100.0)

    def test_rejects_unlisted_power_extrapolation(self):
        with self.assertRaises(Doc29InputError):
            npd_level(self.table, aircraft_id="TEST", npd_id="TEST-NPD", metric="SEL",
                      operation="A", power=201, power_unit="lb_per_engine", distance_m=100)

    def test_normalizes_private_anp_input_with_explicit_identity_units_and_mode(self):
        match, table = load_anp_npd_table(ANP_ARCHIVE, "1900D", "SEL", "A")
        self.assertEqual((match.requested_aircraft_id, match.npd_id, match.metric, match.operation),
                         ("1900D", "PT6A67", "SEL", "A"))
        self.assertEqual(table.power_unit, "lb_per_engine")
        self.assertAlmostEqual(table.distances_m[0], 200*0.3048)
        level = npd_level(table, aircraft_id=match.requested_aircraft_id,
                          npd_id=match.npd_id, metric="SEL", operation="A",
                          power=table.power_settings[0], power_unit=table.power_unit,
                          distance_m=table.distances_m[0])
        self.assertTrue(math.isfinite(level))


class EnergyMetricTests(unittest.TestCase):
    def test_stable_energy_sum(self):
        self.assertAlmostEqual(energy_sum_db([1000.0, 1000.0]), 1003.01029995664, places=9)

    def test_laeq_divides_by_explicit_actual_window(self):
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        events = [event(start + timedelta(seconds=1), 60), event(start + timedelta(seconds=3), 60)]
        self.assertAlmostEqual(laeq_from_sel(events, start, start + timedelta(seconds=10)),
                              53.01029995664, places=9)

    def test_empty_and_mixed_metric_inputs_are_rejected(self):
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        with self.assertRaises(Doc29InputError):
            laeq_from_sel([], start, start + timedelta(seconds=10))
        with self.assertRaises(Doc29InputError):
            laeq_from_sel([event(start, metric="EPNL")], start, start + timedelta(seconds=10))
        with self.assertRaises(Doc29InputError):
            laeq_from_sel([event(datetime(2026, 1, 1), 60)], start, start + timedelta(seconds=10))

    def test_cnel_applies_regulatory_energy_weights_in_la_timezone(self):
        la = ZoneInfo("America/Los_Angeles")
        day = date(2026, 1, 15)
        events = [event(datetime(2026, 1, 15, 12, tzinfo=la), 90),
                  event(datetime(2026, 1, 15, 19, 30, tzinfo=la), 90),
                  event(datetime(2026, 1, 15, 22, tzinfo=la), 90)]
        # Independent formula: one day + 3x evening + 10x night, / 24 hours.
        expected = 90 + 10*math.log10(14.0 / 86400.0)
        self.assertAlmostEqual(daily_cnel_from_sel(events, day), expected, places=10)

    def test_dst_local_date_uses_actual_elapsed_duration_for_laeq(self):
        la = ZoneInfo("America/Los_Angeles")
        spring = date(2026, 3, 8)
        start = datetime.combine(spring, datetime.min.time(), tzinfo=la)
        end = datetime.combine(spring + timedelta(days=1), datetime.min.time(), tzinfo=la)
        self.assertEqual((end.astimezone(timezone.utc)-start.astimezone(timezone.utc)).total_seconds(), 23*3600)
        value = laeq_from_sel([event(datetime(2026,3,8,12,tzinfo=la), 60)], start, end)
        self.assertAlmostEqual(value, 60-10*math.log10(23*3600), places=10)


class OfficialReferenceCaseTests(unittest.TestCase):
    def test_generic_fixed_profile_adapter_uses_only_supported_windows_and_matches_pa28_packet(self):
        source=REPO/"implementation"/"work"/"autonomous_delivery_2026_10_02"/"source_evidence"
        model=Path(__file__).parents[1]
        activity=model/"reference"/"activity_2026_01_15"/"vny_candidate_event_windows_v1_internal.json"
        result=run_generic_fixed_profile_event_adapter(
            activity,ANP_ARCHIVE,source/"easa_anp_substitutions_jets_heavyprops_2018.xlsx",
            source/"activity_type_anp_coverage_internal.json",source/"vny_monitor_site_coordinates.csv",
            source/"vny_daily_local_ac_cnel_2026_q1.csv",source/"faa_vny_runway_ends_query_2026-10-03.json")
        q=result["qualification"]
        self.assertEqual((q["events_before_witness_quality_filter"],q["events_after_required_ADSB_ICAO_witness_filter"],q["events_excluded_for_witness_quality"]),(251,245,6))
        self.assertEqual(q["unique_candidate_event_windows_modeled"],26)
        self.assertEqual(q["candidate_event_windows_excluded_for_known_NPD_range_issue"],4)
        self.assertEqual(q["modeled_event_receiver_sensitivity_rows"],2808)
        self.assertEqual(result["fixed_profile_scenario_mapping_counts"].get("official_fixed_profile_available",0),0)
        c172=next(x for x in result["type_operation_inventory_internal_only"] if x["icao_type_code"]=="C172" and x["operation"]=="departure")
        self.assertEqual(c172["mapping_and_fixed_profile_status"]["status"],"no_official_2018_ICAO_substitution_row")
        self.assertEqual(c172["mapping_and_fixed_profile_status"]["unapproved_family_proxy_candidate_internal_only"]["profile_class"],"procedural_only")
        glf5=next(x for x in result["type_operation_inventory_internal_only"] if x["icao_type_code"]=="GLF5" and x["operation"]=="departure")
        self.assertEqual(glf5["mapping_and_fixed_profile_status"]["status"],"procedural_only_not_fixed_profile")
        old=json.loads((model/"reference"/"activity_2026_01_15"/"vny_pa28_monitor_site_sensitivity_v2_internal.json").read_text())
        differences=[]
        for row in result["monitor_context"]["candidate_subset_summaries"]:
            if row["site_plane_offset_m"]!=0.0: continue
            old_site=next(x for x in old["receivers"] if x["station_id"]==row["station_id"])
            old_axis=next(x for x in old_site["runway_axis_scenarios"] if x["scenario"].endswith(row["runway_assignment"]))
            old_height=next(x for x in old_axis["predictions_by_height"] if x["assumed_microphone_height_m_above_site_plane"]==row["mic_height_m"] and x["assumed_site_plane_vertical_offset_from_runway_plane_m"]==0.0)
            differences.append(abs(row["subset_event_SEL_sum_db_internal"]-old_height["subset_event_energy_sum_sel_db_internal"]))
        self.assertLess(max(differences),1e-10)

    def test_operation_specific_delta_is_applied_once_to_event_sel(self):
        self.assertEqual(generic_power_unit("CNT (% of Max Static Thrust)"),"percent_per_engine")
        self.assertEqual(generic_power_unit("Corrected Net Thrust (lb)"),"lb_per_engine")
        self.assertEqual(generic_power_unit("Other (RPM)"),"rpm_per_engine")
        profile_raw,info=load_generic_fixed_profile(ANP_ARCHIVE,"PA28","D",1)
        constructed=segment_profile_points(profile_raw,False)
        profile=[{"segment_id":i,"start":a,"end":b} for i,(a,b) in enumerate(zip(constructed,constructed[1:]),1)]
        _,sel=load_anp_npd_table(ANP_ARCHIVE,"PA28","SEL","D")
        _,lamax=load_anp_npd_table(ANP_ARCHIVE,"PA28","LAmax","D")
        event={"event_index_internal":1,"event_time_utc":"2026-01-15T18:00:00Z","operation":"departure","runway_end_candidates":["34L"]}
        baseline=generic_event_sel(event,profile,info,sel,lamax,(0.0,100.0,1.5),"D",0.0)
        adjusted=generic_event_sel(event,profile,info,sel,lamax,(0.0,100.0,1.5),"D",1.7)
        self.assertAlmostEqual(adjusted["event_sel_db_internal"]-baseline["event_sel_db_internal"],1.7,places=10)

    def test_monitor_subset_cnel_uses_local_daypart_weights(self):
        rows=[
            {"event_time_utc":"2026-01-15T20:00:00Z","event_sel_db_internal":90.0},
            {"event_time_utc":"2026-01-16T03:30:00Z","event_sel_db_internal":90.0},
            {"event_time_utc":"2026-01-16T06:00:00Z","event_sel_db_internal":90.0},
        ]
        result=_subset_cnel(rows)
        self.assertEqual([_local_daypart(r["event_time_utc"]) for r in rows],["day","evening","night"])
        self.assertEqual(result["candidate_event_counts_by_daypart"],{"day":1,"evening":1,"night":1})
        expected=90.0+10.0*math.log10(14.0/86400.0)
        self.assertAlmostEqual(result["subset_event_cnel_db_internal"],expected,places=10)

    def test_wgs84_monitor_offset_projection_and_site_sensitivity_coverage(self):
        azimuth,distance=_wgs84_inverse(0.0,0.0,0.0,1.0)
        self.assertAlmostEqual(azimuth,90.0,places=8)
        self.assertAlmostEqual(distance,111319.4908,places=2)
        x,y,_=_local_track_offsets({"lat":34.0,"lon":-118.0},
                                   {"latitude_decimal":34.0,"longitude_decimal":-117.999},0.0)
        self.assertLess(abs(x),0.001)
        self.assertGreater(y,90.0)
        source=REPO/"implementation"/"work"/"autonomous_delivery_2026_10_02"/"source_evidence"
        model=Path(__file__).parents[1]
        activity=model/"reference"/"activity_2026_01_15"/"vny_candidate_event_windows_v1_internal.json"
        result=run_vny_pa28_monitor_site_sensitivity(
            activity,ANP_ARCHIVE,source/"vny_monitor_site_coordinates.csv",
            source/"vny_daily_local_ac_cnel_2026_q1.csv",
            source/"faa_vny_runway_ends_query_2026-10-03.json")
        self.assertEqual(result["coverage"]["modeled_site_count"],6)
        self.assertEqual(result["coverage"]["excluded_site_ids"],["VNY07"])
        self.assertEqual(result["coverage"]["candidate_window_rows"],2808)
        self.assertTrue(result["measurement_context"]["comparison_performed"])
        self.assertEqual(result["receiver_height_datum_sensitivity"]["site_plane_offset_scenarios_m_relative_to_runway_plane"],[-5.0,0.0,5.0])
        for site in result["receivers"]:
            self.assertEqual(len(site["runway_axis_scenarios"]),2)
            for axis in site["runway_axis_scenarios"]:
                predictions=axis["predictions_by_height"]
                self.assertEqual(len(predictions),9)
                self.assertEqual([(x["assumed_microphone_height_m_above_site_plane"],x["assumed_site_plane_vertical_offset_from_runway_plane_m"]) for x in predictions],[(h,o) for h in [1.5,4.0,6.0] for o in [-5.0,0.0,5.0]])
                self.assertEqual([x["unique_inferred_candidate_windows_not_confirmed_movements"] for x in predictions],[26]*9)
                self.assertTrue(all(x["subset_daily_cnel_sensitivity"]["candidate_event_counts_by_daypart"]=={"day":25,"evening":1} for x in predictions))

    def test_doc29_height_split_precedes_speed_split_and_keeps_short_nodes(self):
        a={"s_m":0.0,"z_m":914.4,"speed_mps":88.6388888888889,"power":84.78,"bank_deg":0.0}
        b={"s_m":11565.1,"z_m":1330.2,"speed_mps":111.61111111111111,"power":70.83,"bank_deg":0.0}
        hf=_climb_height_fractions(a,b,True)
        self.assertEqual(len(hf),1)
        h=_interpolate_profile(a,b,hf[0])
        local=_speed_subsegment_positions(a["speed_mps"],h["speed_mps"])
        global_breaks=[hf[0]*f for f,_ in local]+hf+[1.0]
        for actual,expected in zip(global_breaks,[0.2796006623,0.5803862964,0.9023569024,1.0]):
            self.assertAlmostEqual(actual,expected,places=8)
        # Height/speed nodes a few metres apart remain separate when they carry
        # different state; only exact duplicate coordinates and state collapse.
        nodes=[a,dict(a,s_m=1.0,z_m=1.0,speed_mps=90.0,power=80.0),b]
        self.assertEqual(len(_deduplicate(nodes)),3)

    def test_propds_r02_three_dimensional_profile_matches_independent_case(self):
        case_file=Path(__file__).parents[1]/"reference"/"propds_r02_reference_case.json"
        result=run_independent_profile_acoustics(case_file)
        geometry=segments_from_fixed_profile(json.loads(case_file.read_text()))
        nodes=[s["end"] for s in geometry if 14703.1 < s["end"]["s_m"] < 26268.2]
        self.assertEqual(len(nodes),3)
        self.assertAlmostEqual(nodes[0]["s_m"],17936.709619,places=5)
        self.assertAlmostEqual(nodes[1]["s_m"],21415.325556,places=5)
        self.assertAlmostEqual(nodes[2]["z_m"],1289.6,places=5)
        # §3.6.5 power increments are equal across the three speed steps
        # after the §3.6.4 height split; height itself used Eq. (3-2d).
        height_end_power=math.sqrt(84.78**2+0.902356902356902*(70.83**2-84.78**2))
        self.assertAlmostEqual(nodes[0]["power"],84.78+(height_end_power-84.78)/3,places=7)
        self.assertAlmostEqual(nodes[1]["power"],84.78+2*(height_end_power-84.78)/3,places=7)
        self.assertEqual(result["segment_count"],27)
        self.assertLess(abs(result["movement_sel_residual_db"]),0.01)
        self.assertLess(max(abs(s["segment_sel_residual_db"]) for s in result["segments"]),0.05)
        self.assertLess(result["max_abs_q_residual_m"],5.0)
        self.assertEqual(result["npd_power_unit"],"percent_per_engine")

    def test_doc29_generalized_speed_breaks_use_integer_n_and_equal_time(self):
        v1, v2, distance = 88.6388888888889, 111.61111111111111, 11565.1
        n = int(1.0 + abs(v2-v1)/10.0)
        dv = (v2-v1)/n
        expected = []
        cumulative = 0.0
        for k in range(1, n):
            cumulative += (v1 + dv*(k-0.5))*2.0*distance/((v1+v2)*n)
            expected.append(cumulative/distance)
        actual = _speed_subsegment_positions(v1,v2)
        self.assertEqual(len(actual), n-1)
        for (fraction,_), target in zip(actual,expected):
            self.assertAlmostEqual(fraction,target,places=12)
        boundaries=[0.0,*expected,1.0]
        speeds=[math.sqrt(v1*v1+f*(v2*v2-v1*v1)) for f in boundaries]
        elapsed=[2.0*distance*(b-a)/(speeds[i]+speeds[i+1])
                 for i,(a,b) in enumerate(zip(boundaries,boundaries[1:]))]
        expected_elapsed=2.0*distance/((v1+v2)*n)
        for dt in elapsed:
            self.assertAlmostEqual(dt,expected_elapsed,places=10)

    def test_inclined_path_equivalent_level_angles_behind_alongside_ahead(self):
        # Analytic straight segment: horizontal span 100 m, rise 10 m, so
        # cos(gamma)=100/sqrt(100^2+10^2). Receiver plane is z=0.
        segment = {
            "start": {"s_m": 0.0, "z_m": 1.0, "speed_mps": 50.0, "power": 2000.0},
            "end": {"s_m": 100.0, "z_m": 11.0, "speed_mps": 60.0, "power": 2000.0},
        }
        cosine = 100.0 / math.sqrt(100.0**2 + 10.0**2)
        gamma = math.atan2(10.0, 100.0)
        # Behind: endpoint height, expanded to its equivalent-level value.
        behind = _segment_geometry(segment, (-100.0, 50.0, 0.0), "D")
        self.assertAlmostEqual(behind["beta_deg"], math.degrees(math.atan2(1.0/cosine, 50.0)), places=10)
        # Alongside: CPA height is the perpendicular height to the inclined
        # trajectory, also expanded by 1/cos(gamma).
        alongside = _segment_geometry(segment, (50.0, 50.0, 0.0), "D")
        sine = math.sin(gamma)
        along_q = 50.0*cosine + (0.0-1.0)*sine
        raw_cpa_height = 1.0 + along_q*sine
        self.assertAlmostEqual(alongside["beta_deg"], math.degrees(math.atan2(raw_cpa_height/cosine, 50.0)), places=10)
        self.assertAlmostEqual(alongside["phi_deg"], math.degrees(math.atan2(raw_cpa_height/cosine, 50.0)), places=10)
        # Ahead: the far endpoint height is transformed the same way.
        ahead = _segment_geometry(segment, (200.0, 50.0, 0.0), "D")
        self.assertAlmostEqual(ahead["beta_deg"], math.degrees(math.atan2(11.0/cosine, 50.0)), places=10)
        ahead_q = 200.0*cosine - sine
        ahead_cpa_height = 1.0 + ahead_q*sine
        self.assertAlmostEqual(ahead["phi_deg"], math.degrees(math.atan2(ahead_cpa_height/cosine, 50.0)), places=10)
        self.assertGreater(alongside["beta_deg"], math.degrees(math.atan2(raw_cpa_height, 50.0)))

    def test_jetfas_r18_first_airborne_segment_reconstruction(self):
        case_file = Path(__file__).parents[1]/"reference"/"jetfas_reference_case.json"
        result = run_segment_reference(case_file)
        self.assertTrue(result["all_checks_pass"], result)
        self.assertLess(abs(result["residual_db"]), 0.001)
        self.assertAlmostEqual(
            result["terms_db"]["finite_segment_correction_computed_from_eq_4_20"],
            -62.50153583006829, places=6,
        )

    def test_all_33_jetfas_geometry_assisted_segments(self):
        case_file = Path(__file__).parents[1]/"reference"/"jetfas_reference_case.json"
        result = run_geometry_assisted_case(case_file)
        self.assertEqual(result["segment_count"], 33)
        self.assertTrue(result["all_segment_residuals_within_0_001_db"])
        self.assertLess(abs(result["movement_residual_db"]), 0.01)

    def test_far_tail_finite_segment_correction_is_finite_and_clamped(self):
        value = _finite_segment_correction(80.0, 70.0, -1e12, 1.0)
        self.assertEqual(value, -150.0)

    def test_profile_route_inputs_construct_33_reference_segment_geometries(self):
        case_file = Path(__file__).parents[1]/"reference"/"jetfas_reference_case.json"
        result = run_profile_geometry_reference(case_file)
        self.assertEqual(result["constructed_segment_count"], 33)
        self.assertTrue(result["segment_count_matches"])
        self.assertLess(result["endpoint_error_max_m"], 2.0)

    def test_a_sheet_geometry_drives_all_33_acoustic_segments(self):
        case_file = Path(__file__).parents[1]/"reference"/"jetfas_reference_case.json"
        result = run_independent_profile_acoustics(case_file)
        self.assertEqual(result["segment_count"], 33)
        self.assertLess(abs(result["movement_sel_residual_db"]), 0.01)
        self.assertLess(max(abs(s["segment_sel_residual_db"]) for s in result["segments"]), 0.03)
        self.assertLess(max(abs(s["segment_sel_residual_db"]) for s in result["segments"][26:]), 0.001)
        self.assertLess(result["max_abs_q_residual_m"], 2.0)

    def test_a_sheet_geometry_and_takeoff_groundroll_drive_jetfds_r02(self):
        case_file = Path(__file__).parents[1]/"reference"/"jetfds_r02_reference_case.json"
        result = run_independent_profile_acoustics(case_file)
        self.assertEqual(result["segment_count"], 29)
        self.assertLess(abs(result["movement_sel_residual_db"]), 0.01)
        # Ground-roll segment geometry, q=0 finite fraction, and ΔSOR reproduce
        # the independently published first nine B-2 components tightly.
        self.assertLess(max(abs(s["segment_sel_residual_db"]) for s in result["segments"][:9]), 0.001)
        self.assertLess(max(abs(s["segment_sel_residual_db"]) for s in result["segments"][9:]), 0.03)
        self.assertLess(result["max_abs_q_residual_m"], 4.0)

    def test_pa28_scenario_uses_correct_departure_geometry_and_excludes_arrivals(self):
        base = Path(__file__).parents[1]
        events = base/"reference"/"activity_2026_01_15"/"vny_candidate_event_windows_v1_internal.json"
        result = run_vny_pa28_scenario(events, ANP_ARCHIVE)
        self.assertEqual(result["activity_input"]["candidate_event_windows_by_operation_after_quality_filter_not_confirmed_operations"],
                         {"arrival": 4, "departure": 26})
        self.assertEqual(result["operation_applicability"]["excluded_arrivals"]["candidate_event_windows_after_quality_filter"], 4)
        self.assertEqual(result["profile_by_operation"]["D"]["constructed_segment_count"], 16)
        first = result["results"]["event_sel_by_candidate_window_internal_only"][0]
        self.assertEqual(first["segment_count"], 16)
        self.assertAlmostEqual(first["segment_levels_internal"][0]["acoustic_impedance_adjustment_db"], 0.074076725, places=8)

    def test_30m_npd_floor_sensitivity_for_ground_roll(self):
        case_file = Path(__file__).parents[1]/"reference"/"jetfas_reference_case.json"
        result = run_independent_profile_acoustics(case_file, minimum_distance_m=1.0)
        self.assertEqual(result["npd_minimum_distance_policy_m"], 1.0)
        self.assertLess(max(s["segment_sel_residual_db"] for s in result["segments"][26:]), -28.0)


    def test_jetfas_r18_published_segment_aggregation(self):
        case_file = Path(__file__).parents[1]/"reference"/"jetfas_reference_case.json"
        result = run(case_file)
        self.assertEqual(result["segment_count"], 33)
        self.assertEqual(result["ecac_published_expected_total_sel_db"], 98.95)
        self.assertTrue(result["within_rounding_tolerance_0_01_db"])
        self.assertIn("not a full Doc 29", result["implementation_scope"])


if __name__ == "__main__":
    unittest.main()
