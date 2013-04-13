import json
import urllib
import os
import time
import struct
import math


def Distance(t1, t2):
    RADIUS = 6371000.  # earth's mean radius in km
    p1 = [0, 0]
    p2 = [0, 0]
    p1[0] = t1[0] * math.pi / 180.
    p1[1] = t1[1] * math.pi / 180.
    p2[0] = t2[0] * math.pi / 180.
    p2[1] = t2[1] * math.pi / 180.

    d_lat = (p2[0] - p1[0])
    d_lon = (p2[1] - p1[1])

    a = math.sin(d_lat / 2) * math.sin(d_lat / 2) + math.cos(
        p1[0]) * math.cos(p2[0]) * math.sin(d_lon / 2) * math.sin(d_lon / 2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    d = RADIUS * c
    return d

try:
    BADAPS = eval(open('data/black.lst').read())
except:
    BADAPS = {}


def wifi_ap_average(aps):
    """
    Converts aps [[lon, lat, ss, mac], ...] to (lon, lat, accuracy)
    """
    global BADAPS
    lbadaps = [ap for ap in aps if ap[3].replace(":", "").replace("-",
                                                                  "") in BADAPS]
    aps = [ap for ap in aps if ap[3].replace(":", "").replace("-", "")
           not in BADAPS]
    if not aps:
        return False
    if len(aps) == 1:
        return (aps[0][0], aps[0][1], 50.)
    meds = []
    for ap in aps:
        dst = [Distance((ap[1], ap[0]), (ap2[1], ap2[0])) for ap2 in aps]
        dst.sort()
        med = dst[len(dst) >> 1]
        ap.append(med)
        meds.append(med)
    meds.sort()
    med = max(meds[int(len(meds) * 0.6)], 50)
    lbadaps += [ap for ap in aps if ap[4] > med]
    for ap in lbadaps:
        BADAPS[ap[3]] = BADAPS.get(ap[3], 0) + 1
    if lbadaps:
        bap = dict([(k, v) for k, v in BADAPS.iteritems() if v > 15])
        a = open('data/black.lst', 'w')
        a.write(repr(bap))
        a.close()
    aps = [ap for ap in aps if ap[4] <= med]
    rlat = 0
    rlon = 0
    count = 0
    acc = 0
    for ap in aps:
        rlon += ap[2] * ap[0]
        rlat += ap[2] * ap[1]
        count += ap[2]
        acc += ap[2] * ap[4]
    return (rlon / count, rlat / count, max(10., acc / count))

mac2int = lambda mac: int(mac.replace(":", "").replace("-", ""), 16)

CACHE = {}

def readwifi(files=None, directory='data/'):
    if not files:
        files = [x for x in os.listdir(directory) if "bin" in x]
    ss = {}
    for fname in files:
        wf = open(directory + fname)
        ff = True
        while ff:
            ff = wf.read(16)
            if ff:
                (mac, lon, lat) = struct.unpack('!qff', ff)
                ss[mac] = (lon, lat)
    return ss

def get_location(req):
    global CACHE
    if not CACHE:
        CACHE = readwifi()
    if "wifi" not in req:
        return False
    aps = []
    for ap in req["wifi"]:
        mac = mac2int(ap["mac"])
        ss = max(100 + ap["ss"], 1)
        if mac in CACHE:
            aps.append([CACHE[mac][0], CACHE[mac][1], ss, ap[
                       "mac"].replace(":", "").replace("-", "")])

    if aps:
        res = wifi_ap_average(aps)
        if res:
            rlon, rlat, acc = res
            return {"position": {"latitude": rlat, "longitude": rlon, "accuracy": acc}}
    else:
        return False

if __name__ == "__main__":
    print get_location({"ip": "202.156.10.234", "wifi": [{"ss": 167.0, "mac": "f8:d1:11:32:56:cc", "ssid": "icemen"}, {"ss": 174.0, "mac": "b0:e7:54:be:fd:ae", "ssid": "BCNZR"}, {"ss": 181.0, "mac": "84:c9:b2:bb:b5:fe", "ssid": "Lee Family"}, {"ss": 182.0, "mac": "b0:e7:54:bd:a3:ca", "ssid": "SINGTEL-6998"}, {"ss": 188.0, "mac": "98:2c:be:ee:6d:62", "ssid": "SINGTEL-5150"}, {"ss": 186.0, "mac": "64:0f:28:4e:88:be", "ssid": "SINGTEL-0641"}, {"ss": 185.0, "mac": "50:46:5d:0b:36:c0", "ssid": "Heng Tiger"}, {"ss": 180.0, "mac": "3c:ea:4f:17:b5:59", "ssid": "SINGTEL-8721"}, {"ss": 182.0, "mac": "00:26:75:6a:82:77", "ssid": "chiongster"}, {"ss": 167.0, "mac": "68:7f:74:c3:0d:80", "ssid": ""}, {"ss": 180.0, "mac": "98:2c:be:df:7f:66", "ssid": "SINGTEL-8012"}, {"ss": 192.0, "mac": "98:2c:be:93:ff:12", "ssid": "SINGTEL-6770"}]})

    print get_location({"ip": "178.120.78.103", "wifi": [{"ss": 173.0, "mac": "70-72-3c-00-6c-d3", "ssid": "BELTELECOM WIFI"}, {"ss": 191.0, "mac": "c8-64-c7-3a-00-7c", "ssid": "dgin"}, {"ss": 186.0, "mac": "d0-15-4a-06-84-f7", "ssid": "BELTELECOM WIFI"}, {"ss": 175.0, "mac": "34-08-04-7f-d8-a9", "ssid": "DLink"}, {"ss": 180.0, "mac": "08-18-1a-ce-be-c2", "ssid": "ZTE"}, {"ss": 171.0, "mac": "00-25-12-ce-46-f2", "ssid": "wlan403"}, {"ss": 186.0, "mac": "d0-15-4a-06-84-f6", "ssid": "serebro"}, {"ss": 173.0, "mac": "70-72-3c-00-6c-d0", "ssid": "zoya"}, {"ss": 145.0, "mac": "00-1c-df-d6-d7-34", "ssid": "cats"}, {"ss": 152.0, "mac": "00-22-93-cc-66-6c", "ssid": "75369LL"}, {"ss": 164.0, "mac": "c8-64-c7-55-3c-4d", "ssid": "BELTELECOM WIFI"}, {"ss": 181.0, "mac": "08-18-1a-ce-be-c3", "ssid": "BELTELECOM WIFI"}]})
