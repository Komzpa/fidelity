import binary
import redis

from ..algo.wifi_ap_average import wifi_ap_average
from .. import *

from cache import savewifi

def loadcache():
    r = redis.Redis(host='localhost', port=6379, db=0)
    for k,v in binary.readwifi().iteritems():
        r.set(mac2key(k), v)

def get_location(req):
    if "wifi" not in req:
        return False
    aps = []
    r = redis.Redis(host='localhost', port=6379, db=0)
    for ap in req["wifi"]:
        mac = mac2int(ap["mac"])
        ss = max(100 + ap["ss"], 1)
        apl = r.get(mac2key(mac))
        if apl:
            apl = eval(apl)
            aps.append([apl[0], apl[1], ss, ap["mac"].replace(":", "").replace("-", "")])
    if aps:
        res = wifi_ap_average(aps)
        if res:
            rlon, rlat, acc = res
            return {"position": {"latitude": rlat, "longitude": rlon, "accuracy": acc, "type": "wifi"}, "service": "redis cache"}
    else:
        return False

#loadcache()