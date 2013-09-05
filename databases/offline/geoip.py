import json
import urllib

import GeoIP
geoip = GeoIP.open('data/GeoLiteCity.dat', GeoIP.GEOIP_MEMORY_CACHE)

def get_location(req = {}):
    if "ip" in req:
        try:
            user_geoip = geoip.record_by_addr(req["ip"])
            acc = 400000
            if user_geoip["country_code"] == "RU":
                acc = 4300000
            if user_geoip["region"]:
                acc = 150000
            if user_geoip["city"]:
                acc = 30000
            return {"position":{"type":"ip", "latitude": user_geoip["latitude"], "longitude": user_geoip["longitude"], "accuracy": acc}, "service": "geoip offline"}
        except:
            return False
