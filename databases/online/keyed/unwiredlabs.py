import json
import os
import urllib.request

URL = os.environ.get("FIDELITY_UNWIREDLABS_URL", "https://us1.unwiredlabs.com/v2/process.php")


def _api_token():
    return os.environ.get("FIDELITY_UNWIREDLABS_TOKEN") or os.environ.get("UNWIREDLABS_TOKEN")


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

        row = {"bssid": mac}
        signal_strength = _signal_strength(ap)
        if signal_strength is not None:
            row["signal"] = signal_strength
        rows.append(row)
    return rows[:15]


def _cells(req):
    rows = []
    radio = None
    mcc = None
    mnc = None
    for cell in req.get("cell", []):
        if not all(name in cell for name in ("mcc", "mnc", "lac", "cellid")):
            continue
        row = {
            "lac": cell["lac"],
            "cid": cell["cellid"],
        }
        if "signal" in cell:
            row["signal"] = int(cell["signal"])
        rows.append(row)
        if radio is None and cell.get("radio"):
            radio = cell["radio"].lower()
        if mcc is None:
            mcc = cell["mcc"]
        if mnc is None:
            mnc = cell["mnc"]
    return rows, radio, mcc, mnc


def _position_type(wifi, cells):
    if wifi:
        return "wifi"
    if cells:
        return "cell"
    return "ip"


def get_location(req=None):
    req = req or {}
    token = _api_token()
    if not token:
        return False

    wifi = _wifi(req)
    cells, radio, mcc, mnc = _cells(req)
    if not wifi and not cells:
        return False

    payload = {
        "token": token,
        "address": 0,
    }
    if wifi:
        payload["wifi"] = wifi
    if cells:
        payload["cells"] = cells
        if radio:
            payload["radio"] = radio
        if mcc is not None:
            payload["mcc"] = mcc
        if mnc is not None:
            payload["mnc"] = mnc
    request = urllib.request.Request(
        URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )

    try:
        response = urllib.request.urlopen(request, timeout=5)
        ret = json.loads(response.read().decode("utf-8"))
        if ret.get("status") != "ok":
            return False
        return {
            "position": {
                "type": _position_type(wifi, cells),
                "latitude": ret["lat"],
                "longitude": ret["lon"],
                "accuracy": ret["accuracy"],
            },
            "service": "unwiredlabs",
        }
    except (KeyError, OSError, ValueError):
        return False
