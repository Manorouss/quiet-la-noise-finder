import math
import unittest
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from implementation.models.siren_events.event_exposure import (
    ExposureError,
    ReceivedEvent,
    combine_sel_db,
    laeq_from_sel_db,
    received_sel_db,
    timed_laeq_db,
)


class EventExposureTests(unittest.TestCase):
    def test_la_eq_to_standard_one_second_sel(self):
        self.assertAlmostEqual(received_sel_db(80.0, 10.0), 90.0)

    def test_sel_to_period_average(self):
        self.assertAlmostEqual(laeq_from_sel_db(90.0, 3600.0), 54.4369749923)

    def test_one_per_second_same_event_equals_event_level(self):
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        events = [ReceivedEvent(f"e{i}", start + timedelta(seconds=i), 1.0, 60.0) for i in range(3600)]
        self.assertAlmostEqual(timed_laeq_db(events, start, start + timedelta(hours=1)), 60.0)

    def test_equal_events_over_double_period_preserve_laeq(self):
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        one = ReceivedEvent("a", start, 10.0, 80.0)
        two = ReceivedEvent("b", start + timedelta(seconds=10), 10.0, 80.0)
        result = timed_laeq_db([one, two], start, start + timedelta(seconds=20))
        self.assertAlmostEqual(result, 80.0)

    def test_two_independent_equal_exposures_add_three_db(self):
        self.assertAlmostEqual(combine_sel_db([90.0, 90.0]), 90.0 + 10.0 * math.log10(2.0))

    def test_real_report_input_vector_is_received_event_arithmetic(self):
        # Passby 1: Appendix E reports LAeq 83.1 dBA and a 24.2-second window.
        self.assertAlmostEqual(received_sel_db(83.1, 24.2), 96.9382, places=3)

    def test_rejects_ambiguous_or_non_event_metrics(self):
        for metric in ("Leq", "Lmax", "L10", "1/10 s SEL"):
            with self.subTest(metric=metric), self.assertRaises(ExposureError):
                received_sel_db(90.0, 10.0, metric=metric)
        with self.assertRaises(ExposureError):
            received_sel_db(90.0, 10.0, weighting="Z")

    def test_rejects_missing_or_invalid_duration_and_nonfinite_level(self):
        for duration in (None, 0, -1, float("nan"), float("inf")):
            with self.subTest(duration=duration), self.assertRaises(ExposureError):
                received_sel_db(90.0, duration)
        with self.assertRaises(ExposureError):
            received_sel_db(float("nan"), 10.0)

    def test_rejects_naive_time_and_events_crossing_period_edge(self):
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        with self.assertRaises(ExposureError):
            timed_laeq_db([ReceivedEvent("naive", datetime(2026, 1, 1), 1, 60)], start, start + timedelta(hours=1))
        with self.assertRaises(ExposureError):
            timed_laeq_db([ReceivedEvent("edge", start + timedelta(minutes=59, seconds=59.5), 1, 60)], start, start + timedelta(hours=1))

    def test_rejects_duplicate_ids_and_overlapping_mixed_recording_windows(self):
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        end = start + timedelta(seconds=30)
        with self.assertRaisesRegex(ExposureError, "duplicate event_id"):
            timed_laeq_db([ReceivedEvent("same", start, 5, 60), ReceivedEvent("same", start + timedelta(seconds=5), 5, 60)], start, end)
        with self.assertRaisesRegex(ExposureError, "windows overlap"):
            timed_laeq_db([ReceivedEvent("a", start, 10, 60), ReceivedEvent("b", start + timedelta(seconds=9), 5, 70)], start, end)

    def test_normalizes_numeric_string_duration_and_rejects_unbounded_duration(self):
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        event = ReceivedEvent("string-duration", start, "1", 60)
        self.assertAlmostEqual(timed_laeq_db([event], start, start + timedelta(seconds=1)), 60.0)
        with self.assertRaises(ExposureError):
            ReceivedEvent("huge", start, 1e30, 60).validated_bounds()

    def test_empty_period_has_zero_energy(self):
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        self.assertEqual(timed_laeq_db([], start, start + timedelta(hours=1)), float("-inf"))

    def test_period_duration_uses_elapsed_time_across_dst(self):
        la = ZoneInfo("America/Los_Angeles")
        start = datetime(2026, 3, 8, 1, 30, tzinfo=la)
        end = datetime(2026, 3, 8, 3, 30, tzinfo=la)
        # This local-clock interval is one elapsed hour across the spring DST jump.
        event = ReceivedEvent("event", start, 1.0, 60.0)
        self.assertAlmostEqual(timed_laeq_db([event], start, end), laeq_from_sel_db(60.0, 3600.0))


if __name__ == "__main__":
    unittest.main()
