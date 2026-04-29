from . import binary
from . import geoip
import ast
import struct

from .. import distance, ip2key, mac2int, mac2key
from ..algo.wifi_ap_average import wifi_ap_average
from ..algo.weighted_storage_cube import shelf

try:
    import redis

    RedisError = redis.RedisError
    r = redis.Redis(host="localhost", port=6379, db=0)
except ImportError:
    RedisError = Exception
    r = False


def pack_ll(ll):
    return struct.pack("=dd", ll[0], ll[1])


def unpack_ll(ll):
    try:
        return struct.unpack("=dd", ll)
    except struct.error:
        if isinstance(ll, bytes):
            ll = ll.decode("ascii")
        if ll[0] == "(":
            return ast.literal_eval(ll)
    return None


def loadcache():
    global r
    if not r:
        return
    try:
        pipe = r.pipeline()
        cnt = 0

        for k, v in binary.readwifi():
            cnt += 1
            pipe.set(mac2key(k), pack_ll(v))
            if cnt == 100000:
                cnt = 0
                pipe.execute()
        pipe.execute()
    except RedisError:
        return


def get_location(req):
    global r
    if not r:
        return False

    if "wifi" in req:
        aps = []
        for ap in req["wifi"]:
            mac = mac2int(ap["mac"])
            signal = ap.get("ss")
            signal = float(signal) if signal is not None else None
            ss = max(100 + signal, 1) if signal is not None and signal <= 0 else max(signal or 1, 1)
            try:
                apl = r.get(mac2key(mac))
            except RedisError:
                return False

            if apl:
                s = shelf()
                s.loads(apl)
                s = s.get_average()
                if s:
                    s["ss"] = ss
                    s["signal"] = signal
                    s["age"] = ap.get("age")
                    aps.append(s)
        if aps:
            res = wifi_ap_average(aps, prior=req.get("gps"))
            if res:
                res["type"] = "wifi"
                return {"position": res, "service": "redis cache"}
    if "ip" in req:
        refloc = geoip.get_location(req)
        for bit in range(4, 15):
            try:
                ipl = r.get(ip2key(req["ip"], bit))
            except RedisError:
                return refloc or False
            if ipl:
                s = shelf()
                s.loads(ipl)
                t = s.get_average()
                if not t:
                    continue
                t["type"] = "ip"
                t["accuracy"] = max(500.1, t["accuracy"], 14.0 * (bit**2.7))
                loc = {"position": t, "service": "redis cache"}
                if refloc:
                    if (bit > 8) and (
                        distance(loc, refloc) > max(refloc["position"]["accuracy"], 200000)
                    ):
                        continue
                    if refloc["position"]["accuracy"] < loc["position"]["accuracy"]:
                        continue
                return loc
        return refloc


def saveip(ip, pos, force=True):
    global r
    if not r:
        return
    try:
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
    except RedisError:
        return


def savewifi(mac, pos, force=True):
    if not r:
        return False
    try:
        ap = r.get(mac2key(mac))
        if force or not ap:
            s = shelf()
            if ap:
                s.loads(ap)
            s.add_point(pos)
            r.set(mac2key(mac), s.dumps())
        return ap
    except RedisError:
        return False


def dropwifi(mac):
    if r:
        try:
            r.delete(mac2key(mac))
        except RedisError:
            return


try:
    if r:
        loaded = r.get("fidelity:loaded")
        if not loaded:
            # loadcache()
            r.set("fidelity:loaded", "yes")
        # r.expire('fidelity:loaded', 86400)
except RedisError:
    pass
