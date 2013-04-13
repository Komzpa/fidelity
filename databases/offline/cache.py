import struct


def mac2int(mac):
    if type(mac) == int:
        return mac
    return int(mac.replace(":", "").replace("-", ""), 16)


def savewifi((mac, lon, lat), fname='data/our.bin'):
    open(fname, 'a+b').write(struct.pack('!qff', mac2int(mac), lon, lat))
