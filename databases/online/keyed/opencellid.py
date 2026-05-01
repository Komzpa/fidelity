import json
import os
import urllib.parse
import urllib.request

URL = "https://opencellid.org"


def _api_key():
    return os.environ.get("FIDELITY_OPENCELLID_API_KEY") or os.environ.get("OPENCELLID_API_KEY")


def _base_url():
    return os.environ.get("FIDELITY_OPENCELLID_URL", URL).rstrip("/")


def _first_cell(req):
    for cell in req.get("cell", []):
        if all(name in cell for name in ("mcc", "mnc", "lac", "cellid")):
            return cell
    return None


def get_location(req=None):
    req = req or {}
    key = _api_key()
    if not key:
        return False

    cell = _first_cell(req)
    if not cell:
        return False

    params = {
        "key": key,
        "mcc": cell["mcc"],
        "mnc": cell["mnc"],
        "lac": cell["lac"],
        "cellid": cell["cellid"],
        "format": "json",
    }
    if cell.get("radio"):
        params["radio"] = cell["radio"]

    try:
        response = urllib.request.urlopen(
            "%s/cell/get?%s" % (_base_url(), urllib.parse.urlencode(params)),
            timeout=5,
        )
        ret = json.loads(response.read().decode("utf-8"))
        return {
            "position": {
                "type": "cell",
                "latitude": ret["lat"],
                "longitude": ret["lon"],
                "accuracy": ret.get("range", 5000),
            },
            "service": "opencellid",
        }
    except (KeyError, OSError, ValueError):
        return False
