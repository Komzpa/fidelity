#!/usr/bin/env python3
import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import locate
import observations

GEOLOCATE_OFFLINE_PROVIDERS = (
    locate.databases.offline.spatial.get_location,
    locate.databases.offline.binary.get_location,
)
GEOLOCATE_PATHS = ("/v1/geolocate", "/geolocation/v1/geolocate")


def _as_bool(value, default=False):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def _signal_strength(row):
    value = row.get("signalStrength", row.get("signal"))
    if value is None:
        return None
    return int(value)


def _wifi_access_points(rows):
    access_points = []
    for row in rows or []:
        mac = row.get("macAddress") or row.get("mac")
        if not mac:
            continue
        access_point = {"mac": mac.upper()}
        for source, target in (
            ("ssid", "ssid"),
            ("age", "age"),
            ("channel", "channel"),
            ("frequency", "frequency"),
            ("signalToNoiseRatio", "snr"),
        ):
            if row.get(source) is not None:
                access_point[target] = row[source]
        signal_strength = _signal_strength(row)
        if signal_strength is not None:
            access_point["ss"] = signal_strength
        access_points.append(access_point)
    return access_points


def _bluetooth_beacons(rows):
    beacons = []
    for row in rows or []:
        mac = row.get("macAddress") or row.get("mac")
        if not mac:
            continue
        beacon = {"mac": mac.upper()}
        for source, target in (("name", "name"), ("age", "age")):
            if row.get(source) is not None:
                beacon[target] = row[source]
        signal_strength = _signal_strength(row)
        if signal_strength is not None:
            beacon["rssi"] = signal_strength
        beacons.append(beacon)
    return beacons


def _cell_towers(rows, default_radio=None):
    towers = []
    for row in rows or []:
        cell_id = row.get("cellId", row.get("newRadioCellId", row.get("cellid")))
        lac = row.get("locationAreaCode", row.get("lac"))
        mcc = row.get("mobileCountryCode", row.get("mcc"))
        mnc = row.get("mobileNetworkCode", row.get("mnc"))
        if not all(value is not None for value in (cell_id, lac, mcc, mnc)):
            continue
        tower = {
            "cellid": cell_id,
            "lac": lac,
            "mcc": mcc,
            "mnc": mnc,
        }
        radio = row.get("radioType", row.get("radio", default_radio))
        if radio:
            tower["radio"] = str(radio).upper()
        for source, target in (
            ("age", "age"),
            ("psc", "psc"),
            ("timingAdvance", "timing_advance"),
        ):
            if row.get(source) is not None:
                tower[target] = row[source]
        signal_strength = _signal_strength(row)
        if signal_strength is not None:
            tower["signal"] = signal_strength
        towers.append(tower)
    return towers


def _ip_fallback_enabled(payload):
    fallbacks = payload.get("fallbacks")
    if isinstance(fallbacks, dict):
        if "ipf" in fallbacks:
            return _as_bool(fallbacks["ipf"])
        if "all" in fallbacks:
            return _as_bool(fallbacks["all"])
    return _as_bool(payload.get("considerIp"), default=True)


def sensor_state_from_geolocate(payload, client_ip=None):
    state = {}
    wifi = _wifi_access_points(payload.get("wifiAccessPoints"))
    if wifi:
        state["wifi"] = wifi
    ble = _bluetooth_beacons(payload.get("bluetoothBeacons"))
    if ble:
        state["ble"] = ble
    cell = _cell_towers(payload.get("cellTowers"), default_radio=payload.get("radioType"))
    if cell:
        state["cell"] = cell
    if _ip_fallback_enabled(payload) and client_ip:
        state["ip"] = client_ip
    return state


def _best_location_for_sensor_state(
    sensor_state, online_profile, include_online, diagnostics=False
):
    diag = None
    if diagnostics:
        diag = {
            "online_attempted": False,
            "providers": [],
        }

    locations = locate._provider_locations(
        GEOLOCATE_OFFLINE_PROVIDERS, sensor_state, diagnostics=diag
    )
    _, locations = locate.filter_accuracy(locations, float("inf"))

    if include_online:
        if diag is not None:
            diag["online_attempted"] = True
        providers = locate.select_online_providers(online_profile)
        locations.extend(locate._provider_locations(providers, sensor_state, diagnostics=diag))
        _, locations = locate.filter_accuracy(locations, float("inf"))

    location = locations[0] if locations else None
    return location, locations, diag


def _geolocate_response(location):
    if not location:
        return None
    position = location["position"]
    return {
        "location": {
            "lat": position["latitude"],
            "lng": position["longitude"],
        },
        "accuracy": position["accuracy"],
    }


def _api_error(status, reason, message):
    return {
        "error": {
            "errors": [
                {
                    "domain": "geolocation",
                    "reason": reason,
                    "message": message,
                }
            ],
            "code": status,
            "message": message,
        }
    }


