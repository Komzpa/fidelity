import subprocess


def get_state():
    a = subprocess.Popen(
        'LC_ALL=C SUDO_ASKPASS=/bin/true sudo -A /sbin/iwlist scan || LC_ALL=C /sbin/iwlist scan', stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, bufsize=1, shell=True).stdout.read()
    wifi = []
    ap = {}
    for line in a.split("\n"):
        line = line.strip()

        if line[:4] == "Cell":
            if ap:
                wifi.append(ap)
                ap = {"ss":1, "ssid":"", "mac":""}
            if " - Address: " in line:
                ap["mac"] = line[-17:]
        if "Quality" in line and "dBm" in line:
            ap["ss"] = float(line.split('level=')[1].split(" dBm")[0])
        elif "Quality" in line and "/100" in line:
            ap["ss"] = float(line.split('level=')[1].split("/100")[0])
        if line[:5] == "ESSID":
            ap["ssid"] = line[7:-1]
    if wifi:
        return {"wifi": wifi}
    else:
        return None

if __name__ == "__main__":
    print get_state()
