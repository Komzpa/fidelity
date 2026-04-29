import argparse
import json
import os
import time
from pathlib import Path

import databases.offline.binary
import databases.offline.cache
import databases.offline.spatial

DEFAULT_LOG_PATH = "data/observations.jsonl"
DEFAULT_WIFI_CACHE_PATH = "data/our.bin"


def _enabled(value):
    return str(value).lower() in ("1", "true", "yes", "on")


def _gps_position(sensor_state):
    gps = sensor_state.get("gps") or {}
    latitude = gps.get("latitude")
    longitude = gps.get("longitude")
    if latitude is None or longitude is None:
        return None

    return {
        "latitude": latitude,
        "longitude": longitude,
        "accuracy": gps.get("accuracy"),
        "altitude": gps.get("altitude"),
        "time": gps.get("time"),
        "heading": gps.get("heading", gps.get("course", gps.get("track"))),
        "source": "gps",
    }


def _best_location_position(locations):
    if not locations:
        return None
    location = locations[0]
    if not location:
        return None
    position = location.get("position", {})
    if "latitude" not in position or "longitude" not in position:
        return None
    return {
        "latitude": position["latitude"],
        "longitude": position["longitude"],
        "accuracy": position.get("accuracy"),
        "altitude": position.get("altitude"),
        "time": position.get("time"),
        "source": location.get("service"),
    }


def anchor_position(sensor_state, locations=None, *, allow_estimated=False):
    gps = _gps_position(sensor_state)
    if gps:
        return gps
    if allow_estimated:
        return _best_location_position(locations)
    return None


def _accuracy_within(position, max_accuracy):
    if max_accuracy is None:
        return True
    accuracy = position.get("accuracy")
    if accuracy is None:
        return False
    return float(accuracy) <= float(max_accuracy)


def _wifi_cache_rows(sensor_state, position, *, max_accuracy=None):
    if not position or not _accuracy_within(position, max_accuracy):
        return []
    if position.get("source") != "gps":
        return []

    rows = []
    for ap in sensor_state.get("wifi", []):
        mac = ap.get("mac")
        if not mac:
            continue
        ssid = ap.get("ssid")
        if isinstance(ssid, str) and ssid.endswith("_nomap"):
            continue
        rows.append((mac, position["longitude"], position["latitude"]))
    return rows


def _spatial_cache_pairs(sensor_state, position, *, max_accuracy=None):
    if not position or not _accuracy_within(position, max_accuracy):
        return 0
    if position.get("source") != "gps":
        return 0

    return len(
        databases.offline.spatial._pair_samples_from_record(
            {
                "anchor": position,
                "gps": sensor_state.get("gps"),
                "wifi": sensor_state.get("wifi", []),
            },
            max_accuracy=max_accuracy,
        )
    )


def _update_binary_cache(row):
    cache = databases.offline.binary.CACHE
    if cache:
        mac, lon, lat = row
        cache[databases.offline.binary.mac2int(mac)] = (lon, lat)


def _has_observable_radio_or_gps(sensor_state):
    return bool(sensor_state.get("wifi") or sensor_state.get("ble") or sensor_state.get("gps"))


def observation_record(sensor_state, locations=None, *, source="locate", allow_estimated=False):
    if not _has_observable_radio_or_gps(sensor_state):
        return None
    return {
        "time": int(time.time() * 1000),
        "source": source,
        "anchor": anchor_position(sensor_state, locations, allow_estimated=allow_estimated),
        "wifi": sensor_state.get("wifi", []),
        "ble": sensor_state.get("ble", []),
        "cell": sensor_state.get("cell", []),
        "gps": sensor_state.get("gps"),
    }


