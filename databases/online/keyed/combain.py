import json
import os
import urllib.parse
import urllib.request

URL = "https://apiv2.combain.com"


def _usable_mac(mac):
    normalized = mac.upper()
    return normalized not in ("00:00:00:00:00:00", "FF:FF:FF:FF:FF:FF")


def _api_key():
    return os.environ.get("FIDELITY_COMBAIN_API_KEY") or os.environ.get("COMBAIN_API_KEY")


def _signal_strength(ap):
    value = ap.get("ss", ap.get("rssi"))
    if value is None:
        return None
    return -abs(int(value))


def _wifi_access_points(req):
    access_points = []
    seen = set()
    for ap in req.get("wifi", []):
        if not ap.get("ssid"):
            continue
        mac = ap["mac"].upper()
        if mac in seen:
            continue
        seen.add(mac)

        access_point = {
            "macAddress": mac,
            "ssid": ap["ssid"],
        }
        signal_strength = _signal_strength(ap)
        if signal_strength is not None:
            access_point["signalStrength"] = signal_strength
        access_points.append(access_point)
    return access_points


def _bluetooth_beacons(req):
    beacons = []
    seen = set()
    for beacon in req.get("ble", []):
        if not beacon.get("mac"):
            continue
        mac = beacon["mac"].upper()
        if not _usable_mac(mac):
            continue
        if mac in seen:
            continue
        seen.add(mac)

        item = {"macAddress": mac}
        if beacon.get("name"):
            item["name"] = beacon["name"]
        signal_strength = _signal_strength(beacon)
        if signal_strength is not None:
            item["signalStrength"] = signal_strength
        beacons.append(item)
    return beacons


def _position_type(access_points, beacons):
    if access_points and beacons:
        return "mixed"
    if access_points:
        return "wifi"
    return "ble"


def get_location(req=None):
    req = req or {}
    key = _api_key()
    if not key:
        return False

    access_points = _wifi_access_points(req)
    beacons = _bluetooth_beacons(req)
    if len(access_points) + len(beacons) < 2:
        return False

    payload = {
        "fallbacks": {"all": 0},
        "address": 0,
    }
    if access_points:
        payload["wifiAccessPoints"] = access_points
    if beacons:
        payload["bluetoothBeacons"] = beacons

    request = urllib.request.Request(
        "%s?%s" % (URL, urllib.parse.urlencode({"key": key})),
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )

    try:
        response = urllib.request.urlopen(request, timeout=5)
        ret = json.loads(response.read().decode("utf-8"))
        location = ret["location"]
        return {
            "position": {
                "type": _position_type(access_points, beacons),
                "latitude": location["lat"],
                "longitude": location["lng"],
                "accuracy": ret["accuracy"],
            },
            "service": "combain",
        }
    except (KeyError, OSError, ValueError):
        return False
