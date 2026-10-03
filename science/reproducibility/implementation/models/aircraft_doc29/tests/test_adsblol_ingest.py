import unittest
import tempfile
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from implementation.models.aircraft_doc29.reclip_paths import derive as reclip_pilot
from implementation.models.aircraft_doc29.ingest_ghcnh_kvny import parse as parse_kvny_weather

from implementation.models.aircraft_doc29.ingest_adsblol_day import (
    SegmentSpool, _assemble_output, _clip_segment, _point_record,
)


class ReadsbAltitudeSemanticsTests(unittest.TestCase):
    def test_published_shape_flags_zero_keeps_baro_and_independent_geometric(self):
        # readsb README-json.md example shape: flags=0 with both raw[3] and
        # raw[10]. The flags qualify raw[3], not presence of raw[10].
        row = [0, 34.2, -118.4, 25125, 240, 270, 0, -512, {},
               "adsb_icao", 25875, -480, 221, None]
        point = _point_record(row, 1768470000)
        self.assertEqual(point["altitude_ft"], 25125.0)
        self.assertEqual(point["altitude_datum"], "barometric_pressure_reference_unspecified")
        self.assertEqual(point["geometric_altitude_ft"], 25875.0)
        self.assertEqual(point["geometric_altitude_datum"], "geometric_WGS84_ellipsoid")
        self.assertEqual(point["vertical_rate_datum"], "barometric_or_unknown")
        self.assertEqual(point["geometric_vertical_rate_fpm"], -480.0)
        self.assertEqual(point["indicated_airspeed_kt"], 221.0)

    def test_flag_8_marks_primary_altitude_geometric_without_requiring_raw10(self):
        row = [0, 34.2, -118.4, 25875, 240, 270, 8, -480, {}, "adsb_icao"]
        point = _point_record(row, 1768470000)
        self.assertEqual(point["altitude_datum"], "geometric_WGS84_ellipsoid")
        self.assertIsNone(point["geometric_altitude_ft"])

    def test_ground_and_null_altitude_preserve_independent_channel(self):
        ground = _point_record([0, 34.2, -118.4, "ground", 0, 0, 0, 0, {}, "adsb_icao", 120], 1768470000)
        self.assertEqual((ground["altitude_state"], ground["altitude_datum"]), ("ground", "surface-state"))
        self.assertEqual(ground["geometric_altitude_ft"], 120.0)
        missing = _point_record([0, 34.2, -118.4, None, 0, 0, 0, 0, {}, "adsb_icao", None], 1768470000)
        self.assertEqual(missing["altitude_state"], "unknown")
        self.assertEqual(missing["geometric_altitude_state"], "unknown")

    def test_actual_kvny_ghcnh_weather_window_is_station_scoped_and_utc_exact(self):
        repo = Path(__file__).resolve().parents[4]
        source = repo/"implementation/work/autonomous_delivery_2026_10_02/source_evidence/ghcnh_van_nuys_2026.psv"
        result = parse_kvny_weather(source)
        self.assertEqual(result["station"]["id"], "USW00023130")
        self.assertEqual((result["utc_window"]["start_inclusive"], result["utc_window"]["end_exclusive"]),
                         ("2026-01-15T08:00:00Z", "2026-01-16T08:00:00Z"))
        self.assertEqual(result["observation_count"], 25)
        self.assertEqual(result["source_station_id_counts"], {"ICAO-KVNY": 25})
        self.assertEqual(result["candidate_pressure_altitude_correction_ft_from_standard_summary"]["count"], 25)
        self.assertTrue(all(record["timestamp_local"].startswith("2026-01-15T") for record in result["records"]))


def _segment_point(epoch, lat, lon, *, track=0, primary_datum="barometric_pressure_reference_unspecified",
                   geometric_datum="geometric_WGS84_ellipsoid", new_leg=False, stale=False):
    return {
        "epoch": epoch, "lat": lat, "lon": lon, "altitude_ft": 1000.0,
        "altitude_state": "numeric", "altitude_datum": primary_datum,
        "geometric_altitude_ft": 1200.0, "geometric_altitude_state": "numeric",
        "geometric_altitude_datum": geometric_datum, "groundspeed_kt": 120.0,
        "track_deg": track, "vertical_rate_fpm": -100.0,
        "vertical_rate_datum": "barometric_or_unknown", "geometric_vertical_rate_fpm": -90.0,
        "indicated_airspeed_kt": 110.0, "position_source": "adsb_icao",
        "stale": stale, "new_leg": new_leg, "position_quality": {"nic": 8},
    }


