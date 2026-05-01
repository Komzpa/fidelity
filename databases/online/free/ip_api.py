import json
import urllib.parse
import urllib.request

URL = "http://ip-api.com/json"
FIELDS = "status,message,lat,lon"


def get_location(req=None):
    req = req or {}
    ip = req.get("ip")
    if ip:
        url = "%s/%s" % (URL, urllib.parse.quote(ip, safe=""))
    else:
        url = URL
    url = "%s?%s" % (url, urllib.parse.urlencode({"fields": FIELDS}))

    try:
        response = urllib.request.urlopen(url, timeout=5)
        ret = json.loads(response.read().decode("utf-8"))
        if ret.get("status") != "success":
            return False
        return {
            "position": {
                "type": "ip",
                "latitude": ret["lat"],
                "longitude": ret["lon"],
                "accuracy": 50000,
            },
            "service": "ip-api.com",
        }
    except (KeyError, OSError, ValueError):
        return False
