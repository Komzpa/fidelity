import math
from __init__ import *

try:
    BADAPS = eval(open('data/black.lst').read())
except:
    BADAPS = {}

def wifi_ap_average(aps):
    """
    Converts aps [[lon, lat, ss, mac], ...] to (lon, lat, accuracy)
    """
    global BADAPS
    lbadaps = [ap for ap in aps if ap[3].replace(":", "").replace("-",
                                                                  "") in BADAPS]
    aps = [ap for ap in aps if ap[3].replace(":", "").replace("-", "")
           not in BADAPS]
    if not aps:
        return False
    if len(aps) == 1:
        return (aps[0][0], aps[0][1], 50.)
    meds = []
    for ap in aps:
        dst = [Distance((ap[1], ap[0]), (ap2[1], ap2[0])) for ap2 in aps]
        dst.sort()
        med = dst[len(dst) >> 1]
        ap.append(med)
        meds.append(med)
    meds.sort()
    med = max(meds[int(len(meds) * 0.6)], 50)
    lbadaps += [ap for ap in aps if ap[4] > med]
    for ap in lbadaps:
        BADAPS[ap[3]] = BADAPS.get(ap[3], 0) + 1
    if lbadaps:
        bap = dict([(k, v) for k, v in BADAPS.iteritems() if v > 15])
        a = open('data/black.lst', 'w')
        a.write(repr(bap))
        a.close()
    aps = [ap for ap in aps if ap[4] <= med]
    rlat = 0
    rlon = 0
    count = 0
    acc = 0
    for ap in aps:
        rlon += ap[2] * ap[0]
        rlat += ap[2] * ap[1]
        count += ap[2]
        acc += ap[2] * ap[4]
    return (rlon / count, rlat / count, max(50., acc / count))