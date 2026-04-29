import os
import struct

from ..algo.wifi_ap_average import wifi_ap_average


def mac2int(mac):
    return int(mac.replace(":", "").replace("-", ""), 16)


CACHE = {}


def readwifi(files=None, directory="data/"):
    if not os.path.isdir(directory):
        return
    if not files:
        files = [x for x in os.listdir(directory) if "bin" in x]
    for fname in files:
        with open(os.path.join(directory, fname), "rb") as wf:
            while ff := wf.read(16):
                mac, lon, lat = struct.unpack("!qff", ff)
                yield (mac, (lon, lat))


def get_location(req):
    global CACHE
    if not req.get("wifi"):
        return False
    if not CACHE:
        CACHE = dict(readwifi())
    aps = []
    for ap in req["wifi"]:
        mac = mac2int(ap["mac"])
        signal = ap.get("ss")
        signal = float(signal) if signal is not None else None
        ss = max(100 + signal, 1) if signal is not None and signal <= 0 else max(signal or 1, 1)
        if mac in CACHE:
            aps.append(
                {
                    "longitude": CACHE[mac][0],
                    "latitude": CACHE[mac][1],
                    "accuracy": 0,
                    "altitude": 0,
                    "ss": ss,
                    "signal": signal,
                    "age": ap.get("age"),
                }
            )

    if aps:
        res = wifi_ap_average(aps, prior=req.get("gps"))
        if res:
            res["type"] = "wifi"
            return {"position": res, "service": "binary offline cache"}
    return False
