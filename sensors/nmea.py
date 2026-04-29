import datetime
import os
import select
import termios
import time

DEVICE_ENV = "FIDELITY_NMEA_DEVICE"
BAUD_ENV = "FIDELITY_NMEA_BAUD"
READ_SECONDS_ENV = "FIDELITY_NMEA_READ_SECONDS"
START_COMMAND_ENV = "FIDELITY_NMEA_START_COMMAND"

DEFAULT_BAUD = 9600
DEFAULT_READ_SECONDS = 5.0

BAUD_RATES = {
    4800: termios.B4800,
    9600: termios.B9600,
    19200: termios.B19200,
    38400: termios.B38400,
    57600: termios.B57600,
    115200: termios.B115200,
}


def _float_or_none(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _nmea_degrees(value, hemisphere):
    raw = _float_or_none(value)
    if raw is None or not hemisphere:
        return None

    degrees = int(raw // 100)
    minutes = raw - (degrees * 100)
    decimal = degrees + (minutes / 60.0)
    if hemisphere.upper() in ("S", "W"):
        decimal = -decimal
    return decimal


def _timestamp_ms(time_value, date_value=None):
    if not time_value:
        return int(time.time() * 1000)

    try:
        hours = int(time_value[0:2])
        minutes = int(time_value[2:4])
        seconds = float(time_value[4:])
    except (TypeError, ValueError):
        return int(time.time() * 1000)

    whole_seconds = int(seconds)
    microseconds = int((seconds - whole_seconds) * 1_000_000)
    if date_value:
        try:
            day = int(date_value[0:2])
            month = int(date_value[2:4])
            year = int(date_value[4:6])
        except (TypeError, ValueError):
            day = month = year = None
        else:
            year += 2000 if year < 80 else 1900
    else:
        now = datetime.datetime.now(datetime.timezone.utc)
        day = now.day
        month = now.month
        year = now.year

    try:
        parsed = datetime.datetime(
            year,
            month,
            day,
            hours,
            minutes,
            whole_seconds,
            microseconds,
            tzinfo=datetime.timezone.utc,
        )
    except (TypeError, ValueError):
        return int(time.time() * 1000)
    return int(parsed.timestamp() * 1000)


def _clean_sentence(line):
    sentence = line.strip()
    if not sentence.startswith("$"):
        return None
    if "*" in sentence:
        sentence = sentence.split("*", 1)[0]
    return sentence.split(",")


def _gps_from_rmc(parts):
    # $GPRMC,time,status,lat,N,lon,E,speed,course,date,...
    if len(parts) < 10 or parts[2].upper() != "A":
        return None

    latitude = _nmea_degrees(parts[3], parts[4])
    longitude = _nmea_degrees(parts[5], parts[6])
    if latitude is None or longitude is None:
        return None

    gps = {
        "latitude": latitude,
        "longitude": longitude,
        "accuracy": 25.0,
        "time": _timestamp_ms(parts[1], parts[9]),
    }
    speed_knots = _float_or_none(parts[7])
    if speed_knots is not None:
        gps["speed"] = speed_knots * 0.514444
    course = _float_or_none(parts[8])
    if course is not None:
        gps["heading"] = course
    return gps


def _gps_from_gga(parts):
    # $GPGGA,time,lat,N,lon,E,quality,satellites,hdop,altitude,M,...
    if len(parts) < 10:
        return None
    try:
        quality = int(parts[6])
    except ValueError:
        return None
    if quality <= 0:
        return None

    latitude = _nmea_degrees(parts[2], parts[3])
    longitude = _nmea_degrees(parts[4], parts[5])
    if latitude is None or longitude is None:
        return None

    hdop = _float_or_none(parts[8])
    gps = {
        "latitude": latitude,
        "longitude": longitude,
        "accuracy": max((hdop or 5.0) * 5.0, 5.0),
        "time": _timestamp_ms(parts[1]),
    }
    altitude = _float_or_none(parts[9])
    if altitude is not None:
        gps["altitude"] = altitude
    return gps


def _gps_from_line(line):
    parts = _clean_sentence(line)
    if not parts:
        return None

    sentence_type = parts[0][-3:].upper()
    if sentence_type == "RMC":
        return _gps_from_rmc(parts)
    if sentence_type == "GGA":
        return _gps_from_gga(parts)
    return None


def _gps_from_lines(lines):
    for line in lines:
        gps = _gps_from_line(line)
        if gps:
            return gps
    return None


def _int_env(name, default):
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def _float_env(name, default):
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


def _configure_serial(fd, baud):
    attrs = termios.tcgetattr(fd)
    speed = BAUD_RATES.get(baud, BAUD_RATES[DEFAULT_BAUD])
    attrs[0] = 0
    attrs[1] = 0
    attrs[2] = termios.CLOCAL | termios.CREAD | termios.CS8
    attrs[3] = 0
    attrs[4] = speed
    attrs[5] = speed
    termios.tcsetattr(fd, termios.TCSANOW, attrs)


def _write_start_command(fd, start_command):
    if not start_command:
        return

    payload = start_command.encode("ascii")
    if not payload.endswith((b"\n", b"\r")):
        payload += b"\r\n"
    os.write(fd, payload)


def _read_lines(device, baud, read_seconds, start_command=None):
    flags = os.O_NOCTTY | os.O_NONBLOCK
    flags |= os.O_RDWR if start_command else os.O_RDONLY
    fd = os.open(device, flags)
    try:
        _configure_serial(fd, baud)
        _write_start_command(fd, start_command)
        deadline = time.monotonic() + read_seconds
        buffer = b""
        lines = []
        while time.monotonic() < deadline:
            timeout = max(0.0, min(0.5, deadline - time.monotonic()))
            ready, _, _ = select.select([fd], [], [], timeout)
            if not ready:
                continue
            chunk = os.read(fd, 4096)
            if not chunk:
                continue
            buffer += chunk
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                lines.append(line.decode("ascii", "ignore"))
                if _gps_from_line(lines[-1]):
                    return lines
        if buffer:
            lines.append(buffer.decode("ascii", "ignore"))
        return lines
    finally:
        os.close(fd)


def get_state():
    device = os.environ.get(DEVICE_ENV)
    if not device:
        return None

    baud = _int_env(BAUD_ENV, DEFAULT_BAUD)
    read_seconds = _float_env(READ_SECONDS_ENV, DEFAULT_READ_SECONDS)
    start_command = os.environ.get(START_COMMAND_ENV)
    try:
        lines = _read_lines(device, baud, read_seconds, start_command)
    except OSError:
        return None

    gps = _gps_from_lines(lines)
    if gps:
        return {"gps": gps}
    return None


if __name__ == "__main__":
    print(get_state())
