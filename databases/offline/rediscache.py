import binary
import redis

import geoip
import struct

from ..algo.wifi_ap_average import wifi_ap_average
from ..algo.weighted_storage_cube import shelf
from .. import *

try:
    r = redis.Redis(host='localhost', port=6379, db=0)
except:
    r = False

def pack_ll(ll):
    return struct.pack("!dd", ll[0], ll[1])

def unpack_ll(ll):
    try:
        return struct.unpack("!dd", ll)
    except struct.error:
        if ll[0] == "(":
            return eval(ll)


def loadcache():
    global r
    pipe = r.pipeline()
    cnt = 0

    for k,v in binary.readwifi():
        cnt += 1
        pipe.set(mac2key(k), pack_ll(v))
        if cnt == 100000:
            cnt = 0
            pipe.execute()
    pipe.execute()

def get_location(req):
    global r

    if "wifi" in req:
        aps = []
        for ap in req["wifi"]:
            mac = mac2int(ap["mac"])
            ss = max(100 + float(ap["ss"]), 1)
            apl = r.get(mac2key(mac))
            if apl:
                s = shelf()
                s.loads(apl)
                s = s.get_average()
                if s:
                    s["ss"] = ss
                    aps.append(s)
        if aps:
            res = wifi_ap_average(aps)
            if res:
                res["type"] = "wifi"
                return {"position": res, "service": "redis cache"}
    if "ip" in req:
        refloc = geoip.get_location(req)
        for bit in range(4, 16):
            ipl = r.get(ip2key(req["ip"], bit))
            if ipl:
                s = shelf()
                s.loads(ipl)
                t = s.get_average()
                if not t:
                    continue
                t["type"] = "ip"
                t["accuracy"] = max(500.1, t["accuracy"], 14.*(bit**2.7))
                loc = {"position": t, "service": "redis cache"}
                if refloc:
                    if (bit > 8) and (distance(loc, refloc) > max(refloc["position"]["accuracy"], 200000)):
                        continue
                    if refloc["position"]["accuracy"] < loc["position"]["accuracy"]:
                        continue
                return loc
        return refloc

def saveip(ip, pos, force = True):
    global r
    for bit in range(4, 16):
        key = ip2key(ip, bit)
        s = shelf()
        shl = r.get(key)
        if shl:
            s.loads(shl)
            if not force:
                break
        s.add_point(pos)
        r.set(key, s.dumps())

def savewifi(mac, pos, force = True):
    ap = r.get(mac2key(mac))
    if force or not ap:
        s = shelf()
        if ap:
            s.loads(ap)
        s.add_point(pos)
        r.set(mac2key(mac), s.dumps())
    return ap

def dropwifi(mac):
    r.delete(mac2key(mac))

try:
    loaded = r.get('fidelity:loaded')
    if not loaded:
        loadcache()
        r.set('fidelity:loaded', 'yes')
      # r.expire('fidelity:loaded', 86400)
except:
    pass