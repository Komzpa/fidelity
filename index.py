#!/usr/bin/python
# -*- coding: utf-8 -*-
import GeoIP
import json
import os
import web
import sys
import urllib
import urllib2
import re
import time
import random

import struct

reload(sys)
sys.setdefaultencoding("utf-8")          # a hack to support UTF-8

from lxml import etree

web.config.debug = False

GeoIpCache = GeoIP.open('GeoLiteCity.dat', GeoIP.GEOIP_MEMORY_CACHE)

LOCALES = ['be', 'ru', 'en', 'none']

OK = 200
ERROR = 500


def handler(self, req_type):
    """
      A handler for web.py.
    """

    data = web.input(wifi=[])
    # resp, ctype, content = face_main(data)
    wifiaps = readwifi()
    content_type = "text/json"
    locstruct = {
        "location": {"latitude": 53.9, "longitude": 27.5670, "altitude": 30.1, "accuracy": 4000.4, "altitude_accuracy": 100, "address": {"street_number": "NA", "street": "NA", "postal_code": "NA", "city": "NA",
        "county": "NA", "region": "NA", "country": "NA", "country_code": "NA"}}, "access_token": "42"}
    locale = data.get("locale", "be")
    userip = os.environ["REMOTE_ADDR"]
    if locale not in ('en', 'ru', 'be'):
        locale = "be"
    else:
        user_geoip = GeoIpCache.record_by_addr(userip)            # try GeoIP
        try:
            locstruct["location"]["latitude"] = user_geoip["latitude"]
            locstruct["location"]["longitude"] = user_geoip["longitude"]
            if user_geoip["city"]:
                locstruct["location"]["address"]["city"] = user_geoip["city"]
            if user_geoip["country_name"]:
                locstruct["location"]["address"][
                    "country"] = user_geoip["country_name"]
            if user_geoip["country_code"]:
                locstruct["location"]["address"][
                    "country_code"] = user_geoip["country_code"]
            if user_geoip["region_name"]:
                locstruct["location"]["address"][
                    "region"] = user_geoip["region_name"]
            if user_geoip["postal_code"]:
                locstruct["location"]["address"][
                    "postal_code"] = user_geoip["postal_code"]
        except TypeError:
            pass

        wifi = []
        if data["wifi"]:
            wifi = [dict(
                [j.split(":") for j in i.split("|")]) for i in data["wifi"]]
        if req_type == "POST":
            towers = json.loads(web.data())["wifi_towers"]
            for tower in towers:
                wifi.append({"mac": tower["mac_address"], 'ss': tower[
                            'signal_strength'], 'ssid': tower['ssid']})
        locstruct["dbg"] = wifi[:]
        coord = (0, 0)
        ss = 0
        cnt = 0
        for ap in wifi:
            # locstruct["dbg"] = ap
            # if type(ap) :
            if "mac" in ap:
                mac = int(ap["mac"].replace(":", "").replace("-", ""), 16)
                if float(ap['ss']) < 0:
                    ap['ss'] = abs(100 - float(ap['ss']))
                # locstruct["dbg"].append(mac)
                if mac in wifiaps:
                    locstruct["dbg"].append(wifiaps[mac])
                    coord = (coord[0] + float(ap['ss']) * wifiaps[mac]
                             [0], coord[1] + float(ap['ss']) * wifiaps[mac][1])
                    ss += float(ap['ss'])
                    cnt += 1
                    # pass
                else:
                    # get from openwlanmap online
                    macstring = hex(mac)[2:-1].zfill(12)
                    # locstruct["dbg"].append([macstring])
                    try:
                        bbox = re.search(r'bbox=\n(.*?),(.*?),(.*?),(.*?)&#38;layer', urllib.urlopen(
                            'http://www.openwlanmap.org/findmac.php?lang=en&bssid=' + macstring + '&step=1').read(), re.MULTILINE).groups()
                        lon = (float(bbox[0]) + float(bbox[2])) / 2
                        lat = (float(bbox[1]) + float(bbox[3])) / 2
                        savewifi((mac, lon, lat))
                        locstruct["dbg"].append([macstring, lon, lat])
                        coord = (coord[0] + lon, coord[1] + lat)
                        ss += float(ap['ss'])
                        cnt += 1
                    except AttributeError:
                        pass
                if ss:
                    locstruct['location']['latitude'] = coord[1] / ss
                    locstruct['location']['longitude'] = coord[0] / ss
                    locstruct['location'][
                        'accuracy'] = float(int(20. * 100 * cnt / ss))
    del locstruct["dbg"]
    # firefox compatibility
    locstruct['location']['lat'] = locstruct['location']['latitude']
    locstruct['location']['lng'] = locstruct['location']['longitude']
    locstruct['accuracy'] = locstruct['location']['accuracy']
    locstruct["status"] = "OK"

    content = json.dumps(locstruct)
    log_entry = {}
    log_entry["sent"] = content
    log_entry["ip"] = os.environ["REMOTE_ADDR"]
    log_entry["ua"] = os.environ.get("HTTP_USER_AGENT")
    log_entry["wifi"] = wifi
    log_entry["time"] = time.time()
    open("/tmp/wirelesslog.txt", "a").write(json.dumps(log_entry) + "\n")
    web.header('Content-type', content_type)
    return content

urls = (
    '/(.*)', 'mainhandler'
)


class mainhandler:
    def GET(self, crap):
        return handler(self, "GET")

    def POST(self, crap):
        return handler(self, "POST")


def readwifi(files=['our.bin', 'openwlanmap.bin', 'openbmap.bin']):
    ss = {}
    for fname in files:
        wf = open(fname)
        ff = True
        while ff:
            ff = wf.read(24)
            if ff:
                (mac, lon, lat) = struct.unpack('!qdd', ff)
                ss[mac] = (lon, lat)
    return ss


def savewifi((mac, lon, lat), fname='our.bin'):
    open('our.bin', 'a+b').write(struct.pack('!qdd', mac, lon, lat))

if __name__ == "__main__":
    app = web.application(urls, globals())
    app.run(
    )                                                    # standalone run


application = web.application(urls, globals()).wsgifunc()        # mod_wsgi
