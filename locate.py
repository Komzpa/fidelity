#!/usr/bin/env python3
import argparse
import json
import os

import databases.offline.binary
import databases.offline.gps
import databases.offline.spatial
import databases.offline.timezone
import databases.online.free.beacondb
import databases.online.free.ip_api
import databases.online.free.ipapi
import databases.online.keyed.abstractapi
import databases.online.keyed.combain
import databases.online.keyed.google_geolocation
import databases.online.keyed.here
import databases.online.keyed.maxmind2
import databases.online.keyed.opencellid
import databases.online.keyed.unwiredlabs
import databases.online.keyed.yandex
import sensors.arp
import sensors.ble
import sensors.gpsd
import sensors.iwlist
import sensors.modemmanager
import sensors.nmea
import sensors.nmcli
import sensors.networkmanager
import observations

ROOT = os.path.abspath(os.path.dirname(__file__))

SENSORS = (
    sensors.networkmanager.get_state,
    sensors.nmcli.get_state,
    sensors.iwlist.get_state,
    sensors.ble.get_state,
    sensors.gpsd.get_state,
    sensors.nmea.get_state,
    sensors.modemmanager.get_state,
    sensors.arp.get_state,
)

OFFLINE_PROVIDERS = (
    databases.offline.gps.get_location,
    databases.offline.spatial.get_location,
    databases.offline.timezone.get_location,
    databases.offline.binary.get_location,
)


class Provider:
    def __init__(
        self,
        get_location,
        *,
        branch,
        credential_envs=(),
    ):
        self.get_location = get_location
        self.branch = branch
        self.credential_envs = credential_envs

    @property
    def name(self):
        return _callable_name(self.get_location)

    def skip_reason(self):
        if self.credential_envs and not _credential_envs_present(self.credential_envs):
            return "skipped_missing_credentials"
        return None


ONLINE_FREE_PROVIDERS = (
    Provider(databases.online.free.beacondb.get_location, branch="free"),
    Provider(databases.online.free.ipapi.get_location, branch="free"),
    Provider(
        databases.online.free.ip_api.get_location,
        branch="free",
    ),
)

ONLINE_KEYED_PROVIDERS = (
    Provider(
        databases.online.keyed.google_geolocation.get_location,
        branch="keyed",
        credential_envs=("FIDELITY_GOOGLE_GEOLOCATION_API_KEY", "GOOGLE_GEOLOCATION_API_KEY"),
    ),
    Provider(
        databases.online.keyed.here.get_location,
        branch="keyed",
        credential_envs=("FIDELITY_HERE_API_KEY", "HERE_API_KEY"),
    ),
    Provider(
        databases.online.keyed.unwiredlabs.get_location,
        branch="keyed",
        credential_envs=("FIDELITY_UNWIREDLABS_TOKEN", "UNWIREDLABS_TOKEN"),
    ),
    Provider(
        databases.online.keyed.opencellid.get_location,
        branch="keyed",
        credential_envs=("FIDELITY_OPENCELLID_API_KEY", "OPENCELLID_API_KEY"),
    ),
    Provider(
        databases.online.keyed.combain.get_location,
        branch="keyed",
        credential_envs=("FIDELITY_COMBAIN_API_KEY", "COMBAIN_API_KEY"),
    ),
    Provider(
        databases.online.keyed.yandex.get_location,
        branch="keyed",
        credential_envs=("FIDELITY_YANDEX_LOCATOR_API_KEY", "YANDEX_LOCATOR_API_KEY"),
    ),
    Provider(
        databases.online.keyed.maxmind2.get_location,
        branch="keyed",
        credential_envs=(
            ("FIDELITY_MAXMIND_ACCOUNT_ID", "MAXMIND_ACCOUNT_ID"),
            ("FIDELITY_MAXMIND_LICENSE_KEY", "MAXMIND_LICENSE_KEY"),
        ),
    ),
    Provider(
        databases.online.keyed.abstractapi.get_location,
        branch="keyed",
        credential_envs=("FIDELITY_ABSTRACT_API_KEY", "ABSTRACT_API_KEY"),
    ),
)

ONLINE_PROVIDERS = ONLINE_FREE_PROVIDERS + ONLINE_KEYED_PROVIDERS


def _callable_name(func):
    return "%s.%s" % (func.__module__, func.__name__)


def _credential_envs_present(credential_envs):
    for group in credential_envs:
        names = (group,) if isinstance(group, str) else group
        if not any(os.environ.get(name) for name in names):
            return False
    return True


def _provider_callable(provider):
    return provider.get_location if isinstance(provider, Provider) else provider


