import re
import urllib
from ..offline import cache

def get_location(req = {}):
    if "ip" in req:
      return False
    resp = urllib.urlopen("http://google.com/jsapi").read()
    
    lat = float(re.search('"latitude":(.*?),', resp).groups()[0])
    lon = float(re.search('"longitude":(.*?),', resp).groups()[0])    
    return {"position":{"type":"ip", "latitude": lat, "longitude": lon, "accuracy": 130000.}}
