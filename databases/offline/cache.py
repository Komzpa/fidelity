import struct
from pathlib import Path

from .. import mac2int


def savewifi(ap, fname="data/our.bin"):
    mac, lon, lat = ap
    parent = Path(fname).parent
    if parent != Path("."):
        parent.mkdir(parents=True, exist_ok=True)
    if not isinstance(mac, int):
        mac = mac2int(mac)
    with open(fname, "ab") as cache_file:
        cache_file.write(struct.pack("!qff", mac, lon, lat))
