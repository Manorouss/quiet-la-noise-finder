#!/usr/bin/env python3
"""Trains per hour through a station, by CNEL period, averaged over a week of GTFS service.

Periods (as the road model): day 07-19, evening 19-22, night 22-07; the result is the average number of trains
per hour in each period, both directions, rail routes only (GTFS route_type 0, 1, 2; Thruway buses are left out).

  gtfs_counts.py <gtfs dir> "<stop name>" [--week-of 2026-10-05]
"""
from __future__ import annotations

import argparse
import collections
import csv
import datetime
import json
from pathlib import Path

HOURS = {"D": 12, "E": 3, "N": 9}
RAIL_TYPES = {"0", "1", "2"}


def table(folder: Path, name: str) -> list[dict]:
    path = folder / name
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def services(folder: Path, date: datetime.date) -> set[str]:
    ymd, weekday = date.strftime("%Y%m%d"), date.strftime("%A").lower()
    active = {r["service_id"] for r in table(folder, "calendar.txt") if r["start_date"] <= ymd <= r["end_date"] and r[weekday] == "1"}
    for r in table(folder, "calendar_dates.txt"):
        if r["date"] == ymd:
            (active.add if r["exception_type"] == "1" else active.discard)(r["service_id"])
    return active


def period(clock: str) -> str:
    hour = int(clock.split(":")[0]) % 24
    return "D" if 7 <= hour < 19 else "E" if 19 <= hour < 22 else "N"


def counts(folder: Path, stop_name: str, week_of: datetime.date) -> dict:
    routes = {r["route_id"]: r for r in table(folder, "routes.txt")}
    stops = {s["stop_id"] for s in table(folder, "stops.txt") if stop_name.lower() in s["stop_name"].lower()}
    trips = table(folder, "trips.txt")
    times = collections.defaultdict(list)   # trip -> times at the station
    for st in table(folder, "stop_times.txt"):
        if st["stop_id"] in stops:
            times[st["trip_id"]].append(st.get("departure_time") or st["arrival_time"])
    total = collections.defaultdict(lambda: collections.Counter())
    for day in range(7):
        date = week_of + datetime.timedelta(days=day)
        active = services(folder, date)
        for t in trips:
            route = routes.get(t["route_id"], {})
            if t["service_id"] not in active or route.get("route_type", "2") not in RAIL_TYPES or not times.get(t["trip_id"]):
                continue
            name = route.get("route_short_name") or route.get("route_long_name") or t["route_id"]
            total[name][period(min(times[t["trip_id"]]))] += 1
    return {name: {p: round(c[p] / 7 / HOURS[p], 3) for p in HOURS} | {"per_day": round(sum(c.values()) / 7, 1)} for name, c in total.items()}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("gtfs", type=Path)
    parser.add_argument("stop")
    parser.add_argument("--week-of", type=datetime.date.fromisoformat, default=datetime.date(2026, 10, 5))
    args = parser.parse_args()
    print(json.dumps(counts(args.gtfs, args.stop, args.week_of), indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