def write_observation(
    sensor_state,
    locations=None,
    *,
    log_path=DEFAULT_LOG_PATH,
    wifi_cache_path=DEFAULT_WIFI_CACHE_PATH,
    source="locate",
    write_log=True,
    write_wifi_cache=True,
    max_cache_accuracy=100,
    allow_estimated=False,
):
    record = observation_record(
        sensor_state,
        locations,
        source=source,
        allow_estimated=allow_estimated,
    )
    if record is None:
        return {"recorded": False, "wifi_cache_rows": 0, "spatial_cache_pairs": 0}

    if write_log:
        log_file = Path(log_path)
        log_file.parent.mkdir(parents=True, exist_ok=True)
        with log_file.open("a", encoding="utf-8") as out:
            out.write(json.dumps(record, sort_keys=True))
            out.write("\n")

    spatial_pairs = 0
    if write_log:
        spatial_pairs = _spatial_cache_pairs(
            sensor_state,
            record["anchor"],
            max_accuracy=max_cache_accuracy,
        )

    wifi_rows = []
    if write_wifi_cache:
        wifi_rows = _wifi_cache_rows(
            sensor_state,
            record["anchor"],
            max_accuracy=max_cache_accuracy,
        )
        for row in wifi_rows:
            databases.offline.cache.savewifi(row, wifi_cache_path)
            _update_binary_cache(row)

    return {
        "recorded": True,
        "wifi_cache_rows": len(wifi_rows),
        "spatial_cache_pairs": spatial_pairs,
    }


def should_record_from_env():
    return _enabled(os.environ.get("FIDELITY_RECORD_OBSERVATIONS", ""))


def write_observation_from_env(sensor_state, locations=None, *, source="locate"):
    if not should_record_from_env():
        return {"recorded": False, "wifi_cache_rows": 0, "spatial_cache_pairs": 0}

    max_accuracy = os.environ.get("FIDELITY_OBSERVATION_MAX_CACHE_ACCURACY", "100")
    return write_observation(
        sensor_state,
        locations,
        log_path=os.environ.get("FIDELITY_OBSERVATION_LOG", DEFAULT_LOG_PATH),
        wifi_cache_path=os.environ.get("FIDELITY_OBSERVATION_WIFI_CACHE", DEFAULT_WIFI_CACHE_PATH),
        source=source,
        write_log=not _enabled(os.environ.get("FIDELITY_OBSERVATION_DISABLE_LOG", "")),
        write_wifi_cache=not _enabled(
            os.environ.get("FIDELITY_OBSERVATION_DISABLE_WIFI_CACHE", "")
        ),
        max_cache_accuracy=float(max_accuracy) if max_accuracy else None,
        allow_estimated=_enabled(os.environ.get("FIDELITY_OBSERVATION_ALLOW_ESTIMATED", "")),
    )


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Record one local sensor observation and optionally extend the Wi-Fi cache."
    )
    parser.add_argument("--log", default=DEFAULT_LOG_PATH, help="JSONL observation log path")
    parser.add_argument(
        "--wifi-cache", default=DEFAULT_WIFI_CACHE_PATH, help="binary Wi-Fi cache path"
    )
    parser.add_argument(
        "--max-cache-accuracy",
        type=float,
        default=100,
        help="maximum GPS accuracy in meters accepted for Wi-Fi cache learning",
    )
    parser.add_argument(
        "--allow-estimated",
        action="store_true",
        help="allow the best non-GPS location as the observation anchor",
    )
    parser.add_argument("--no-log", action="store_true", help="do not append JSONL observation log")
    parser.add_argument(
        "--no-wifi-cache",
        action="store_true",
        help="do not append GPS-anchored Wi-Fi observations to binary cache",
    )
    parser.add_argument("--json", action="store_true", help="print result as JSON")
    args = parser.parse_args(argv)

    import locate

    sensor_state, locations = locate.locate(include_online=True)
    result = write_observation(
        sensor_state,
        locations,
        log_path=args.log,
        wifi_cache_path=args.wifi_cache,
        write_log=not args.no_log,
        write_wifi_cache=not args.no_wifi_cache,
        max_cache_accuracy=args.max_cache_accuracy,
        allow_estimated=args.allow_estimated,
        source="fidelity-record-observation",
    )
    if args.json:
        print(json.dumps(result, sort_keys=True))
    else:
        print(
            "recorded: %s, wifi cache rows: %s, spatial cache pairs: %s"
            % (result["recorded"], result["wifi_cache_rows"], result["spatial_cache_pairs"])
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
