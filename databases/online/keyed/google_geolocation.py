import json
import os
import urllib.parse
import urllib.request

URL = "https://www.googleapis.com/geolocation/v1/geolocate"


def _api_key():
    return os.environ.get("FIDELITY_GOOGLE_GEOLOCATION_API_KEY") or os.environ.get(
        "GOOGLE_GEOLOCATION_API_KEY"
    )


def _signal_strength(ap):
    if "ss" not in ap:
        return None
    return -abs(int(ap["ss"]))


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

        access_point = {"macAddress": mac}
        signal_strength = _signal_strength(ap)
        if signal_strength is not None:
            access_point["signalStrength"] = signal_strength
        access_points.append(access_point)
    return access_points


def _cell_towers(req):
    towers = []
    for cell in req.get("cell", []):
        if not all(name in cell for name in ("mcc", "mnc", "lac", "cellid")):
            continue
        tower = {
            "cellId": cell["cellid"],
            "locationAreaCode": cell["lac"],
            "mobileCountryCode": cell["mcc"],
            "mobileNetworkCode": cell["mnc"],
        }
        if cell.get("radio"):
            tower["radioType"] = cell["radio"].lower()
        if "signal" in cell:
            tower["signalStrength"] = int(cell["signal"])
        towers.append(tower)
    return towers


def _position_type(access_points, cell_towers):
    if access_points:
        return "wifi"
    if cell_towers:
        return "cell"
    return "ip"


def get_location(req=None):
    req = req or {}
    key = _api_key()
    if not key:
        return False

    payload = {"considerIp": False}
    access_points = _wifi_access_points(req)
    cell_towers = _cell_towers(req)
    if access_points:
        payload["wifiAccessPoints"] = access_points
    if cell_towers:
        payload["cellTowers"] = cell_towers
    if not access_points and not cell_towers and "ip" not in req:
        return False

    request = urllib.request.Request(
        "%s?%s" % (URL, urllib.parse.urlencode({"key": key})),
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )

    try:
        response = urllib.request.urlopen(request, timeout=5)
        ret = json.loads(response.read().decode("utf-8"))
        return {
            "position": {
                "type": _position_type(access_points, cell_towers),
                "latitude": ret["location"]["lat"],
                "longitude": ret["location"]["lng"],
                "accuracy": ret["accuracy"],
            },
            "service": "google geolocation",
        }
    except (KeyError, OSError, ValueError):
        return False
