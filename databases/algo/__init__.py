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

def tile_number(lon_deg, lat_deg, zoom):
    n = 2.0 ** zoom
    xtile = int((lon_deg + 180.0) / 360.0 * n)
    ytile = int((lat_deg + 90.0) / 180.0 * n)
    return (xtile, ytile)