def _provider_name(provider):
    return provider.name if isinstance(provider, Provider) else _callable_name(provider)


def _provider_branch(provider):
    return provider.branch if isinstance(provider, Provider) else "legacy"


def _provider_skip_reason(provider):
    if isinstance(provider, Provider):
        return provider.skip_reason()
    return None


def select_online_providers(profile):
    if profile == "free":
        return ONLINE_FREE_PROVIDERS
    if profile == "keyed":
        return ONLINE_KEYED_PROVIDERS
    if profile == "all":
        return ONLINE_PROVIDERS
    raise ValueError("unknown online profile: %s" % profile)


def format_link(loc):
    if not loc:
        return "cannot determine location"

    position = loc["position"]
    return ("http://www.openstreetmap.org/?mlat=%s&mlon=%s&zoom=16 (accuracy %s m, service %s)") % (
        position["latitude"],
        position["longitude"],
        position["accuracy"],
        loc["service"],
    )


def filter_accuracy(locations, accuracy):
    locations = [loc for loc in locations if loc]
    locations.sort(key=lambda loc: loc["position"]["accuracy"])
    is_good = bool(locations and locations[0]["position"]["accuracy"] <= accuracy)
    return is_good, locations


def _wifi_key(ap):
    return ap.get("mac", "").upper()


def _wifi_is_better(candidate, current):
    candidate_ss = candidate.get("ss")
    current_ss = current.get("ss")
    if candidate.get("ssid") and not current.get("ssid"):
        return True
    if candidate_ss is None or current_ss is None:
        return False
    if candidate_ss <= 0 < current_ss:
        return True
    if candidate_ss <= 0 and current_ss <= 0:
        return candidate_ss > current_ss
    if candidate_ss > 0 and current_ss > 0:
        return candidate_ss > current_ss
    return False


def _merge_sensor_state(merged, state):
    if not state:
        return

    if state.get("wifi"):
        wifi = {_wifi_key(ap): ap for ap in merged.get("wifi", [])}
        for ap in state["wifi"]:
            key = _wifi_key(ap)
            if not key:
                continue
            normalized = dict(ap)
            normalized["mac"] = key
            if key not in wifi or _wifi_is_better(normalized, wifi[key]):
                wifi[key] = normalized
        merged["wifi"] = list(wifi.values())

    if state.get("ip"):
        merged["ip"] = state["ip"]
    if state.get("gps"):
        merged["gps"] = state["gps"]
    if state.get("clock"):
        merged["clock"] = state["clock"]
    if state.get("cell"):
        merged["cell"] = state["cell"]
    if state.get("operators"):
        merged["operators"] = state["operators"]
    if state.get("modem"):
        merged["modem"] = state["modem"]
    if state.get("ble"):
        ble = {device.get("mac", "").upper(): device for device in merged.get("ble", [])}
        for device in state["ble"]:
            key = device.get("mac", "").upper()
            if not key:
                continue
            normalized = dict(device)
            normalized["mac"] = key
            ble[key] = normalized
        merged["ble"] = list(ble.values())


def get_sensor_state(diagnostics=None):
    merged = {}
    for sensor in SENSORS:
        name = _callable_name(sensor)
        state = sensor()
        if diagnostics is not None:
            diagnostics["sensors"].append(
                {
                    "name": name,
                    "status": "used" if state else "empty",
                }
            )
        _merge_sensor_state(merged, state)
    return merged


def _provider_locations(providers, sensor_state, diagnostics=None):
    locations = []
    for provider in providers:
        skip_reason = _provider_skip_reason(provider)
        if skip_reason:
            if diagnostics is not None:
                diagnostics["providers"].append(
                    {
                        "name": _provider_name(provider),
                        "branch": _provider_branch(provider),
                        "status": skip_reason,
                        "service": None,
                        "accuracy": None,
                    }
                )
            locations.append(False)
            continue

        location = _provider_callable(provider)(sensor_state)
        if diagnostics is not None:
            diagnostics["providers"].append(
                {
                    "name": _provider_name(provider),
                    "branch": _provider_branch(provider),
                    "status": "location" if location else "no_location",
                    "service": location.get("service") if location else None,
                    "accuracy": location["position"].get("accuracy") if location else None,
                }
            )
        locations.append(location)
    return locations


