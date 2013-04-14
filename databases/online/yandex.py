import json
import urllib
from ..offline import cache

"""
req = {
ip: "127.0.0.1",
wifi: [{"ss": 167.0, "mac": "f8:d1:11:32:56:cc", "ssid": "icemen"}, ...],

}
"""

def get_location(req = {}):
    yar = {"common": {"version": "1.0", "api_key":
                      "AM68UVEBAAAAjygkZgIA_Wgbba5vOsfai2EAg41b0U2E44AAAAAAAAAAAACWTxLwE2AcETaBMml42EKlixR3dw=="}}
    if "wifi" not in req:
        return False
    if "ip" in req:
        yar["ip"] = {"address_v4": req["ip"]}
    if "wifi" in req:
        yar["wifi_networks"] = []
        for ap in req["wifi"]:
            yar["wifi_networks"].append(
                {"mac": ap["mac"].upper().replace(":", "-")})
    try:
        resp = urllib.urlopen("http://api.lbs.yandex.net/geolocation", urllib.urlencode({
                            "json": json.dumps(yar)})).read().strip()
        if '<error code="6">Location not found</error>' in resp:
            return False
        else:
            ret = json.loads(resp)
            lon, lat = ret["position"]["longitude"], ret["position"]["latitude"]
            if ret["position"]["type"] == "wifi":
                for ap in req:
                    cache.savewifi((ap["mac"], lon, lat), "data/yandex.cache.bin")
            ret["position"]["accuracy"] = ret["position"]["precision"]
            del ret["position"]["precision"]
            return ret
    except:
        return False

if __name__ == "__main__":
    print get_location({"ip": "202.156.10.234", "wifi": [{"ss": 167.0, "mac": "f8:d1:11:32:56:cc", "ssid": "icemen"}, {"ss": 174.0, "mac": "b0:e7:54:be:fd:ae", "ssid": "BCNZR"}, {"ss": 181.0, "mac": "84:c9:b2:bb:b5:fe", "ssid": "Lee Family"}, {"ss": 182.0, "mac": "b0:e7:54:bd:a3:ca", "ssid": "SINGTEL-6998"}, {"ss": 188.0, "mac": "98:2c:be:ee:6d:62", "ssid": "SINGTEL-5150"}, {"ss": 186.0, "mac": "64:0f:28:4e:88:be", "ssid": "SINGTEL-0641"}, {"ss": 185.0, "mac": "50:46:5d:0b:36:c0", "ssid": "Heng Tiger"}, {"ss": 180.0, "mac": "3c:ea:4f:17:b5:59", "ssid": "SINGTEL-8721"}, {"ss": 182.0, "mac": "00:26:75:6a:82:77", "ssid": "chiongster"}, {"ss": 167.0, "mac": "68:7f:74:c3:0d:80", "ssid": ""}, {"ss": 180.0, "mac": "98:2c:be:df:7f:66", "ssid": "SINGTEL-8012"}, {"ss": 192.0, "mac": "98:2c:be:93:ff:12", "ssid": "SINGTEL-6770"}]})

    print get_location({"ip": "178.120.78.103", "wifi": [{"ss": 173.0, "mac": "70-72-3c-00-6c-d3", "ssid": "BELTELECOM WIFI"}, {"ss": 191.0, "mac": "c8-64-c7-3a-00-7c", "ssid": "dgin"}, {"ss": 186.0, "mac": "d0-15-4a-06-84-f7", "ssid": "BELTELECOM WIFI"}, {"ss": 175.0, "mac": "34-08-04-7f-d8-a9", "ssid": "DLink"}, {"ss": 180.0, "mac": "08-18-1a-ce-be-c2", "ssid": "ZTE"}, {"ss": 171.0, "mac": "00-25-12-ce-46-f2", "ssid": "wlan403"}, {"ss": 186.0, "mac": "d0-15-4a-06-84-f6", "ssid": "serebro"}, {"ss": 173.0, "mac": "70-72-3c-00-6c-d0", "ssid": "zoya"}, {"ss": 145.0, "mac": "00-1c-df-d6-d7-34", "ssid": "cats"}, {"ss": 152.0, "mac": "00-22-93-cc-66-6c", "ssid": "75369LL"}, {"ss": 164.0, "mac": "c8-64-c7-55-3c-4d", "ssid": "BELTELECOM WIFI"}, {"ss": 181.0, "mac": "08-18-1a-ce-be-c3", "ssid": "BELTELECOM WIFI"}]})
