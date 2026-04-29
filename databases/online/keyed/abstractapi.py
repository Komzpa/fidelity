import json
import os
import urllib.parse
import urllib.request

URL = "https://ipgeolocation.abstractapi.com/v1/"


def _api_key():
    return os.environ.get("FIDELITY_ABSTRACT_API_KEY") or os.environ.get("ABSTRACT_API_KEY")


def get_location(req=None):
    req = req or {}
    key = _api_key()
    if not key:
        return False

    query = {"api_key": key}
    if req.get("ip"):
        query["ip_address"] = req["ip"]

    try:
        response = urllib.request.urlopen(
            "%s?%s" % (URL, urllib.parse.urlencode(query)),
            timeout=5,
        )
        ret = json.loads(response.read().decode("utf-8"))
        if ret.get("error"):
            return False
        return {
            "position": {
                "type": "ip",
                "latitude": ret["latitude"],
                "longitude": ret["longitude"],
                "accuracy": 50000,
            },
            "service": "abstract ip geolocation",
        }
    except (KeyError, OSError, ValueError):
        return False
