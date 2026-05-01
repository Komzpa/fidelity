import re
import subprocess

CELL_RE = re.compile(r"^Cell\s+\d+\s+-\s+Address:\s+([0-9A-Fa-f:]{17})$")


def _append_access_point(wifi, ap):
    if ap.get("mac"):
        wifi.append(ap)


def get_state():
    a = subprocess.Popen(
        "LC_ALL=C SUDO_ASKPASS=/bin/true sudo -A /sbin/iwlist scan || LC_ALL=C /sbin/iwlist scan",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        shell=True,
    ).stdout.read()
    a = a.decode("utf-8", errors="replace")
    wifi = []
    ap = {}
    for line in a.split("\n"):
        line = line.strip()

        cell_match = CELL_RE.match(line)
        if cell_match:
            _append_access_point(wifi, ap)
            ap = {"ss": 1, "ssid": "", "mac": cell_match.group(1).upper()}
        if "Quality" in line and "dBm" in line:
            ap["ss"] = float(line.split("level=")[1].split(" dBm")[0])
        elif "Quality" in line and "/100" in line:
            ap["ss"] = float(line.split("level=")[1].split("/100")[0])
        if line[:5] == "ESSID":
            ap["ssid"] = line[7:-1]
    _append_access_point(wifi, ap)
    if wifi:
        return {"wifi": wifi}
    return None


if __name__ == "__main__":
    print(get_state())
