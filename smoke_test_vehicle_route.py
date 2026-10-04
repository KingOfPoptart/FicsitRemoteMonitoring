#!/usr/bin/env python3
"""Post-build smoke test for the vehicle route / PathName fix.

This project has no unit test framework -- it's a compiled UE4 plugin whose
only existing "test" is the self-hosted CI job that builds it against a full
SatisfactoryModLoader checkout. This script is the practical substitute: run
it against a *running dedicated server with the patched plugin loaded* to
verify the JSON contract the patch promises.

Usage:
  python3 smoke_test_vehicle_route.py [host:port]   # default 127.0.0.1:8080

Checks, per vehicle returned by /getVehicles:
  - "PathName" is present and is NOT the literal placeholder "PathName"
  - "Route" is present and is a list
  - each Route entry has Index, Name, IsStation, IsNextStop, PathNodeGUID
  - "NextStop" / "NextStopIndex" / "IsCurrentlyDocking" are present
  - if NextStopIndex is a valid index into Route, NextStop matches that
    waypoint's Name, and exactly one Route entry has IsNextStop == true
  - if PathName is non-empty, it equals the Route names joined by " -> "
  - station names in Route actually come from /getTruckStation (sanity
    cross-check, not a hard requirement -- some docks are non-truck-station
    actors FRM doesn't enumerate, so this only WARNS on mismatch)

Exit code 0 = all hard checks passed, 1 = failures, 2 = couldn't reach server.
"""

import json
import sys
import urllib.error
import urllib.request

DEFAULT_HOST = "127.0.0.1:8080"


def fetch(host, endpoint, timeout=8):
    url = f"http://{host}/{endpoint}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return json.load(resp)
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        print(f"FATAL: cannot reach {url}: {exc}")
        sys.exit(2)
    except json.JSONDecodeError as exc:
        print(f"FATAL: {url} returned non-JSON: {exc}")
        sys.exit(2)


def check_vehicle(v, station_names, failures, warnings):
    vid = v.get("ID", "<no ID>")

    path_name = v.get("PathName")
    if path_name is None:
        failures.append(f"{vid}: missing PathName")
    elif path_name == "PathName":
        failures.append(f"{vid}: PathName is still the unpatched placeholder literal")

    route = v.get("Route")
    if route is None:
        failures.append(f"{vid}: missing Route")
        return
    if not isinstance(route, list):
        failures.append(f"{vid}: Route is not a list ({type(route).__name__})")
        return

    required_waypoint_keys = {"Index", "Name", "IsStation", "IsNextStop", "PathNodeGUID"}
    next_stop_flags = 0
    names = []
    for i, wp in enumerate(route):
        missing = required_waypoint_keys - set(wp.keys())
        if missing:
            failures.append(f"{vid}: Route[{i}] missing keys {sorted(missing)}")
            continue
        if wp["Index"] != i:
            failures.append(f"{vid}: Route[{i}].Index == {wp['Index']}, expected {i}")
        if wp["IsNextStop"]:
            next_stop_flags += 1
        names.append(wp["Name"])
        if wp["IsStation"] and wp["Name"] not in station_names and wp["Name"] != "Unnamed Station":
            warnings.append(
                f"{vid}: Route[{i}] claims station '{wp['Name']}' not seen in /getTruckStation "
                "(may be a non-truck-station dock type -- not necessarily a bug)"
            )

    if route and next_stop_flags != 1:
        failures.append(f"{vid}: expected exactly 1 Route entry with IsNextStop=true, found {next_stop_flags}")

    for key in ("NextStop", "NextStopIndex", "IsCurrentlyDocking"):
        if key not in v:
            failures.append(f"{vid}: missing {key}")

    next_stop = v.get("NextStop")
    next_stop_index = v.get("NextStopIndex")
    if isinstance(next_stop_index, (int, float)) and 0 <= int(next_stop_index) < len(route):
        expected = route[int(next_stop_index)]["Name"]
        if next_stop != expected:
            failures.append(
                f"{vid}: NextStop={next_stop!r} does not match Route[{int(next_stop_index)}].Name={expected!r}"
            )

    if path_name and route:
        expected_path_name = " -> ".join(names)
        if path_name != expected_path_name:
            failures.append(
                f"{vid}: PathName={path_name!r} does not equal joined Route names {expected_path_name!r}"
            )
    elif not route and path_name not in (None, ""):
        failures.append(f"{vid}: Route is empty but PathName={path_name!r} (expected empty string)")


def main():
    host = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_HOST

    vehicles = fetch(host, "getVehicles")
    stations = fetch(host, "getTruckStation")
    station_names = {s.get("Name") for s in stations if s.get("Name")}

    if not isinstance(vehicles, list):
        print(f"FATAL: /getVehicles did not return a list ({type(vehicles).__name__})")
        sys.exit(2)

    print(f"Checking {len(vehicles)} vehicle(s) from {host} against {len(station_names)} known stations...\n")

    failures, warnings = [], []
    for v in vehicles:
        check_vehicle(v, station_names, failures, warnings)

    for w in warnings:
        print(f"WARN: {w}")
    for f in failures:
        print(f"FAIL: {f}")

    print()
    if failures:
        print(f"RESULT: {len(failures)} failure(s), {len(warnings)} warning(s)")
        sys.exit(1)
    print(f"RESULT: all checks passed ({len(warnings)} warning(s))")
    sys.exit(0)


if __name__ == "__main__":
    main()
