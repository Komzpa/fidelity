import os
import math
import struct

import databases.offline.cache
import databases.offline.binary


def deg2num(lat_deg, lon_deg, zoom):
    lat_rad = math.radians(lat_deg)
    n = 2.0 ** zoom
    xtile = int((lon_deg + 180.0) / 360.0 * n)
    ytile = int((1.0 - math.log(
        math.tan(lat_rad) + (1 / math.cos(lat_rad))) / math.pi) / 2.0 * n)
    return (xtile, ytile)

directory = "data/"
files = [x for x in os.listdir(directory) if ("bin" in x and "tile" not in x)]
for fn in files:
    data = databases.offline.binary.readwifi([fn])
    tiles = {(0, 0, 0): [(mac, lon, lat) for mac, (lon, lat)
             in data.iteritems()]}
    while tiles:
        for k in tiles.keys():
            if len(tiles[k]) > 500000:
                print "bisecting ", k
                z = k[0] + 1
                for f in tiles[k]:
                    x, y = deg2num(f[2], f[1], z)
                    if (z, x, y) not in tiles:
                        tiles[(z, x, y)] = [f]
                    else:
                        tiles[(z, x, y)].append(f)
                del tiles[k]

            else:
                print "saving", k
                for l in tiles[k]:
                    databases.offline.cache.savewifi(
                        l, "data/%s.z%s.x%s.y%s.tile.bin" % (fn[:-4], k[0], k[1], k[2]))
                del tiles[k]
    print f, len(data), len(tiles)
