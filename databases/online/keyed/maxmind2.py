import base64
import json
import os
import urllib.parse
import urllib.request

HOST = os.environ.get("FIDELITY_MAXMIND_HOST", "geolite.info")


def _credentials():
    account_id = os.environ.get("FIDELITY_MAXMIND_ACCOUNT_ID") or os.environ.get(
        "MAXMIND_ACCOUNT_ID"
    )
    license_key = os.environ.get("FIDELITY_MAXMIND_LICENSE_KEY") or os.environ.get(
        "MAXMIND_LICENSE_KEY"
    )
    if not account_id or not license_key:
        return None
    return account_id, license_key


def _authorization(credentials):
    token = "%s:%s" % credentials
    return "Basic %s" % base64.b64encode(token.encode("utf-8")).decode("ascii")


def get_location(req=None):
    req = req or {}
    credentials = _credentials()
    if not credentials:
        return False

    ip = urllib.parse.quote(req.get("ip", "me"), safe="")
    request = urllib.request.Request(
        "https://%s/geoip/v2.1/city/%s" % (HOST, ip),
        headers={"Authorization": _authorization(credentials)},
    )

    try:
        response = urllib.request.urlopen(request, timeout=5)
        ret = json.loads(response.read().decode("utf-8"))
        location = ret["location"]
        return {
            "position": {
                "type": "ip",
                "latitude": location["latitude"],
                "longitude": location["longitude"],
                "accuracy": location.get("accuracy_radius", 50000),
            },
            "service": "maxmind geolite2 city",
        }
    except (KeyError, OSError, ValueError):
        return False