class FidelityHandler(BaseHTTPRequestHandler):
    server_version = "fidelity-http/0.1"

    def _send_json(self, status, payload):
        body = json.dumps(payload, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _query(self):
        return parse_qs(urlparse(self.path).query)

    def _read_json(self):
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8"))
        except ValueError:
            self._send_json(400, _api_error(400, "parseError", "Invalid JSON"))
            return None
        if not isinstance(payload, dict):
            self._send_json(400, _api_error(400, "parseError", "Invalid request"))
            return None
        return payload

    def _client_ip(self):
        if self.server.trust_forwarded_for:
            forwarded_for = self.headers.get("X-Forwarded-For")
            if forwarded_for:
                return forwarded_for.split(",", 1)[0].strip()
        host, _port = self.client_address
        return host

    def do_GET(self):
        parsed = urlparse(self.path)
        query = self._query()
        if parsed.path == "/health":
            self._send_json(200, {"status": "ok"})
            return
        if parsed.path == "/v1/location":
            diagnostics = query.get("diagnostics", ["0"])[0] in ("1", "true", "yes")
            result = locate.locate(
                include_online=not self.server.offline_only,
                collect_diagnostics=diagnostics,
                online_profile=self.server.online_profile,
            )
            if diagnostics:
                sensor_state, locations, diag = result
            else:
                sensor_state, locations = result
                diag = None
            body = locate._json_result(sensor_state, locations, diagnostics=diag)
            if self.server.record_observations:
                body["observation"] = observations.write_observation(
                    sensor_state,
                    locations,
                    log_path=self.server.observation_log,
                    wifi_cache_path=self.server.observation_wifi_cache,
                    max_cache_accuracy=self.server.observation_max_cache_accuracy,
                    allow_estimated=self.server.observation_allow_estimated,
                    source="fidelity-serve",
                )
            elif observations.should_record_from_env():
                body["observation"] = observations.write_observation_from_env(
                    sensor_state,
                    locations,
                    source="fidelity-serve",
                )
            self._send_json(200, body)
            return
        self._send_json(404, _api_error(404, "notFound", "Not found"))

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path not in GEOLOCATE_PATHS:
            self._send_json(404, _api_error(404, "notFound", "Not found"))
            return

        payload = self._read_json()
        if payload is None:
            return

        query = self._query()
        diagnostics = query.get("diagnostics", ["0"])[0] in ("1", "true", "yes")
        sensor_state = sensor_state_from_geolocate(payload, client_ip=self._client_ip())
        location, locations, diag = _best_location_for_sensor_state(
            sensor_state,
            self.server.online_profile,
            include_online=not self.server.offline_only,
            diagnostics=diagnostics,
        )
        response = _geolocate_response(location)
        if response is None:
            self._send_json(404, _api_error(404, "notFound", "Not found"))
            return
        if diagnostics:
            response["diagnostics"] = diag
            response["locations"] = locations
            response["sensors"] = sensor_state
        self._send_json(200, response)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, format, *args):
        if not self.server.quiet:
            super().log_message(format, *args)


class FidelityHTTPServer(ThreadingHTTPServer):
    def __init__(
        self,
        server_address,
        handler_class,
        *,
        online_profile,
        offline_only,
        quiet,
        trust_forwarded_for=False,
        record_observations=False,
        observation_log=observations.DEFAULT_LOG_PATH,
        observation_wifi_cache=observations.DEFAULT_WIFI_CACHE_PATH,
        observation_max_cache_accuracy=100,
        observation_allow_estimated=False,
    ):
        super().__init__(server_address, handler_class)
        self.online_profile = online_profile
        self.offline_only = offline_only
        self.quiet = quiet
        self.trust_forwarded_for = trust_forwarded_for
        self.record_observations = record_observations
        self.observation_log = observation_log
        self.observation_wifi_cache = observation_wifi_cache
        self.observation_max_cache_accuracy = observation_max_cache_accuracy
        self.observation_allow_estimated = observation_allow_estimated


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Serve fidelity location lookups over a small local HTTP API."
    )
    parser.add_argument("--host", default="127.0.0.1", help="address to bind")
    parser.add_argument("--port", default=8765, type=int, help="port to bind")
    parser.add_argument(
        "--online-profile",
        choices=("all", "free", "keyed"),
        default="free",
        help="online provider branch to use for geolocate requests",
    )
    parser.add_argument(
        "--offline-only", action="store_true", help="serve only offline/local-cache lookups"
    )
    parser.add_argument(
        "--trust-forwarded-for",
        action="store_true",
        help="use X-Forwarded-For as the client IP when considerIp is enabled",
    )
    parser.add_argument("--quiet", action="store_true", help="disable per-request access logs")
    parser.add_argument(
        "--record-observations",
        action="store_true",
        help=(
            "record /v1/location Wi-Fi/BLE/GPS observations and learn GPS-anchored Wi-Fi cache rows"
        ),
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
        help="allow best non-GPS /v1/location result as observation log anchor",
    )
    args = parser.parse_args(argv)

    server = FidelityHTTPServer(
        (args.host, args.port),
        FidelityHandler,
        online_profile=args.online_profile,
        offline_only=args.offline_only,
        quiet=args.quiet,
        trust_forwarded_for=args.trust_forwarded_for,
        record_observations=args.record_observations,
        observation_log=args.observation_log,
        observation_wifi_cache=args.observation_wifi_cache,
        observation_max_cache_accuracy=args.observation_max_cache_accuracy,
        observation_allow_estimated=args.observation_allow_estimated,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
