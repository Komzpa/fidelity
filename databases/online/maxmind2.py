import json
import urllib
from ..offline import cache

def get_location(req = {}):
    ip = req.get("ip", "me")
    try:
        resp = json.loads(urllib.urlopen("http://www.maxmind.com/geoip/city_isp_org/%s?demo=1"%ip).read())
        acc = 140000
        if (resp["location"]["latitude"]) != int(resp["location"]["latitude"]):
            acc /= 2
        if (resp["location"]["longitude"]) != int(resp["location"]["longitude"]):
            acc /= 2
        return {"position":{"type":"ip", "latitude": resp["location"]["latitude"], "longitude": resp["location"]["longitude"], "accuracy": acc}, "service": "maxmind geoip demo"}
    except:
        return False