def locate(include_online=True, accuracy=100000, collect_diagnostics=False, online_profile="all"):
    os.chdir(ROOT)
    diagnostics = None
    if collect_diagnostics:
        diagnostics = {
            "accuracy_threshold": accuracy,
            "fallback": None,
            "online_attempted": False,
            "providers": [],
            "sensors": [],
        }

    sensor_state = get_sensor_state(diagnostics=diagnostics)
    locations = _provider_locations(OFFLINE_PROVIDERS, sensor_state, diagnostics=diagnostics)
    is_good, locations = filter_accuracy(locations, accuracy)

    if include_online and not is_good:
        if diagnostics is not None:
            diagnostics["online_attempted"] = True
        online_providers = select_online_providers(online_profile)
        locations.extend(
            _provider_locations(online_providers, sensor_state, diagnostics=diagnostics)
        )
        _, locations = filter_accuracy(locations, accuracy)

    if diagnostics is not None:
        if locations and locations[0]["position"]["accuracy"] > accuracy:
            diagnostics["fallback"] = "best location is worse than requested accuracy"
        if include_online and not diagnostics["online_attempted"]:
            diagnostics["fallback"] = "offline location met requested accuracy"
        return sensor_state, locations, diagnostics

    return sensor_state, locations


def _json_result(sensor_state, locations, diagnostics=None):
    result = {"sensors": sensor_state, "locations": locations}
    if diagnostics is not None:
        result["diagnostics"] = diagnostics
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Locate this machine from Wi-Fi, ARP, and IP hints."
    )
    parser.add_argument(
        "--offline-only", action="store_true", help="skip network geolocation providers"
    )
    parser.add_argument(
        "--online-profile",
        choices=("all", "free", "keyed"),
        default="all",
        help=(
            "online provider branch to use: all available providers, free/no-key providers only, "
            "or credential-gated providers only"
        ),
    )
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON")
    parser.add_argument(
        "--diagnostics",
        action="store_true",
        help="include sensor/provider decisions in JSON or human output",
    )
    parser.add_argument(
        "--accuracy",
        type=float,
        default=100000,
        help="good-enough accuracy threshold in meters",
    )
    parser.add_argument(
        "--record-observation",
        action="store_true",
        help="append visible Wi-Fi/BLE/GPS observation log and learn GPS-anchored Wi-Fi cache rows",
    )
    parser.add_argument(
        "--observation-log",
        default=observations.DEFAULT_LOG_PATH,
        help="JSONL observation log path",
    )
    parser.add_argument(
        "--observation-wifi-cache",
        default=observations.DEFAULT_WIFI_CACHE_PATH,
        help="binary Wi-Fi cache path for GPS-anchored observations",
    )
    parser.add_argument(
        "--observation-max-cache-accuracy",
        type=float,
        default=100,
        help="maximum GPS accuracy in meters accepted for Wi-Fi cache learning",
    )
    parser.add_argument(
        "--observation-allow-estimated",
        action="store_true",
        help="allow best non-GPS location as the observation log anchor",
    )
    args = parser.parse_args(argv)

    result = locate(
        include_online=not args.offline_only,
        accuracy=args.accuracy,
        collect_diagnostics=args.diagnostics,
        online_profile=args.online_profile,
    )
    if args.diagnostics:
        sensor_state, locations, diagnostics = result
    else:
        sensor_state, locations = result
        diagnostics = None

    observation = None
    if args.record_observation:
        observation = observations.write_observation(
            sensor_state,
            locations,
            log_path=args.observation_log,
            wifi_cache_path=args.observation_wifi_cache,
            max_cache_accuracy=args.observation_max_cache_accuracy,
            allow_estimated=args.observation_allow_estimated,
        )
    elif observations.should_record_from_env():
        observation = observations.write_observation_from_env(sensor_state, locations)

    if args.json:
        body = _json_result(sensor_state, locations, diagnostics=diagnostics)
        if observation is not None:
            body["observation"] = observation
        print(json.dumps(body, indent=2, sort_keys=True))
        return 0

    print("Found wifi:", sensor_state)
    for location in locations:
        print(format_link(location))
    if not locations:
        print(format_link(None))
    if diagnostics is not None:
        print("Diagnostics:")
        print("  online attempted:", diagnostics["online_attempted"])
        if diagnostics["fallback"]:
            print("  fallback:", diagnostics["fallback"])
        for provider in diagnostics["providers"]:
            print(
                "  provider {name} [{branch}]: {status}".format(
                    name=provider["name"],
                    branch=provider.get("branch", "legacy"),
                    status=provider["status"],
                )
            )
    if observation is not None:
        print(
            "Observation recorded: %s, wifi cache rows: %s, spatial cache pairs: %s"
            % (
                observation["recorded"],
                observation["wifi_cache_rows"],
                observation["spatial_cache_pairs"],
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
