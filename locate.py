import sensors.iwlist

import databases.online.yandex
import databases.online.openwlanmap
import databases.online.maxmind2
import databases.online.googlejsapi
import databases.offline.binary
import databases.offline.timezone



def printlink(loc):
    if loc:
        print "http://www.openstreetmap.org/?mlat=%s&mlon=%s&zoom=16" % (loc["position"]["latitude"], loc["position"]["longitude"])
    else:
        print "..."

loc = databases.offline.timezone.get_location()
printlink(loc)

sens = sensors.iwlist.get_state()

print sens



loc = databases.offline.binary.get_location(sens)
printlink(loc)
loc = databases.online.googlejsapi.get_location(sens)
printlink(loc)
loc = databases.online.yandex.get_location(sens)
printlink(loc)
loc = databases.online.maxmind2.get_location(sens)
printlink(loc)
loc = databases.online.openwlanmap.get_location(sens)
printlink(loc)