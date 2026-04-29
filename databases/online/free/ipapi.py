import json
import urllib.parse
import urllib.request


def get_location(req=None):
    req = req or {}
    ip = req.get("ip")
    if ip:
        url = "https://ipapi.co/%s/json/" % urllib.parse.quote(ip, safe="")
    else:
        url = "https://ipapi.co/json/"

    try:
        response = urllib.request.urlopen(url, timeout=5)
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
            "service": "ipapi.co",
        }
    except (KeyError, OSError, ValueError):
        return False
