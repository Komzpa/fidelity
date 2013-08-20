import struct

from .. import *

def savewifi((mac, lon, lat), fname='data/our.bin'):
    open(fname, 'a+b').write(struct.pack('!qff', mac2int(mac), lon, lat))
