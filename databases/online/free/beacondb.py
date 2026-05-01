import json
import urllib.error
import urllib.request

USER_AGENT = "fidelity/0.1 (+https://github.com/Komzpa/fidelity)"
URL = "https://api.beacondb.net/v1/geolocate"


def _usable_mac(mac):
    normalized = mac.upper()
    return normalized not in ("00:00:00:00:00:00", "FF:FF:FF:FF:FF:FF")


def _signal_strength(ap):
    value = ap.get("ss", ap.get("rssi"))
    if value is None:
        return None
    return -abs(int(value))


def _wifi_access_points(req):
    access_points = []
    seen = set()
    for ap in req.get("wifi", []):
        ssid = ap.get("ssid")
        if not ssid or ssid.endswith("_nomap"):
            continue

        mac = ap["mac"].upper()
        if mac in seen:
            continue
        seen.add(mac)

        access_point = {"macAddress": mac}
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


def get_location(req):
    access_points = _wifi_access_points(req)
    beacons = _bluetooth_beacons(req)
    if len(access_points) + len(beacons) < 2:
        return False

    payload = {
        "considerIp": False,
        "fallbacks": {"ipf": False, "lacf": False},
    }
    if access_points:
        payload["wifiAccessPoints"] = access_points
    if beacons:
        payload["bluetoothBeacons"] = beacons

    request = urllib.request.Request(
        URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        },
    )

    try:
        response = urllib.request.urlopen(request, timeout=5)
        ret = json.loads(response.read().decode("utf-8"))
        return {
            "position": {
                "type": _position_type(access_points, beacons),
                "latitude": ret["location"]["lat"],
                "longitude": ret["location"]["lng"],
                "accuracy": ret["accuracy"],
            },
            "service": "beaconDB",
        }
    except (KeyError, OSError, urllib.error.HTTPError, ValueError):
        return False
