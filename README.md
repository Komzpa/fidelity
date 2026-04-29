# fidelity

Wireless geolocation helpers for locating a machine from nearby Wi-Fi, ARP, IP, and
offline cache signals.

The project is an old experimental codebase, now ported to modern Python syntax and
kept importable on current Python releases. Optional backends stay optional: PostgreSQL,
Redis, GeoIP, NetworkManager/DBus, and external geolocation services are only needed
when you call the corresponding provider.
The online Wi-Fi lookup uses beaconDB, an Ichnaea-compatible public wireless
geolocation service, when local cache data is not enough.

## Usage

Run the locator. It collects local sensor hints from NetworkManager/DBus, `nmcli`,
`iwlist`, optional `gpsd`/`gpspipe`, opt-in raw NMEA serial GPS, ModemManager/`mmcli`,
and ARP before asking offline and online providers:

```bash
python locate.py
# or, after installation:
fidelity-locate
```

Useful CLI switches:

```bash
python locate.py --offline-only
python locate.py --json
python locate.py --json --diagnostics
python locate.py --accuracy 50000
python locate.py --online-profile free
python locate.py --online-profile keyed
fidelity-locate --offline-only --json
```

Run a small local HTTP endpoint when other software should query fidelity:

```bash
fidelity-serve --host 127.0.0.1 --port 8765
curl 'http://127.0.0.1:8765/v1/location?diagnostics=1'
curl -s http://127.0.0.1:8765/v1/geolocate \
  -H 'content-type: application/json' \
  -d '{"wifiAccessPoints":[{"macAddress":"aa:bb:cc:00:11:22","signalStrength":-63}]}'
```

`/v1/geolocate` follows the Ichnaea/MLS geolocation contract, including
`wifiAccessPoints`, `bluetoothBeacons`, `cellTowers`, `considerIp`, `fallbacks`,
and the standard `location`/`accuracy` response. A `?key=...` query parameter is
accepted for client compatibility and ignored by the local server. The Google
Geolocation-style `/geolocation/v1/geolocate` path is also accepted as an alias.

Record local observations when a machine has both a usable GPS fix and visible
radio fingerprints. This appends a JSONL trace with Wi-Fi/BLE/GPS/cell rows and,
when the GPS fix is accurate enough, extends the local binary Wi-Fi cache. The
same JSONL also feeds the richer offline Wi-Fi cache that matches BSSID pairs and
GPS heading buckets:

```bash
fidelity-locate --offline-only --record-observation
fidelity-record-observation --max-cache-accuracy 50
fidelity-build-spatial-cache
fidelity-serve --record-observations
```

`fidelity-build-spatial-cache` accepts one or more JSONL or JSONL.gz observation
paths through `--observations`. Besides fidelity's own observation records, it
can read generic GPS TPV plus Wi-Fi dump rows with `tpv` and `wifi` fields, and
its `--json` output reports aggregate build counters without printing learned
MACs or coordinates. Use an `.gz` suffix on `--output` when a large
materialized cache should be stored compressed.

For USB GNSS receivers that expose NMEA directly instead of going through gpsd,
opt in explicitly so arbitrary serial devices are not touched:

```bash
FIDELITY_NMEA_DEVICE=/dev/ttyUSB0 FIDELITY_NMEA_BAUD=115200 fidelity-locate --offline-only
```

Prepare ModemManager location sources explicitly when a modem should expose GPS,
3GPP cell location, A-GPS, or a gpsd-owned unmanaged NMEA port. The command is a
dry run unless `--apply` is passed:

```bash
fidelity-modemmanager-setup --enable-3gpp --enable-agps-msb --enable-gps
fidelity-modemmanager-setup --gps-unmanaged --gps-refresh-rate 0 --apply
```

The human-readable output prints OpenStreetMap links sorted by reported accuracy.
The JSON output includes the collected sensor state and every candidate location.
Add `--diagnostics` when you need to see which sensor/provider produced the result,
whether online providers were attempted, and whether the best location is only a
coarse fallback.
See [docs/providers.md](docs/providers.md) for the current status of each online
provider, online provider profiles, and the environment variables for
credential-gated APIs. See [docs/integrations.md](docs/integrations.md) for
GeoClue, gpsd, and HTTP endpoint integration notes.

## Development

Create an environment with the development tools:

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
python -m pip install build twine
```

Run checks:

```bash
ruff check .
ruff format --check .
pytest -q
python -m compileall -q bin-retile.py bin_retile.py locate.py observations.py server.py databases sensors
python -m build --wheel --sdist
python -m twine check dist/*
```

Optional extras:

```bash
python -m pip install -e '.[postgres]'
python -m pip install -e '.[redis]'
```

## Data Files

The repository still contains legacy offline cache data under `data/`. `bin-retile.py`
can split binary Wi-Fi cache files into tiled cache files and regenerate `index.json`.
After installation, the same command is available as `fidelity-retile`.
