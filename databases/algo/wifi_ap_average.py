import math

from . import Distance


def signal_distance(signal, lag_s=0):
    """
    Estimate AP observation radius from RSSI and observation lag.

    This is the portable part of the historical PostGIS `tpv_from_wifi` query:
    `10 * lag_s + 0.2347 * exp(-0.07877 * rssi_dbm)`. It came from a small local
    regression and is used here as an uncertainty radius, not as a hard distance.
    """
    if signal is None:
        return None
    try:
        signal = float(signal)
        lag_s = max(float(lag_s or 0), 0.0)
    except (TypeError, ValueError):
        return None
    if signal > 0:
        signal = -abs(signal)
    return (10.0 * lag_s) + (0.2347 * math.exp(-0.07877 * signal))


def _signal_weight(signal, fallback=1):
    if signal is None:
        return max(float(fallback or 1), 1.0)
    try:
        signal = float(signal)
    except (TypeError, ValueError):
        return max(float(fallback or 1), 1.0)
    if signal <= 0:
        return max(100.0 + signal, 1.0)
    return max(signal, 1.0)


def _lag_seconds(ap):
    if ap.get("lag_s") is not None:
        try:
            return float(ap["lag_s"])
        except (TypeError, ValueError):
            return 0.0
    if ap.get("age") is not None:
        try:
            return float(ap["age"]) / 1000.0
        except (TypeError, ValueError):
            return 0.0
    return 0.0


def _normalise_ap(ap):
    if isinstance(ap, dict):
        signal = ap.get("signal", ap.get("rssi", ap.get("ss")))
        lag_s = _lag_seconds(ap)
        radius = signal_distance(signal, lag_s=lag_s)
        if radius is None:
            radius = ap.get("accuracy", 0)
        explicit_radius = ap.get("radius", 0)
        return {
            "longitude": ap["longitude"],
            "latitude": ap["latitude"],
            "accuracy": ap.get("accuracy", 0),
            "altitude": ap.get("altitude", 0),
            "ss": _signal_weight(signal, fallback=ap.get("ss", 1)),
            "signal": signal,
            "lag_s": lag_s,
            "radius": max(
                float(ap.get("accuracy", 0) or 0),
                float(radius or 0),
                float(explicit_radius or 0),
            ),
        }

    lon, lat, ss = ap[:3]
    try:
        signal = float(ss)
    except (TypeError, ValueError):
        signal = None
    return {
        "longitude": lon,
        "latitude": lat,
        "accuracy": 0,
        "altitude": 0,
        "ss": _signal_weight(ss),
        "signal": signal if signal is not None and signal <= 0 else None,
        "lag_s": 0,
        "radius": signal_distance(ss) or 0,
    }


def _normalise_prior(prior):
    if not prior:
        return None
    if "position" in prior:
        prior = prior["position"]
    if "latitude" not in prior or "longitude" not in prior:
        return None
    accuracy = float(prior.get("accuracy", 100) or 100)
    return {
        "latitude": prior["latitude"],
        "longitude": prior["longitude"],
        "altitude": prior.get("altitude", 0),
        "accuracy": accuracy,
        "radius": accuracy,
        "ss": max(1.0, 10000.0 / max(accuracy * accuracy, 1.0)),
        "signal": None,
        "lag_s": 0,
        "prior": True,
    }


def _median(values):
    values = sorted(values)
    if not values:
        return 0
    middle = len(values) // 2
    if len(values) % 2:
        return values[middle]
    return (values[middle - 1] + values[middle]) / 2.0


def _project(points):
    origin_lon = sum(point["longitude"] for point in points) / len(points)
    origin_lat = sum(point["latitude"] for point in points) / len(points)
    scale_x = max(111320.0 * math.cos(math.radians(origin_lat)), 1.0)
    scale_y = 110540.0

    projected = []
    for point in points:
        projected.append(
            {
                "x": (point["longitude"] - origin_lon) * scale_x,
                "y": (point["latitude"] - origin_lat) * scale_y,
                "point": point,
            }
        )
    return origin_lon, origin_lat, scale_x, scale_y, projected


def _weighted_geometric_median(points):
    if len(points) == 1:
        return points[0]["longitude"], points[0]["latitude"]

    origin_lon, origin_lat, scale_x, scale_y, projected = _project(points)
    total_weight = sum(point["point"]["ss"] for point in projected)
    x = sum(point["x"] * point["point"]["ss"] for point in projected) / total_weight
    y = sum(point["y"] * point["point"]["ss"] for point in projected) / total_weight

    for _ in range(32):
        numerator_x = 0.0
        numerator_y = 0.0
        denominator = 0.0
        for item in projected:
            distance = math.hypot(x - item["x"], y - item["y"])
            if distance < 0.001:
                continue
            weight = item["point"]["ss"] / distance
            numerator_x += item["x"] * weight
            numerator_y += item["y"] * weight
            denominator += weight
        if not denominator:
            break
        next_x = numerator_x / denominator
        next_y = numerator_y / denominator
        if math.hypot(next_x - x, next_y - y) < 0.01:
            x = next_x
            y = next_y
            break
        x = next_x
        y = next_y

    return origin_lon + (x / scale_x), origin_lat + (y / scale_y)


def wifi_ap_average(aps, prior=None):
    """
    Convert access-point observations to a weighted location estimate.
    """
    aps = [_normalise_ap(ap) for ap in aps]

    if len(aps) == 1:
        ap = aps[0]
        return {
            "latitude": ap["latitude"],
            "longitude": ap["longitude"],
            "accuracy": ap["accuracy"],
            "altitude": ap["altitude"],
        }

    # median filtering
    meds = []
    for ap in aps:
        dst = [
            Distance((ap["longitude"], ap["latitude"]), (ap2["longitude"], ap2["latitude"]))
            for ap2 in aps
        ]
        dst.sort()
        med = dst[len(dst) >> 1]
        ap["med"] = med
        meds.append(med)
    meds.sort()
    med = min(max(meds[int(len(meds) * 0.6)], 50), 500)
    aps = [ap for ap in aps if ap["med"] <= med]

    if not aps:
        return False

    prior = _normalise_prior(prior)
    solver_points = list(aps)
    if prior:
        solver_points.append(prior)

    longitude, latitude = _weighted_geometric_median(solver_points)
    weighted_altitude = 0.0
    total_weight = 0.0
    for point in solver_points:
        weighted_altitude += point["altitude"] * point["ss"]
        total_weight += point["ss"]

    residuals = [
        Distance((longitude, latitude), (ap["longitude"], ap["latitude"])) + ap["radius"]
        for ap in aps
    ]
    result = {
        "latitude": latitude,
        "longitude": longitude,
        "accuracy": max(_median(residuals), prior["accuracy"] if prior else 0),
        "altitude": weighted_altitude / total_weight,
    }
    return result
