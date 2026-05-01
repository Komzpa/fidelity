import datetime
import json
import subprocess
import time


def _timestamp_ms(value):
    if not value:
        return int(time.time() * 1000)
    try:
        parsed = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return int(time.time() * 1000)
    return int(parsed.timestamp() * 1000)


def _accuracy(row):
    values = [row.get(name) for name in ("eph", "epx", "epy")]
    values = [float(value) for value in values if value is not None]
    if values:
        return max(values)
    return 50.0


def _gps_from_tpv(row):
    if row.get("class") != "TPV" or row.get("mode", 0) < 2:
        return None
    if "lat" not in row or "lon" not in row:
        return None

    gps = {
        "latitude": row["lat"],
        "longitude": row["lon"],
        "accuracy": _accuracy(row),
        "time": _timestamp_ms(row.get("time")),
    }
    if "speed" in row:
        gps["speed"] = row["speed"]
    if "track" in row:
        gps["heading"] = row["track"]
    if "altHAE" in row:
        gps["altitude"] = row["altHAE"]
    elif "alt" in row:
        gps["altitude"] = row["alt"]
    return gps


def get_state():
    try:
        result = subprocess.run(
            ["gpspipe", "-w", "-n", "10"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None

    for line in result.stdout.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        gps = _gps_from_tpv(row)
        if gps:
            return {"gps": gps}
    return None


if __name__ == "__main__":
    print(get_state())
