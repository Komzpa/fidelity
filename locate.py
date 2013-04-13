import sensors.iwlist

import databases.online.yandex
import databases.online.openwlanmap
import databases.offline.binary

sens = sensors.iwlist.get_state()

print sens

def printlink(loc):
    if loc:
        print "http://www.openstreetmap.org/?mlat=%s&mlon=%s&zoom=16" % (loc["position"]["latitude"], loc["position"]["longitude"])
    else:
        print "..."

loc = databases.offline.binary.get_location(sens)
printlink(loc)
if not loc:
    loc = databases.online.yandex.get_location(sens)
    printlink(loc)
    loc = databases.online.openwlanmap.get_location(sens)
    printlink(loc)
