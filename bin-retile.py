import os
import math
import struct
import json

import databases.offline.cache
import databases.offline.binary


def deg2num(lat_deg, lon_deg, zoom):
    n = 2.0 ** zoom
    xtile = int((lon_deg + 180.0) / 360.0 * n)
    ytile = int((lat_deg + 90.0) / 180.0 * n)
    return (xtile, ytile)

directory = "data/"
files = [x for x in os.listdir(directory) if ("bin" in x and "tile" not in x)]
jsindex = []
for fn in files:
    data = databases.offline.binary.readwifi([fn])
    tiles = {(0, 0, 0): [(mac, lon, lat) for mac, (lon, lat)
             in data.iteritems() if ((abs(lat)>0.0009 or abs(lon)>0.0009) and mac)]}
    while tiles:
        for k in tiles.keys():
            if len(tiles[k]) > 200000:
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
                bbox = [tiles[k][0][1],tiles[k][0][2],tiles[k][0][1],tiles[k][0][2]]
                minmac = tiles[k][0][0]
                maxmac = tiles[k][0][0]
                fname = "data/%s.z%s.x%s.y%s.tile.bin" % (fn[:-4], k[0], k[1], k[2])
                for l in tiles[k]:
                    bbox = [min(bbox[0], l[1]), min(bbox[1], l[2]), max(bbox[2], l[1]), max(bbox[3], l[2])]
                    minmac = min(minmac, l[0])
                    maxmac = max(maxmac, l[0])
                    databases.offline.cache.savewifi(l, fname)
                jsindex.append({"count": len(tiles[k]), "filename": fname, "bbox": bbox, "minmac": minmac, "maxmac": maxmac})
                del tiles[k]
    print f, len(data), len(tiles)

open("index.json", "w").write(json.dumps(jsindex))