class FragmentContinuityTests(unittest.TestCase):
    def test_disk_spool_joins_only_shared_endpoints_and_never_emits_identity(self):
        spool = SegmentSpool(max_bytes=10_000_000)
        def p(second, lon):
            return {"timestamp_utc": f"2026-01-15T08:00:{second:02d}Z", "lat": 34.0, "lon": lon}
        try:
            spool.add("private-transient-id", "C172", (p(0, -118.5), p(1, -118.4)))
            spool.add("private-transient-id", "C172", (p(1, -118.4), p(2, -118.3)))
            spool.add("private-transient-id", "C172", (p(4, -118.3), p(5, -118.2)))
            paths = list(spool.iter_paths())
            self.assertEqual(len(paths), 2)
            self.assertEqual([len(points) for _, points in paths], [3, 2])
            self.assertNotIn("private-transient-id", repr(paths))
        finally:
            spool.close()

    def test_shortest_track_angle_and_mixed_altitude_datum_guard(self):
        clipped = _clip_segment(
            _segment_point(1768464000, 33.95, -118.95, track=359, primary_datum="baro-a"),
            _segment_point(1768464010, 33.95, -118.85, track=1, primary_datum="baro-b"),
        )
        self.assertIsNotNone(clipped)
        midpoint = clipped[0]
        self.assertAlmostEqual(midpoint["track_deg"], 0.0, places=9)
        self.assertIsNone(midpoint["altitude_ft"])
        self.assertEqual(midpoint["altitude_datum"], "mixed_or_unknown")
        self.assertAlmostEqual(midpoint["geometric_altitude_ft"], 1200.0)

    def test_pilot_reclip_discards_corner_tangency_without_zero_duration_edge(self):
        def point(epoch, lat, lon):
            return {"timestamp_utc": datetime.fromtimestamp(epoch, timezone.utc).isoformat().replace("+00:00", "Z"),
                    "lat": lat, "lon": lon, "altitude_ft": 1000.0, "altitude_state": "numeric",
                    "altitude_datum": "barometric_pressure_reference_unspecified", "geometric_altitude_ft": 1200.0,
                    "geometric_altitude_state": "numeric", "geometric_altitude_datum": "geometric_WGS84_ellipsoid",
                    "groundspeed_kt": 100.0, "track_deg": 90.0, "vertical_rate_fpm": 0.0,
                    "vertical_rate_datum": "barometric_or_unknown", "geometric_vertical_rate_fpm": 0.0,
                    "indicated_airspeed_kt": 90.0, "position_source": "adsb_icao", "stale": False,
                    "new_leg": False, "position_quality": {}}
        broad = {"schema":"quiet_la_adsblol_regional_activity_paths_v2",
                 "utc_window":{"start_inclusive":"2026-01-15T08:00:00Z","end_exclusive":"2026-01-16T08:00:00Z"},
                 "paths":[{"type_code":"C172","points":[point(1768470000,33.8,-118.91),
                                                               point(1768470010,33.9,-118.9),
                                                               point(1768470020,34.0,-118.85)]}]}
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); source=root/"broad.json"; output=root/"pilot.json"; receipt=root/"receipt.json"
            source.write_text(json.dumps(broad,separators=(",",":")))
            result=reclip_pilot(source,output,receipt)
            normalized=json.loads(output.read_text())
            self.assertEqual(result["retained_adjacent_segments"],1)
            self.assertEqual(normalized["path_count"],1)
            pts=normalized["paths"][0]["points"]
            self.assertEqual(len(pts),2)
            self.assertLess(datetime.fromisoformat(pts[0]["timestamp_utc"].replace("Z","+00:00")),
                            datetime.fromisoformat(pts[1]["timestamp_utc"].replace("Z","+00:00")))

    def test_only_exact_shared_endpoint_fragments_join(self):
        shared = {"timestamp_utc": "2026-01-15T08:00:01Z", "lat": 34.0, "lon": -118.4}
        first = {"type_code": "A320", "points": [
            {"timestamp_utc": "2026-01-15T08:00:00Z", "lat": 34.0, "lon": -118.5}, shared.copy()]}
        continuous = {"type_code": "A320", "points": [
            shared.copy(), {"timestamp_utc": "2026-01-15T08:00:02Z", "lat": 34.0, "lon": -118.3}]}
        disjoint = {"type_code": "A320", "points": [
            {"timestamp_utc": "2026-01-15T08:00:03Z", "lat": 34.0, "lon": -118.3},
            {"timestamp_utc": "2026-01-15T08:00:04Z", "lat": 34.0, "lon": -118.2}]}
        paths = _assemble_output({"transient": [first, continuous, disjoint]})
        self.assertEqual(len(paths), 2)
        self.assertEqual(len(paths[0]["points"]), 3)
        self.assertEqual(len(paths[1]["points"]), 2)

    def test_stale_new_leg_and_bbox_exit_reentry_cannot_be_reconnected(self):
        # A stale/new-leg exclusion leaves distinct clipped endpoints; the
        # assembler must keep them separate even when the elapsed time is short.
        one = {"type_code": "C172", "points": [
            {"timestamp_utc": "2026-01-15T08:00:00Z", "lat": 34.0, "lon": -118.5},
            {"timestamp_utc": "2026-01-15T08:00:01Z", "lat": 34.0, "lon": -118.4}]}
        after_break = {"type_code": "C172", "points": [
            {"timestamp_utc": "2026-01-15T08:00:02Z", "lat": 34.0, "lon": -118.4},
            {"timestamp_utc": "2026-01-15T08:00:03Z", "lat": 34.0, "lon": -118.3}]}
        outside_and_reentry = {"type_code": "C172", "points": [
            {"timestamp_utc": "2026-01-15T08:00:04Z", "lat": 34.0, "lon": -118.1},
            {"timestamp_utc": "2026-01-15T08:00:05Z", "lat": 34.0, "lon": -118.2}]}
        paths = _assemble_output({"transient": [one, after_break, outside_and_reentry]})
        self.assertEqual(len(paths), 3)

    def test_streaming_broad_to_pilot_reclip_keeps_exit_and_reentry_separate(self):
        def point(seconds, lon):
            ts = datetime(2026, 1, 15, 8, tzinfo=timezone.utc) + timedelta(seconds=seconds)
            stamp = ts.isoformat().replace("+00:00", "Z")
            return {
                "timestamp_utc": stamp, "lat": 34.0, "lon": lon,
                "altitude_ft": 1000.0, "altitude_state": "numeric",
                "altitude_datum": "barometric_pressure_reference_unspecified",
                "geometric_altitude_ft": 1200.0, "geometric_altitude_state": "numeric",
                "geometric_altitude_datum": "geometric_WGS84_ellipsoid",
                "groundspeed_kt": 120.0, "track_deg": 90.0,
                "vertical_rate_fpm": 0.0, "vertical_rate_datum": "barometric_or_unknown",
                "geometric_vertical_rate_fpm": 0.0, "indicated_airspeed_kt": 110.0,
                "position_source": "adsb_icao", "stale": False,
                "position_quality": {"nic": 8},
            }
        broad = {
            "schema": "quiet_la_adsblol_regional_activity_paths_v2",
            "utc_window": {"start_inclusive": "2026-01-15T08:00:00Z", "end_exclusive": "2026-01-16T08:00:00Z"},
            "paths": [{"path_id": "anonymous", "type_code": "C172", "points": [
                point(0, -118.95), point(10, -118.85), point(20, -118.7),
                point(30, -117.0), point(40, -118.7),
            ]}],
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, output, receipt = root/"broad.json", root/"pilot.json", root/"receipt.json"
            source.write_text(json.dumps(broad, separators=(",", ":")))
            result = reclip_pilot(source, output, receipt)
            normalized = json.loads(output.read_text())
            self.assertEqual(normalized["path_count"], 2)
            self.assertEqual(result["retained_path_count"], 2)
            self.assertNotIn("anonymous", output.read_text())
            self.assertGreater(result["retained_adjacent_segments"], 0)


if __name__ == "__main__":
    unittest.main()
