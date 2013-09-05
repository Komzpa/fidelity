import math

def mac2str(mac):
    mac = mac2int(mac)
    return ("%012x" % mac).upper()

def mac2key(mac):
    return "wifi:" + mac2str(mac)

def mac2int(mac):
    if type(mac) == int:
        return mac
    return int(mac.replace(":", "").replace("-", ""), 16)

def ip2int(ip):
    if type(ip) == int:
        return ip
    ip = [int(i) for i in ip.split(".")]
    return (ip[0] << 8*3) + (ip[1] << 8*2) + (ip[2] << 8) + ip[3]

def ipcutoff(ip, bits):
    ip = ip2int(ip)
    return ip & ~ ((1<<(bits+1))-1)

def ip2key(ip, bits = 0):
    ipn = ipcutoff(ip, bits)
   # print ip, "ip:%08x:%02x"%(ipn, bits)
    return "ip:%08x:%02x"%(ipn, bits)

def distance(origin, destination):
    lon1, lat1 = origin["position"]["longitude"], origin["position"]["latitude"]
    lon2, lat2 = destination["position"]["longitude"], destination["position"]["latitude"]
    radius = 6371000

    dlat = math.radians(lat2-lat1)
    dlon = math.radians(lon2-lon1)
    a = math.sin(dlat/2) * math.sin(dlat/2) + math.cos(math.radians(lat1)) \
        * math.cos(math.radians(lat2)) * math.sin(dlon/2) * math.sin(dlon/2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    d = radius * c

    return d