import sensors.networkmanager
import sensors.iwlist

import databases.online.yandex
import databases.online.openwlanmap
import databases.online.maxmind2
import databases.online.googlejsapi
import databases.offline.binary
import databases.offline.timezone

import sys
import os
os.chdir(os.path.abspath(os.path.dirname(sys.modules['__main__'].__file__)))

def printlink(loc):
    if loc:
        print "http://www.openstreetmap.org/?mlat=%s&mlon=%s&zoom=16 (accuracy %s m, service %s)" % (loc["position"]["latitude"], loc["position"]["longitude"], loc["position"]["accuracy"], loc["service"])
    else:
        print "cannot determine location"

def filter_accuracy(ll, accuracy):
    ll = [x for x in ll if x]
    ll.sort(key=lambda x: x["position"]["accuracy"])
    is_good = ll and ll[0]["position"]["accuracy"] <= accuracy
    return is_good, ll

sens = sensors.networkmanager.get_state()
if not sens:
    sens = sensors.iwlist.get_state()
print "Found wifi:", sens

ll = []
ll.append(databases.offline.timezone.get_location())
ll.append(databases.offline.binary.get_location(sens))
is_good, ll = filter_accuracy(ll, 100000)
if not is_good:
    ll.append(databases.online.googlejsapi.get_location(sens))
    ll.append(databases.online.yandex.get_location(sens))
    ll.append(databases.online.maxmind2.get_location(sens))
    ll.append(databases.online.openwlanmap.get_location(sens))
    is_good, ll = filter_accuracy(ll, 100000)
[printlink(x) for x in ll]

