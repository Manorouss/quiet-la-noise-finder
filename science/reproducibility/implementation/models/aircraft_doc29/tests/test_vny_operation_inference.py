import unittest

from implementation.models.aircraft_doc29.infer_vny_runway_operations import _candidate_for_runway


def p(lat, *, state="numeric", geom=1000.0, speed=100.0, track=0.0):
    return {"lat":lat,"lon":0.0,"timestamp_utc":"2026-01-15T12:00:00Z",
            "altitude_state":state,"geometric_altitude_state":"numeric" if geom is not None else "unknown",
            "geometric_altitude_datum":"geometric_WGS84_ellipsoid" if geom is not None else "unknown",
            "geometric_altitude_ft":geom,"groundspeed_kt":speed,"track_deg":track}


RUNWAY={"runway_end":"16X","alignment_deg_true":0.0,"threshold":(0.0,0.0)}


class VNYInferredOperationTests(unittest.TestCase):
    def test_arrival_candidate_requires_aligned_inbound_then_ground_state(self):
        result=_candidate_for_runway({"points":[p(-0.03),p(-0.015),p(0.0002,state="ground",geom=None,speed=40)]},RUNWAY)
        self.assertTrue(result["arrival_candidate"])
        self.assertEqual(result["arrival_confidence"],"high_candidate")
        self.assertFalse(result["departure_candidate"])

    def test_departure_candidate_requires_surface_anchor_and_same_datum_height_rise(self):
        result=_candidate_for_runway({"points":[p(0.0001,state="ground",geom=1000,speed=5),p(0.006,geom=1060,speed=55),p(0.012,geom=1200,speed=90)]},RUNWAY)
        self.assertTrue(result["departure_candidate"])
        self.assertFalse(result["arrival_candidate"])

    def test_no_operation_from_proximity_without_aligned_track(self):
        result=_candidate_for_runway({"points":[p(0.0001,state="ground",geom=None,speed=4,track=None)]},RUNWAY)
        self.assertFalse(result["arrival_candidate"])
        self.assertFalse(result["departure_candidate"])

    def test_departure_rejects_mixed_or_missing_geometric_height(self):
        result=_candidate_for_runway({"points":[p(0.0001,state="ground",geom=1000,speed=5),
                                                {**p(0.006,geom=1100,speed=55),"geometric_altitude_datum":"mixed_or_unknown"}]},RUNWAY)
        self.assertFalse(result["departure_candidate"])


if __name__=="__main__": unittest.main()
