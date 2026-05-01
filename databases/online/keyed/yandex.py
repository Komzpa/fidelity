import json
import os
import urllib.parse
import urllib.request

URL = "https://locator.api.maps.yandex.ru/v1/locate"


def _api_key():
    return os.environ.get("FIDELITY_YANDEX_LOCATOR_API_KEY") or os.environ.get(
        "YANDEX_LOCATOR_API_KEY"
    )


def _signal_strength(ap):
    if "ss" not in ap:
        return None
    return -abs(int(ap["ss"]))


def _wifi(req):
    rows = []
    seen = set()
    for ap in req.get("wifi", []):
        if not ap.get("ssid"):
            continue
        mac = ap["mac"].upper()
        if mac in seen:
            continue
        seen.add(mac)

        row = {"bssid": mac, "age": 0}
        signal_strength = _signal_strength(ap)
        if signal_strength is not None:
            row["signal_strength"] = signal_strength
        rows.append(row)
    return rows


def _cell(req):
    rows = []
    for cell in req.get("cell", []):
        if "signal" not in cell:
            continue
        if not all(name in cell for name in ("mcc", "mnc", "lac", "cellid")):
            continue

        radio = cell.get("radio", "GSM").lower()
        payload = {
            "mcc": cell["mcc"],
            "mnc": cell["mnc"],
            "signal_strength": int(cell["signal"]),
        }
        if radio == "lte":
            payload["tac"] = cell.get("tac", cell["lac"])
            payload["ci"] = cell["cellid"]
            rows.append({"lte": payload})
        elif radio in ("umts", "wcdma"):
            payload["lac"] = cell["lac"]
            payload["cid"] = cell["cellid"]
            rows.append({"wcdma": payload})
        elif radio == "gsm":
            payload["lac"] = cell["lac"]
            payload["cid"] = cell["cellid"]
            rows.append({"gsm": payload})
    return rows


def _position_type(wifi, cells):
    if wifi:
        return "wifi"
    if cells:
        return "cell"
    return "ip"


def get_location(req=None):
    req = req or {}
    key = _api_key()
    if not key:
        return False

    payload = {}
    wifi = _wifi(req)
    cells = _cell(req)
    if wifi:
        payload["wifi"] = wifi
    if cells:
        payload["cell"] = cells
    if "ip" in req:
        payload["ip"] = [{"address": req["ip"]}]
    if not payload:
        return False

    request = urllib.request.Request(
        "%s?%s" % (URL, urllib.parse.urlencode({"apikey": key})),
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "User-Agent": "fidelity/0.1 (+https://github.com/Komzpa/fidelity)",
        },
    )

    try:
        response = urllib.request.urlopen(request, timeout=5)
        ret = json.loads(response.read().decode("utf-8"))
        location = ret["location"]
        return {
            "position": {
                "type": _position_type(wifi, cells),
                "latitude": location["point"]["lat"],
                "longitude": location["point"]["lon"],
                "accuracy": location["accuracy"],
            },
            "service": "yandex locator",
        }
    except (KeyError, OSError, ValueError):
        return False
