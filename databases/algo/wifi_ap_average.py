import math
from __init__ import *

def wifi_ap_average(aps):
    """
    Converts aps [[pos, ss], ...] to (lon, lat, accuracy)
    """

    if len(aps) == 1:
        return aps[0]
    # median filtering
    meds = []
    for ap in aps:
        dst = [Distance((ap["longitude"], ap["latitude"]), (ap2["longitude"], ap2["latitude"])) for ap2 in aps]
        dst.sort()
        med = dst[len(dst) >> 1]
        ap["med"] = med
        meds.append(med)
    meds.sort()
    med = min(max(meds[int(len(meds) * 0.6)], 50), 500)
    aps = [ap for ap in aps if ap["med"] <= med]

    if not aps:
        return False

    count = 0
    result = {"latitude":0, "longitude":0, "accuracy":0, "altitude":0}
    for ap in aps:
        for k in result.keys():
            result[k] += ap["ss"] * ap[k]
        count += ap["ss"]
    for k in result.keys():
        result[k] /= count
    return result