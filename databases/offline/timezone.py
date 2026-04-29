import os


def _local_timezone_name():
    try:
        with open("/etc/timezone") as timezone_file:
            timezone = timezone_file.read().strip()
            if timezone:
                return timezone
    except OSError:
        pass

    try:
        localtime = os.path.realpath("/etc/localtime")
    except OSError:
        return None

    marker = "/zoneinfo/"
    if marker not in localtime:
        return None
    return localtime.split(marker, 1)[1]


def _parse_zone_coordinate(coords):
    sign = -1 if coords[0] == "-" else 1
    coords = coords[1:]
    degrees_len = 2 if len(coords) in (4, 6) else 3
    value = float(coords[:degrees_len])
    coords = coords[degrees_len:]
    if len(coords) >= 2:
        value += float(coords[:2]) / 60
        coords = coords[2:]
    if len(coords) >= 2:
        value += float(coords[:2]) / 3600
    return value * sign


def _parse_zone_tab_coords(coords):
    split_at = max(coords.rfind("+"), coords.rfind("-"))
    if split_at <= 0:
        raise ValueError("zone.tab coordinate must contain latitude and longitude")
    return _parse_zone_coordinate(coords[:split_at]), _parse_zone_coordinate(coords[split_at:])


def get_location(req={}):
    if "ip" in req:
        return False
    try:
        timezone = _local_timezone_name()
        if not timezone:
            return False
        with open("/usr/share/zoneinfo/zone.tab") as zones_file:
            zones = [x.strip().split()[:3] for x in zones_file if x.strip() and x.strip()[0] != "#"]
        coords = [x for x in zones if x[2] == timezone][0][1]
        lat, lon = _parse_zone_tab_coords(coords)
        return {
            "position": {
                "type": "timezone",
                "latitude": lat,
                "longitude": lon,
                "accuracy": 150000.0,
            },
            "service": "timezone",
        }
    except (IndexError, OSError, ValueError):
        return False
