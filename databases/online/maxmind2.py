import json
import urllib
from ..offline import cache

def get_location(req = {}):
    ip = req.get("ip", "me")
    try:
        resp = json.loads(urllib.urlopen("http://www.maxmind.com/geoip/city_isp_org/%s?demo=1"%ip).read())
        return {"position":{"type":"ip", "latitude": resp["location"]["latitude"], "longitude": resp["location"]["longitude"], "accuracy": 130000.}}
    except:
        return False
