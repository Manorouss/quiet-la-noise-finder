#!/usr/bin/env python3
"""US train types for the CNOSSOS-EU railway model of NoiseModelling 6.0.

NoiseModelling's vehicle and train-set files are French (SNCF). A custom file replaces the default, so the
defaults are copied and US approximations added (keys starting with US_ / LA_):

  US_DIESEL_PAX   passenger diesel locomotive (Metrolink F125/MP36PH, Amtrak P42/Charger): SNCF22 body
                  (4-axle diesel, 1250 mm wheels) with a louder traction source (TRACTION key below)
  US_FRT_LOCO     freight diesel (6-axle, ~3.3 MW): SNCF26 body (CC72000, 6 axles) with the same traction
  US_FRT_CAR      freight car: SNCF78 (composite brake shoes) with the highest CNOSSOS contact filter (SNCF5)
  coaches         SNCF69 (V2N bilevel, disc brakes) stands in for Bombardier/Rotem bilevels
  LA_LRV          one light-rail vehicle (Siemens P2000/S70, Kinkisharyo P3010): SNCF68 tram-train, 1 unit, 6 axles

Train sets: US_METROLINK (1 loco + 5 bilevels), US_AMTRAK (1 loco + 6), US_FREIGHT (3 locos + 80 cars),
LA_LRV_2 / LA_LRV_3 (2- / 3-car light rail), and single-vehicle sets for calibration (CAL_*).
Calibrated against the FTA reference SEL at 50 ft for a 50 mph pass-by (science/rail/calibrate.py, flat ground
G=0.5): passenger locomotive (2 units) 92.3 dBA (FTA 92), freight locomotive 91.0 (92), coach 81.6 (82).
The traction key hardly matters at 50 mph (rolling noise dominates): EU7 89.3, EU4 89.6, SNCF14 89.2 per unit.
LA_LRV still gives no emission (to fix before light rail is used).
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

DEFAULTS = Path(__file__).resolve().parents[5] / "implementation/work/source_cache/rail/nm_railway_json_6.0.0"
TRACTION = "EU7"   # diesel traction source (EU7: Class 66, an EMD design); chosen by calibrate.py


def vehicles(traction: str = TRACTION) -> dict:
    v = json.loads((DEFAULTS / "RailwayVehiclesCnossos.json").read_text())
    loco = copy.deepcopy(v["SNCF22"])
    loco.update({"Description": "US passenger diesel locomotive (approximation)", "Reference": "Quiet LA", "RefTraction": traction})
    freight_loco = copy.deepcopy(v["SNCF26"])
    freight_loco.update({"Description": "US freight diesel locomotive, 6 axles (approximation)", "Reference": "Quiet LA", "RefTraction": traction})
    car = copy.deepcopy(v["SNCF78"])
    car.update({"Description": "US freight car, composite brake shoes, heavy axle load (approximation)", "Reference": "Quiet LA", "RefContact": "SNCF5"})
    lrv = copy.deepcopy(v["SNCF68"])
    lrv.update({"Description": "LA Metro light-rail vehicle (approximation)", "Reference": "Quiet LA", "NbCoach": 1, "NbAxlePerVeh": 6, "Length": 27})
    v.update({"US_DIESEL_PAX": loco, "US_FRT_LOCO": freight_loco, "US_FRT_CAR": car, "LA_LRV": lrv})
    return v


def trainsets() -> dict:
    t = json.loads((DEFAULTS / "RailwayTrainsets.json").read_text())
    t.update({
        # A US passenger diesel is two CNOSSOS locomotive units (calibrate.py: 1 unit 89.3 dBA SEL at 50 ft / 50 mph,
        # 2 units 92.3; FTA reference 92). The 6-axle freight unit alone gives 91.0 (reference 92) and stays at 1.
        "US_METROLINK": {"US_DIESEL_PAX": 2, "SNCF69": 5},
        "US_AMTRAK": {"US_DIESEL_PAX": 2, "SNCF69": 6},
        "US_FREIGHT": {"US_FRT_LOCO": 3, "US_FRT_CAR": 80},
        "LA_LRV_2": {"LA_LRV": 2},
        "LA_LRV_3": {"LA_LRV": 3},
        "CAL_LOCO": {"US_DIESEL_PAX": 1},
        "CAL_LOCO_X2": {"US_DIESEL_PAX": 2},
        "CAL_FRT_LOCO": {"US_FRT_LOCO": 1},
        "CAL_FRT_LOCO_X2": {"US_FRT_LOCO": 2},
        "CAL_LOCO_SNCF": {"SNCF22": 1},
        "CAL_COACH": {"SNCF69": 1},
        "CAL_FRT_CAR": {"US_FRT_CAR": 1},
        "CAL_LRV": {"LA_LRV": 1},
    })
    return t


def write(folder: Path, traction: str = TRACTION) -> tuple[Path, Path]:
    folder.mkdir(parents=True, exist_ok=True)
    vpath, tpath = folder / "vehicles.json", folder / "trainsets.json"
    vpath.write_text(json.dumps(vehicles(traction), indent=1))
    tpath.write_text(json.dumps(trainsets(), indent=1))
    return vpath, tpath
