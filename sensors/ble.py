import os
import re
import subprocess

SCAN_ENV = "FIDELITY_BLE_SCAN"
SCAN_SECONDS_ENV = "FIDELITY_BLE_SCAN_SECONDS"

DEFAULT_SCAN_SECONDS = 10

ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
DEVICE_RE = re.compile(r"\[NEW\]\s+Device\s+([0-9A-Fa-f:]{17})(?:\s+(.*))?$")
FIELD_RE = re.compile(r"\[CHG\]\s+Device\s+([0-9A-Fa-f:]{17})\s+([A-Za-z]+):\s+(.+)$")
MANUFACTURER_KEY_RE = re.compile(
    r"\[CHG\]\s+Device\s+([0-9A-Fa-f:]{17})\s+ManufacturerData\.Key:\s+(.+)$"
)


def _enabled(name):
    return os.environ.get(name, "").lower() in ("1", "true", "yes", "on")


def _scan_seconds():
    try:
        return max(1, int(os.environ.get(SCAN_SECONDS_ENV, DEFAULT_SCAN_SECONDS)))
    except ValueError:
        return DEFAULT_SCAN_SECONDS


def _clean(line):
    return ANSI_RE.sub("", line).strip()


def _parse_int(value):
    match = re.search(r"\((-?\d+)\)", value)
    if match:
        return int(match.group(1))
    try:
        return int(value, 0)
    except ValueError:
        return None


def _device(devices, mac):
    mac = mac.upper()
    return devices.setdefault(mac, {"mac": mac})


def _parse_scan(output):
    devices = {}
    pending_manufacturer_key = None

    for raw_line in output.splitlines():
        line = _clean(raw_line)
        if not line:
            continue

        if pending_manufacturer_key and re.match(r"^[0-9a-fA-F]{2}(?:\s+[0-9a-fA-F]{2})+", line):
            mac, key = pending_manufacturer_key
            data = line.split("  ", 1)[0].strip().replace(" ", "").lower()
            _device(devices, mac).setdefault("manufacturer_data", {})[key] = data
            pending_manufacturer_key = None
            continue

        match = FIELD_RE.match(line)
        if match:
            device = _device(devices, match.group(1))
            field = match.group(2)
            value = match.group(3).strip()
            if field == "RSSI":
                rssi = _parse_int(value)
                if rssi is not None:
                    device["rssi"] = rssi
            elif field == "TxPower":
                tx_power = _parse_int(value)
                if tx_power is not None:
                    device["tx_power"] = tx_power
            elif field == "AddressType":
                device["address_type"] = value
            continue

        match = MANUFACTURER_KEY_RE.match(line)
        if match:
            mac = match.group(1)
            key = match.group(2).split(" ", 1)[0].lower()
            _device(devices, mac).setdefault("manufacturer_data", {}).setdefault(key, "")
            pending_manufacturer_key = (mac, key)
            continue

        match = DEVICE_RE.match(line)
        if match:
            device = _device(devices, match.group(1))
            name = (match.group(2) or "").strip()
            if name and name.upper() != device["mac"].replace(":", "-"):
                device["name"] = name

    return list(devices.values())


def get_state():
    if not _enabled(SCAN_ENV):
        return None

    seconds = _scan_seconds()
    try:
        result = subprocess.run(
            ["bluetoothctl", "--timeout", str(seconds), "scan", "le"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=seconds + 5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None

    devices = _parse_scan(result.stdout)
    if devices:
        return {"ble": devices}
    return None


if __name__ == "__main__":
    print(get_state())
