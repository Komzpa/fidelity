import subprocess


def _split_nmcli_row(row):
    fields = []
    current = []
    escaped = False
    for char in row:
        if escaped:
            current.append(char)
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == ":":
            fields.append("".join(current))
            current = []
        else:
            current.append(char)
    fields.append("".join(current))
    return fields


def get_state():
    try:
        result = subprocess.run(
            [
                "nmcli",
                "-t",
                "--escape",
                "yes",
                "-f",
                "BSSID,SSID,SIGNAL",
                "dev",
                "wifi",
                "list",
                "--rescan",
                "no",
            ],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None

    wifi = []
    for line in result.stdout.splitlines():
        fields = _split_nmcli_row(line)
        if len(fields) != 3:
            continue
        mac, ssid, signal = fields
        try:
            ss = float(signal)
        except ValueError:
            continue
        if mac:
            wifi.append({"mac": mac.upper(), "ssid": ssid, "ss": ss})
    if wifi:
        return {"wifi": wifi}
    return None


if __name__ == "__main__":
    print(get_state())
