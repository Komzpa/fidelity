import subprocess
import dbus

class WiFiList(): 
    def __init__(self):
        self.bus = dbus.SystemBus()
        self.NM = 'org.freedesktop.NetworkManager'
        nm = self.bus.get_object(self.NM, '/org/freedesktop/NetworkManager')
        self.devlist = nm.GetDevices(dbus_interface = self.NM) 
        self.aps = []

    def dbus_get_property(self, prop, member, proxy):
        return proxy.Get(self.NM+'.' + member, prop, dbus_interface = 'org.freedesktop.DBus.Properties')

    def repopulate_ap_list(self):
        apl = []
        res = []
        for i in self.devlist:
            tmp = self.bus.get_object(self.NM, i)
            if self.dbus_get_property('DeviceType', 'Device', tmp) == 2:
                apl.append(self.bus.get_object(self.NM, i).GetAccessPoints(dbus_interface = self.NM+'.Device.Wireless'))
        for i in apl:
            for j in i:
                res.append(self.bus.get_object(self.NM, j))
        return res

    def update(self):
        self.aps = []
        for i in self.repopulate_ap_list():
            ssid = self.dbus_get_property('Ssid', 'AccessPoint', i)
            ssid = "".join(["%s" % k for k in ssid])
            ss = self.dbus_get_property('Strength', 'AccessPoint', i);
            mac = self.dbus_get_property('HwAddress', 'AccessPoint', i);
            self.aps.append({"mac":str(mac), "ssid": unicode(ssid), "ss": float(ss)})

wfl = WiFiList()

def get_state():
    global wfl
    wfl.update()
    return {"wifi": wfl.aps}

if __name__ == "__main__":
    print get_state()
