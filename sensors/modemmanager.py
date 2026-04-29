import datetime
import argparse
import json
import os
import subprocess
import time

from sensors import nmea


def _int_or_none(value):
    if value in (None, "", "--"):
        return None
    try:
        value = str(value)
        if value.isdecimal():
            return int(value, 10)
        return int(value, 0)
    except ValueError:
        return None


def _float_or_none(value):
    if value in (None, "", "--"):
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _timestamp_ms(value):
    if value in (None, "", "--"):
        return int(time.time() * 1000)
    try:
        parsed = datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return int(time.time() * 1000)
    return int(parsed.timestamp() * 1000)


def _enabled(name):
    return os.environ.get(name, "").lower() in ("1", "true", "yes", "on")


def _run(args, *, timeout=5):
    try:
        return subprocess.run(
            args,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def _should_retry_with_sudo(result):
    if result is None:
        return False
    stderr = (getattr(result, "stderr", "") or "").lower()
    return "policykit authorization failed" in stderr or "not authorized" in stderr


def _run_mmcli(args, *, timeout=5, allow_sudo_retry=True):
    result = _run(["mmcli", *args], timeout=timeout)
    if allow_sudo_retry and _should_retry_with_sudo(result):
        sudo_result = _run(["sudo", "-n", "mmcli", *args], timeout=timeout)
        if sudo_result is not None:
            return sudo_result
    return result


def _run_json(args, *, timeout=5):
    result = _run_mmcli(args, timeout=timeout)
    if result is None:
        return {}

    try:
        return json.loads(result.stdout)
    except ValueError:
        return {}


def _present(value):
    return value not in (None, "", "--", [])


def _safe_modem_status(modem, location_status):
    generic = modem.get("generic", {})
    location = location_status.get("modem", {}).get("location", {})
    status = {
        "manufacturer": generic.get("manufacturer"),
        "model": generic.get("model"),
        "state": generic.get("state"),
        "state_failed_reason": generic.get("state-failed-reason"),
        "power_state": generic.get("power-state"),
        "location_capabilities": location.get("capabilities", []),
        "location_enabled": location.get("enabled", []),
    }
    return {key: value for key, value in status.items() if _present(value)}


def _radio_from_modem(modem):
    technologies = modem.get("generic", {}).get("access-technologies", [])
    for technology in technologies:
        technology = technology.lower()
        if "5gnr" in technology or technology == "nr":
            return "NR"
        if "lte" in technology:
            return "LTE"
        if "umts" in technology or "hspa" in technology:
            return "UMTS"
        if "gsm" in technology or "edge" in technology or "gprs" in technology:
            return "GSM"
    return None


def _normalize_operator(row):
    if isinstance(row, str):
        try:
            row = json.loads(row)
        except ValueError:
            return None
    if not isinstance(row, dict):
        return None

    code = row.get("operator-code")
    if not _present(code):
        return None

    operator = {"operator_code": code}
    if len(code) >= 5:
        operator["mcc"] = _int_or_none(code[:3])
        operator["mnc"] = _int_or_none(code[3:])
    if _present(row.get("operator-name")):
        operator["operator_name"] = row["operator-name"]
    if _present(row.get("access-technologies")):
        operator["access_technologies"] = [
            item.strip() for item in str(row["access-technologies"]).split(",") if item.strip()
        ]
    if _present(row.get("availability")):
        operator["availability"] = row["availability"]
    return operator


def _operator_scan():
    if not _enabled("FIDELITY_MODEMMANAGER_SCAN_OPERATORS"):
        return []

    scan_doc = _run_json(
        ["-m", "any", "--3gpp-scan", "--output-json", "--timeout=120"],
        timeout=130,
    )
    networks = scan_doc.get("modem", {}).get("3gpp", {}).get("scan-networks", [])
    operators = []
    for row in networks:
        operator = _normalize_operator(row)
        if operator:
            operators.append(operator)
    return operators


def _cell_from_location(location, modem):
    threegpp = location.get("3gpp", {})
    mcc = _int_or_none(threegpp.get("mcc"))
    mnc = _int_or_none(threegpp.get("mnc"))
    cellid = _int_or_none(threegpp.get("cid"))
    lac = _int_or_none(threegpp.get("lac"))
    tac = _int_or_none(threegpp.get("tac"))

    area = lac if lac is not None else tac
    if mcc is None or mnc is None or cellid is None or area is None:
        return None

    cell = {
        "mcc": mcc,
        "mnc": mnc,
        "lac": area,
        "cellid": cellid,
    }
    if tac is not None:
        cell["tac"] = tac
    radio = _radio_from_modem(modem)
    if radio is None and tac is not None and lac is None:
        radio = "LTE"
    if radio:
        cell["radio"] = radio
    return cell


def _gps_from_location(location):
    gps = location.get("gps", {})
    latitude = _float_or_none(gps.get("latitude"))
    longitude = _float_or_none(gps.get("longitude"))
    if latitude is None or longitude is None:
        return nmea._gps_from_lines(gps.get("nmea", []))

    state = {
        "latitude": latitude,
        "longitude": longitude,
        "accuracy": 50.0,
    }
    altitude = _float_or_none(gps.get("altitude"))
    if altitude is not None:
        state["altitude"] = altitude
    if gps.get("utc") not in (None, "", "--"):
        state["time"] = _timestamp_ms(gps["utc"])
    return state


def _signal_strength(signal):
    for family in ("lte", "umts", "gsm", "5g"):
        family_signal = signal.get(family, {})
        for name in ("rssi", "rsrp"):
            value = _float_or_none(family_signal.get(name))
            if value is not None:
                return int(value)
    return None


def setup_commands(
    *,
    modem="any",
    supl_server=None,
    enable_3gpp=False,
    enable_agps_msa=False,
    enable_agps_msb=False,
    enable_gps=False,
    enable_gps_nmea=False,
    enable_gps_raw=False,
    enable_gps_unmanaged=False,
    gps_refresh_rate=None,
    enable_signals=False,
    assistance_data=None,
):
    commands = []

    if supl_server:
        commands.append(["-m", modem, f"--location-set-supl-server={supl_server}"])

    if gps_refresh_rate is not None:
        commands.append(["-m", modem, f"--location-set-gps-refresh-rate={gps_refresh_rate}"])

    if enable_signals:
        commands.append(["-m", modem, "--location-set-enable-signal"])

    if enable_3gpp:
        commands.append(["-m", modem, "--location-enable-3gpp"])

    if enable_agps_msb:
        commands.append(["-m", modem, "--location-enable-agps-msb"])

    if enable_agps_msa:
        commands.append(["-m", modem, "--location-enable-agps-msa"])

    if assistance_data:
        commands.append(["-m", modem, f"--location-inject-assistance-data={assistance_data}"])

    gps_sources = []
    if enable_gps or enable_gps_nmea:
        gps_sources.append("--location-enable-gps-nmea")
    if enable_gps or enable_gps_raw:
        gps_sources.append("--location-enable-gps-raw")
    if enable_gps_unmanaged:
        gps_sources.append("--location-enable-gps-unmanaged")
    if gps_sources:
        commands.append(["-m", modem, *gps_sources])

    return commands


def setup_commands_from_env(*, modem="any"):
    refresh_rate = os.environ.get("FIDELITY_MODEMMANAGER_GPS_REFRESH_RATE")
    if refresh_rate in (None, ""):
        refresh_rate = None

    return setup_commands(
        modem=modem,
        supl_server=os.environ.get("FIDELITY_MODEMMANAGER_SUPL_SERVER"),
        enable_3gpp=_enabled("FIDELITY_MODEMMANAGER_ENABLE_3GPP"),
        enable_agps_msb=_enabled("FIDELITY_MODEMMANAGER_ENABLE_AGPS_MSB"),
        enable_agps_msa=_enabled("FIDELITY_MODEMMANAGER_ENABLE_AGPS_MSA"),
        enable_gps=_enabled("FIDELITY_MODEMMANAGER_ENABLE_GPS"),
        enable_gps_nmea=_enabled("FIDELITY_MODEMMANAGER_ENABLE_GPS_NMEA"),
        enable_gps_raw=_enabled("FIDELITY_MODEMMANAGER_ENABLE_GPS_RAW"),
        enable_gps_unmanaged=_enabled("FIDELITY_MODEMMANAGER_ENABLE_GPS_UNMANAGED"),
        gps_refresh_rate=refresh_rate,
        enable_signals=_enabled("FIDELITY_MODEMMANAGER_ENABLE_LOCATION_SIGNALS"),
        assistance_data=os.environ.get("FIDELITY_MODEMMANAGER_ASSISTANCE_DATA"),
    )


def _enable_location_sources():
    for command in setup_commands_from_env(modem="any"):
        _run_mmcli(command, timeout=20)


def get_state():
    _enable_location_sources()

    location_doc = _run_json(["-m", "any", "--location-get", "--output-json"])
    modem_doc = _run_json(["-m", "any", "--output-json"])
    status_doc = _run_json(["-m", "any", "--location-status", "--output-json"])
    if not location_doc and not modem_doc:
        return None

    modem = modem_doc.get("modem", {})
    location = location_doc.get("modem", {}).get("location", {})
    state = {}
    modem_status = _safe_modem_status(modem, status_doc)
    if modem_status:
        state["modem"] = modem_status

    cell = _cell_from_location(location, modem)
    if cell:
        signal_doc = _run_json(["-m", "any", "--signal-get", "--output-json"])
        signal_strength = _signal_strength(signal_doc.get("modem", {}).get("signal", {}))
        if signal_strength is not None:
            cell["signal"] = signal_strength
        state["cell"] = [cell]

    gps = _gps_from_location(location)
    if gps:
        state["gps"] = gps

    operators = _operator_scan()
    if operators:
        state["operators"] = operators

    return state or None


def _format_mmcli_command(command):
    return "mmcli " + " ".join(command)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Prepare ModemManager location sources for fidelity."
    )
    parser.add_argument("--modem", default="any", help="ModemManager modem selector")
    parser.add_argument(
        "--from-env",
        action="store_true",
        help="build commands from FIDELITY_MODEMMANAGER_* environment variables",
    )
    parser.add_argument("--apply", action="store_true", help="run the generated mmcli commands")
    parser.add_argument("--json", action="store_true", help="print generated commands as JSON")
    parser.add_argument("--supl-server", help="configure the A-GPS SUPL server")
    parser.add_argument("--enable-3gpp", action="store_true", help="enable 3GPP LAC/CID location")
    parser.add_argument("--enable-agps-msa", action="store_true", help="enable A-GPS MSA support")
    parser.add_argument("--enable-agps-msb", action="store_true", help="enable A-GPS MSB support")
    parser.add_argument(
        "--enable-gps",
        action="store_true",
        help="enable both GPS NMEA and GPS raw location outputs",
    )
    parser.add_argument("--gps-nmea", action="store_true", help="enable GPS NMEA output")
    parser.add_argument("--gps-raw", action="store_true", help="enable GPS raw output")
    parser.add_argument(
        "--gps-unmanaged",
        action="store_true",
        help="start GPS without taking over the NMEA tty, useful when gpsd owns it",
    )
    parser.add_argument("--gps-refresh-rate", type=int, help="set ModemManager GPS refresh rate")
    parser.add_argument(
        "--enable-signals",
        action="store_true",
        help="enable ModemManager D-Bus location property update signals",
    )
    parser.add_argument(
        "--assistance-data",
        help="inject a local GNSS assistance-data file before enabling GPS outputs",
    )
    args = parser.parse_args(argv)

    if args.from_env:
        commands = setup_commands_from_env(modem=args.modem)
    else:
        commands = setup_commands(
            modem=args.modem,
            supl_server=args.supl_server,
            enable_3gpp=args.enable_3gpp,
            enable_agps_msa=args.enable_agps_msa,
            enable_agps_msb=args.enable_agps_msb,
            enable_gps=args.enable_gps,
            enable_gps_nmea=args.gps_nmea,
            enable_gps_raw=args.gps_raw,
            enable_gps_unmanaged=args.gps_unmanaged,
            gps_refresh_rate=args.gps_refresh_rate,
            enable_signals=args.enable_signals,
            assistance_data=args.assistance_data,
        )

    if args.json:
        print(json.dumps({"commands": [["mmcli", *command] for command in commands]}, indent=2))
    elif commands:
        for command in commands:
            print(_format_mmcli_command(command))
    else:
        print("No ModemManager setup commands selected.")

    if not args.apply:
        return 0

    failed = False
    for command in commands:
        result = _run_mmcli(command, timeout=130)
        if result is None or result.returncode != 0:
            failed = True
            if result is not None and result.stderr:
                print(result.stderr.strip())
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
