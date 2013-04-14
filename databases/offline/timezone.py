import json
import urllib
from ..offline import cache

def get_location(req = {}):
    if "ip" in req:
        return False
    try:
        timezone = open("/etc/timezone").read().strip()
        zones = [x.strip().split()[:3] for x in open("/usr/share/zoneinfo/zone.tab") if x.strip()[0]!="#"]
        coords = [x for x in zones if x[2]==timezone][0][1] + "        "
        # lat sign
        lat_sign = +1
        if coords[0] == "-":
                lat_sign = -1
        coords = coords[1:]
        # lat deg
        lat = float(coords[:2])
        coords = coords[2:]
        # lat mm
        if coords[0].isdigit():
            lat += float(coords[:2]) / 60
            coords = coords[2:]
            # lat ss
            if coords[0].isdigit():
                        lat += float(coords[:2]) / 3600
                        coords = coords[2:]
        lat = lat * lat_sign
        # lon sign
        lon_sign = +1
        if coords[0] == "-":
                lon_sign = -1
        coords = coords[1:]
        # lon deg
        lon = float(coords[:3])
        coords = coords[3:]
        # lon mm
        if coords[0].isdigit():
            lon += float(coords[:2]) / 60
            coords = coords[2:]
            # lon ss
            if coords[0].isdigit():
                        lon += float(coords[:2]) / 3600
                        coords = coords[2:]
        lon = lon * lon_sign

        return {"position":{"type":"timzone", "latitude": lat, "longitude": lon, "accuracy": 150000.}}
    except:
        return False
