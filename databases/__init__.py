def mac2str(mac):
    mac = mac2int(mac)
    return hex(mac)[2:].upper().zfill(12)

def mac2key(mac):
    return "wifi:" + mac2str(mac)

def mac2int(mac):
    if type(mac) == int:
        return mac
    return int(mac.replace(":", "").replace("-", ""), 16)