import unittest
from datetime import datetime, timezone

from implementation.models.aircraft_doc29.extract_vny_event_windows import _event_proposals


def point(epoch, lat, *, ground=False, speed=80.0, geom=1200.0, track=0.0):
    return {"timestamp_utc":datetime.fromtimestamp(epoch,timezone.utc).isoformat().replace("+00:00","Z"),
            "lat":lat,"lon":0.0,"altitude_state":"ground" if ground else "numeric",
            "groundspeed_kt":speed,"track_deg":track,
            "geometric_altitude_state":"numeric" if geom is not None else "unknown",
            "geometric_altitude_datum":"geometric_WGS84_ellipsoid" if geom is not None else "unknown",
            "geometric_altitude_ft":geom,"position_source":"adsb_icao"}


RWY={"runway_end":"16L","alignment_deg_true":0.0,"threshold":(0.0,0.0)}


class VNYEventWindowTests(unittest.TestCase):
    def test_touch_and_go_has_two_temporally_witnessed_events(self):
        trace={"path_id":"internal","type_code":"P28A","points":[
            point(1000,-0.025,speed=80),point(1050,-0.01,speed=70),
            point(1100,0.0001,ground=True,speed=45,geom=None),
            point(1110,0.006,speed=60,geom=1350),point(1120,0.012,speed=75,geom=1550)]}
        events=_event_proposals(trace,RWY)
        self.assertEqual({e["operation"] for e in events},{"arrival","departure"})
        arrival=next(e for e in events if e["operation"]=="arrival")
        departure=next(e for e in events if e["operation"]=="departure")
        self.assertLessEqual(arrival["event_window"]["elapsed_seconds"],300)
        self.assertLessEqual(departure["event_window"]["elapsed_seconds"],240)
        self.assertEqual(arrival["ground_witness"]["point_index_within_internal_fragment"],2)
        self.assertEqual(departure["ground_witness"]["point_index_within_internal_fragment"],2)

    def test_distant_surface_contact_does_not_match_old_approach(self):
        trace={"path_id":"internal","type_code":"P28A","points":[
            point(1000,-0.025,speed=80),point(1400,0.0001,ground=True,speed=30,geom=None)]}
        events=_event_proposals(trace,RWY)
        self.assertEqual(events,[])

    def test_departure_rejects_incompatible_height_datum(self):
        trace={"path_id":"internal","type_code":"P28A","points":[
            point(1000,0.0001,ground=True,speed=4,geom=None),
            {**point(1010,0.006,speed=60,geom=1500),"geometric_altitude_datum":"mixed_or_unknown"},
            point(1020,0.012,speed=80,geom=1800)]}
        events=_event_proposals(trace,RWY)
        self.assertNotIn("departure",{e["operation"] for e in events})


if __name__=="__main__":unittest.main()
