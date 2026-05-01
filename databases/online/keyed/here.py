import json
import os
import urllib.parse
import urllib.request

URL = "https://positioning.hereapi.com/v2/locate"


def _api_key():
    return os.environ.get("FIDELITY_HERE_API_KEY") or os.environ.get("HERE_API_KEY")


def _signal_strength(ap):
    if "ss" not in ap:
        return None
    return -abs(int(ap["ss"]))


def _wlan(req):
    rows = []
    seen = set()
    for ap in req.get("wifi", []):
        if not ap.get("ssid"):
            continue
        mac = ap["mac"].upper()
        if mac in seen:
            continue
        seen.add(mac)

        row = {"mac": mac}
        signal_strength = _signal_strength(ap)
        if signal_strength is not None:
            row["rss"] = signal_strength
        rows.append(row)
    return rows


def get_location(req=None):
    req = req or {}
    key = _api_key()
    if not key:
        return False

    wlan = _wlan(req)
    if len(wlan) < 2:
        return False

    request = urllib.request.Request(
        "%s?%s" % (URL, urllib.parse.urlencode({"apiKey": key})),
        data=json.dumps({"wlan": wlan}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )

    try:
        response = urllib.request.urlopen(request, timeout=5)
        ret = json.loads(response.read().decode("utf-8"))
        location = ret["location"]
        return {
            "position": {
                "type": "wifi",
                "latitude": location["lat"],
                "longitude": location["lng"],
                "accuracy": location["accuracy"],
            },
            "service": "here network positioning",
        }
    except (KeyError, OSError, ValueError):
        return False
