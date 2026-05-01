# Location provider status

This project can use local cache data first, then online providers when local data
does not produce a sufficiently accurate result. The online provider landscape has
changed since the original Python 2 code was written, so providers are split into
offline, free online, credential-gated online, and removed dead integrations.

## Provider profiles

`locate` always checks offline providers first. If the best offline result is not
accurate enough and online providers are enabled, the `--online-profile` option
selects the online branch:

| Profile | Providers attempted |
| --- | --- |
| `all` | Default. Use free/no-key online providers and every credential-gated provider whose environment variables are present. |
| `free` | Use only providers that do not need account credentials. |
| `keyed` | Use only credential-gated providers whose environment variables are present. |

Diagnostics report provider `branch` (`free`, `keyed`, or `legacy`) and skipped
credential-gated providers as `skipped_missing_credentials`.

Provider modules are laid out the same way: no-key providers live under
`databases.online.free`, and credential-gated providers live under
`databases.online.keyed`.

## Sensor sources

`locate` builds one sensor state before calling providers:

| Sensor | Status | Notes |
| --- | --- | --- |
| NetworkManager/DBus | Default Wi-Fi sensor when Python DBus bindings and NetworkManager are available. | Collects BSSID, SSID, and signal strength from active scan results. |
| `nmcli` | Optional Wi-Fi fallback. | Uses `nmcli -t --escape yes -f BSSID,SSID,SIGNAL dev wifi list --rescan no`, so systems with NetworkManager but without Python DBus can still feed online Wi-Fi providers. |
| `iwlist` | Legacy Wi-Fi fallback. | Kept for systems without NetworkManager; unsupported-scan output is ignored. |
| BLE/BlueZ | Opt-in nearby Bluetooth Low Energy fingerprint source. | Set `FIDELITY_BLE_SCAN=1` to run `bluetoothctl scan le`; optional `FIDELITY_BLE_SCAN_SECONDS` defaults to `10`. The sensor records BLE MAC, name, RSSI, TxPower, address type, and manufacturer data when BlueZ exposes them. BLE MAC/name/RSSI rows are sent as `bluetoothBeacons` to providers that document that field. |
| `gpsd`/`gpspipe` | Optional GPS source. | Reads the first usable TPV fix from `gpspipe -w -n 10` and feeds the existing offline GPS provider before coarse timezone/binary fallbacks. |
| Raw NMEA serial GPS | Opt-in GPS source for USB/UART GNSS receivers. | Set `FIDELITY_NMEA_DEVICE` to a serial path such as `/dev/ttyUSB0`; optional `FIDELITY_NMEA_BAUD` defaults to `9600`. `FIDELITY_NMEA_START_COMMAND` can send an explicit receiver start command such as `$GPS_START` before reading. The sensor reads RMC/GGA sentences for a short window and never probes or writes to serial devices unless explicitly configured. |
| ModemManager/`mmcli` | Optional cellular, modem GPS, and modem-status source. | Reads `mmcli -m any --location-get --output-json` and `--location-status`; if PolicyKit blocks location reads and passwordless sudo is available, it retries with `sudo -n mmcli` so headless machines can expose modem GPS without an interactive prompt. Valid 3GPP MCC/MNC/LAC/TAC/CID rows become normalized `cell` observations; modem GPS fields become `gps` when already available. Safe modem status excludes IMEI and SIM identifiers. |
| ARP | Legacy local-network hints. | Kept for offline/diagnostic consumers. Online Wi-Fi providers ignore rows without an SSID. |

ModemManager activation is intentionally opt-in because it can change modem state.
Use `fidelity-modemmanager-setup` to prepare system modem location settings
explicitly. The command prints the `mmcli` operations by default and only changes
the modem when `--apply` is passed:

```bash
fidelity-modemmanager-setup --enable-3gpp --enable-agps-msb --enable-gps
fidelity-modemmanager-setup --gps-unmanaged --gps-refresh-rate 0 --apply
```

The setup command can:

* enable `3gpp-lac-ci` for serving-cell LAC/TAC/CID observations;
* enable GPS NMEA and/or raw outputs, which start the modem GPS engine;
* enable `gps-unmanaged` when gpsd or another service should own the NMEA tty;
* configure `--location-set-supl-server=HOST:PORT` before A-GPS;
* enable A-GPS MSA/MSB before GPS outputs;
* inject a local GNSS assistance-data file exposed by the modem vendor path;
* set the ModemManager D-Bus GPS refresh rate;
* enable ModemManager D-Bus location update signals for consumers that watch
  location properties instead of polling.

Set `FIDELITY_MODEMMANAGER_ENABLE_GPS=1` to ask `fidelity-locate` itself to enable
`gps-nmea` and `gps-raw` before reading location. If ModemManager returns NMEA
sentences instead of parsed GPS latitude/longitude fields, fidelity parses valid
RMC/GGA fixes from the `gps.nmea` array. For devices that are registered on a
network, `FIDELITY_MODEMMANAGER_ENABLE_3GPP=1` asks ModemManager to enable the
`3gpp-lac-ci` location source before reading.

A-GPS is also explicit. `FIDELITY_MODEMMANAGER_SUPL_SERVER` sets the SUPL server
with `mmcli --location-set-supl-server=...`, and
`FIDELITY_MODEMMANAGER_ENABLE_AGPS_MSB=1` /
`FIDELITY_MODEMMANAGER_ENABLE_AGPS_MSA=1` ask ModemManager to enable those A-GPS
sources. `FIDELITY_MODEMMANAGER_ENABLE_GPS_NMEA=1`,
`FIDELITY_MODEMMANAGER_ENABLE_GPS_RAW=1`,
`FIDELITY_MODEMMANAGER_ENABLE_GPS_UNMANAGED=1`,
`FIDELITY_MODEMMANAGER_GPS_REFRESH_RATE=SEC`,
`FIDELITY_MODEMMANAGER_ENABLE_LOCATION_SIGNALS=1`, and
`FIDELITY_MODEMMANAGER_ASSISTANCE_DATA=/path/to/file` mirror the setup command
for environments where configuration must happen as part of a locate run.

Raw serial NMEA access is explicit for the same reason: fidelity does not probe
or write to serial devices unless `FIDELITY_NMEA_DEVICE` is set. Some receivers
need a start command before they stream NMEA, so
`FIDELITY_NMEA_START_COMMAND` can send one configured line before reading.

BLE scanning is opt-in because it changes local radio state and exposes nearby
device identifiers. When enabled, the resulting BLE rows can feed providers that
document `bluetoothBeacons`.

Operator scanning is intentionally opt-in because it can be slow and active:
set `FIDELITY_MODEMMANAGER_SCAN_OPERATORS=1` to also run
`mmcli -m any --3gpp-scan --output-json --timeout=120`. Scan results are exposed
as diagnostic `operators` rows with operator code/name, MCC/MNC, access
technologies, and availability. Operator rows alone are not enough for tower
geolocation; providers need a serving cell ID or richer neighbour-cell data.

## Local observation learning

Fidelity can build its own local evidence trail when a machine sees radio
fingerprints and also has a trustworthy GPS fix. This is opt-in because Wi-Fi,
BLE, cell, and GPS observations are local private data.

```bash
fidelity-locate --offline-only --record-observation
fidelity-record-observation --max-cache-accuracy 50
fidelity-build-spatial-cache
fidelity-serve --record-observations
```

When enabled, fidelity appends JSONL records to `data/observations.jsonl` by
default. Each row keeps the observation time, visible Wi-Fi rows, BLE rows, cell
rows, GPS row, and the anchor position. If the anchor is a GPS fix whose accuracy
is within `--observation-max-cache-accuracy` (default `100` meters), visible Wi-Fi
BSSIDs are also appended to `data/our.bin`, the existing binary offline Wi-Fi
cache format used by `databases.offline.binary`. SSIDs ending in `_nomap` are
logged but not learned into the Wi-Fi cache.

The remote `/v1/geolocate` endpoint does not learn from submitted client
fingerprints. Only local `/v1/location`, `fidelity-locate`, and
`fidelity-record-observation` can record observations.

Environment variables mirror the CLI for unattended devices:

| Variable | Meaning |
| --- | --- |
| `FIDELITY_RECORD_OBSERVATIONS=1` | Enable observation recording for `fidelity-locate` and `fidelity-serve /v1/location`. |
| `FIDELITY_OBSERVATION_LOG` | JSONL observation path; defaults to `data/observations.jsonl`. |
| `FIDELITY_OBSERVATION_WIFI_CACHE` | Binary Wi-Fi cache path; defaults to `data/our.bin`. |
| `FIDELITY_OBSERVATION_MAX_CACHE_ACCURACY` | Maximum GPS accuracy in meters accepted for Wi-Fi cache learning. |
| `FIDELITY_OBSERVATION_DISABLE_LOG=1` | Skip the JSONL log while still allowing cache learning. |
| `FIDELITY_OBSERVATION_DISABLE_WIFI_CACHE=1` | Keep the JSONL log but do not append the binary Wi-Fi cache. |
| `FIDELITY_OBSERVATION_ALLOW_ESTIMATED=1` | Allow the best non-GPS location as a log anchor; Wi-Fi cache learning still requires the configured accuracy threshold. |
| `FIDELITY_SPATIAL_OBSERVATION_LOG` | Observation JSONL path used by the richer pairwise Wi-Fi cache; defaults to `FIDELITY_OBSERVATION_LOG` or `data/observations.jsonl`. |
| `FIDELITY_SPATIAL_CACHE` | Materialized pairwise/heading Wi-Fi cache path; defaults to `data/spatial_wifi_pairs.json`. |
| `FIDELITY_SPATIAL_MAX_ANCHOR_ACCURACY` | Maximum GPS accuracy accepted by the pairwise/heading cache; defaults to `FIDELITY_OBSERVATION_MAX_CACHE_ACCURACY` or `100`. |
| `FIDELITY_SPATIAL_GZIP_LEVEL` | Gzip compression level for materialized cache paths ending in `.gz`; defaults to `6` and is clamped to `0..9`. |

## Offline Wi-Fi positioning

The binary, Redis, learned local Wi-Fi caches, and richer observation-log cache
feed the same offline Wi-Fi positioning helper. The helper keeps the legacy
binary cache format, but the estimator now uses the portable parts of the
historical PostGIS TPV query:

* RSSI and observation age are converted into an uncertainty radius with
  `10 * lag_s + 0.2347 * exp(-0.07877 * rssi_dbm)`;
* stale Wi-Fi observations therefore become weaker instead of being treated as
  fresh AP hits;
* candidate AP points are solved with a weighted geometric median instead of a
  plain weighted average, so a distant outlier is less able to drag the estimate;
* a recent GPS fix can be used as a weak prior for the Wi-Fi solve, while GPS
  itself still remains the preferred direct offline provider when accurate.

Pairwise AP geometries and heading buckets are handled by
`databases.offline.spatial`. It reads the local observation JSONL, accepts only
GPS-anchored Wi-Fi observations within the configured learning accuracy, builds
pair keys for every visible BSSID pair, and stores the anchor point under the
GPS heading bucket rounded down to 60 degrees. Repeated samples for the same
pair and heading are compacted into one robust geometry, and heading-specific
samples also produce an all-heading `-360` geometry like the historical SQL.
Lookup requests with a current GPS heading prefer samples from the same bucket
while still accepting all-heading and heading-agnostic samples; requests without
heading use only all-heading and heading-agnostic samples. The result is then
solved by the same weighted geometric median helper as the binary and Redis
caches.

For unattended services, run `fidelity-build-spatial-cache` after recording a
batch of local observations. Runtime lookup first reads the materialized
`FIDELITY_SPATIAL_CACHE` JSON file and falls back to rebuilding from the
observation JSONL when the compact cache has not been created yet, or when an
existing materialized cache is unreadable or uses an unsupported payload version.
The builder accepts one or more plain JSONL or `.gz`-compressed JSONL paths via
`--observations`; rows may be fidelity observation records or generic GPS TPV
plus Wi-Fi dump records with `tpv` and `wifi` fields. `--json` prints only
aggregate counters such as read records, usable records, Wi-Fi rows, raw pair
samples, compact pair count, and compact sample count. Materialized cache paths
ending in `.gz` are written and read as compressed JSON; `--gzip-level` can tune
the compression level for one build and otherwise defaults to
`FIDELITY_SPATIAL_GZIP_LEVEL`. Cache payloads store the number of input sources
rather than their file paths. Current cache files use a compact sample-array
payload on disk while runtime lookup expands it back to the same named geometry
fields; legacy version-1 payloads with named sample objects remain readable.

This is intentionally separate from `data/our.bin`: the legacy binary cache stays
portable and append-only for `mac -> point`, while pairwise/heading evidence uses
the richer JSONL observation history.

## Free online providers

| Provider | Status | How it is called |
| --- | --- | --- |
| beaconDB | Default no-key online Wi-Fi/BLE provider. It is an experimental public-domain wireless geolocation database and exposes an Ichnaea/MLS-compatible endpoint. | `POST https://api.beacondb.net/v1/geolocate` with `wifiAccessPoints` and/or `bluetoothBeacons`, negative integer `signalStrength` values in dBm, `considerIp: false`, and `fallbacks.ipf/lacf: false`. Requests include a `User-Agent`. |
| ipapi.co | No-key HTTPS IP fallback. | `GET https://ipapi.co/{ip}/json/`, or `GET https://ipapi.co/json/` for the caller's public IP. This is IP-only and approximate, but it is safe to use once online lookup is enabled. |
| ip-api.com | No-key HTTP-only IP fallback. | `GET http://ip-api.com/json/{query}?fields=...`. This sends only the public IP or no explicit IP when locating the caller, so it is enabled in online mode despite the free endpoint being HTTP-only. |

beaconDB asks clients to identify themselves with a User-Agent and documents that
its API is compatible with Mozilla Location Service/Ichnaea. Ichnaea-compatible
Bluetooth/Wi-Fi requests need at least two networks with `macAddress`, and
`_nomap` Wi-Fi SSIDs must be filtered out by clients.

`locate` merges Wi-Fi scan data from NetworkManager, `nmcli`, and `iwlist` before
calling online providers. ARP rows are kept in diagnostics for legacy/offline
consumers, but online Wi-Fi providers ignore entries without an SSID so local
client devices are not sent as access-point observations.

## Credential-gated online providers

These providers are current APIs, but they require credentials. The provider
registry skips them before making a request unless the matching environment
variables are present, so normal use does not leak requests to paid or
account-scoped services by accident.

| Provider | Environment | How it is called |
| --- | --- | --- |
| Google Geolocation API | `FIDELITY_GOOGLE_GEOLOCATION_API_KEY` or `GOOGLE_GEOLOCATION_API_KEY` | `POST https://www.googleapis.com/geolocation/v1/geolocate?key=...` with JSON `wifiAccessPoints`, optional `cellTowers`, and `considerIp: false`. Google documents that Wi-Fi `signalStrength` must be negative dBm; values greater than `-10` produce `NOT FOUND`. |
| HERE Network Positioning API v2 | `FIDELITY_HERE_API_KEY` or `HERE_API_KEY` | `POST https://positioning.hereapi.com/v2/locate?apiKey=...` with JSON `wlan` rows. The project sends MAC-48 addresses as `mac`, RSSI as `rss`, and requires at least two Wi-Fi access points instead of enabling `fallback=singleWifi`. |
| Unwired Labs LocationAPI | `FIDELITY_UNWIREDLABS_TOKEN` or `UNWIREDLABS_TOKEN`; optional `FIDELITY_UNWIREDLABS_URL` region override | `POST https://us1.unwiredlabs.com/v2/process.php` by default with JSON `token`, `wifi` and/or `cells`, and `address: 0`. Unwired Labs also publishes `us2`, `eu1`, and `ap1` regional endpoints. |
| Combain Location API | `FIDELITY_COMBAIN_API_KEY` or `COMBAIN_API_KEY` | `POST https://apiv2.combain.com?key=...` with JSON `wifiAccessPoints` and/or `bluetoothBeacons`. The project requires at least two Wi-Fi/BLE radio observations before calling it. Combain documents Google/MLS-compatible migration and explicitly supports Bluetooth beacon identifiers by MAC address or iBeacon UUID/major/minor, but notes that outdoor wide-area Wi-Fi positioning for new prepaid customers has been discontinued; existing/contract access may still apply. |
| Yandex Locator API | `FIDELITY_YANDEX_LOCATOR_API_KEY` or `YANDEX_LOCATOR_API_KEY` | `POST https://locator.api.maps.yandex.ru/v1/locate?apikey=...` with JSON `wifi`, `cell`, or `ip` arrays. The legacy `api.lbs.yandex.net/geolocation` endpoint moved to this API; Wi-Fi rows now use `bssid` instead of `mac`. Cell rows are sent only when ModemManager exposes dBm signal strength, because Yandex marks `signal_strength` as required. |
| OpenCellID | `FIDELITY_OPENCELLID_API_KEY` or `OPENCELLID_API_KEY`; optional `FIDELITY_OPENCELLID_URL` host override | `GET https://opencellid.org/cell/get?...&format=json` with MCC, MNC, LAC/TAC, Cell ID, and optional radio. This is useful once ModemManager returns a registered 3GPP cell. |
| MaxMind GeoLite2 City web service | `FIDELITY_MAXMIND_ACCOUNT_ID`/`FIDELITY_MAXMIND_LICENSE_KEY`, or `MAXMIND_ACCOUNT_ID`/`MAXMIND_LICENSE_KEY` | `GET https://geolite.info/geoip/v2.1/city/{ip}` using HTTP Basic auth. This is IP-only and should be treated as coarse fallback data, not Wi-Fi positioning. Set `FIDELITY_MAXMIND_HOST=geoip.maxmind.com` if using the paid GeoIP2 host. |
| Abstract IP Geolocation API | `FIDELITY_ABSTRACT_API_KEY` or `ABSTRACT_API_KEY` | `GET https://ipgeolocation.abstractapi.com/v1/?api_key=...&ip_address=...`. This is IP-only and approximate. |

## Researched but not wired by default

| Provider | Notes |
| --- | --- |
| Neighbour cell scans | ModemManager's simple `--location-get` path exposes the serving 3GPP cell, and `--3gpp-scan` exposes operators, not tower IDs. Providers work with one cell, but multi-cell positioning would need a deeper modem-specific neighbour-cell path. |
| Google Geolocation API BLE | The current Google Maps Geolocation API request schema documents `cellTowers` and `wifiAccessPoints`, but not `bluetoothBeacons`, so fidelity does not send BLE there. |
| IndoorAtlas, Navigine, and similar indoor-positioning platforms | These products can use BLE beacons, but they are deployment/venue SDK platforms: they need mapped indoor spaces, installed beacon infrastructure, and account/project setup rather than a drop-in global HTTP geolocation lookup for arbitrary scans. |

## Removed integrations

| Removed module | What happened |
| --- | --- |
| Mozilla MLS online provider | Mozilla retired MLS in 2024: new API keys stopped, submissions were disabled, third-party keys were removed, and the Ichnaea repository was archived on July 31, 2024. The former `databases.online.mozilla` module was removed instead of preserving a guaranteed-404 integration. |
| `databases.online.keyed.google_geolocation` old behavior | The old module was named `googlejsapi` and scraped `http://google.com/jsapi`, which now serves the Google Charts loader rather than an IP geolocation payload. The module has been renamed and repointed to the current Google Geolocation API, opt-in via API key. |
| `databases.online.keyed.yandex` old behavior | The old code used `http://api.lbs.yandex.net/geolocation` with an API key embedded in the request body. Yandex documents that the current endpoint is `locator.api.maps.yandex.ru/v1/locate` with the key in the `apikey` query parameter. |
| `databases.online.keyed.maxmind2` old behavior | The old demo URL `http://www.maxmind.com/geoip/city_isp_org/...?...demo=1` is not a supported integration path. MaxMind's current web services use `/geoip/v2.1/...` with account/license Basic auth. |
| OpenWLANMap online lookup | The historic `findmac.php` parser is not a maintained online API integration and was removed with the former `databases.online.openwlanmap` module. The repository's bundled `data/openwlanmap.bin` remains available for offline lookups. |

## Sources checked

* beaconDB documents `https://api.beacondb.net/v1/geolocate` as an
  Ichnaea/MLS-compatible endpoint, shows beacon counts, and asks clients to set
  a User-Agent:
  <https://beacondb.net/>
* Ichnaea documents the `wifiAccessPoints` and `bluetoothBeacons` payloads,
  `_nomap` filtering responsibility, fallback controls, and 404-not-found
  behavior:
  <https://ichnaea.readthedocs.io/en/stable/api/geolocate.html>
* Mozilla's Ichnaea issue #2065 documents MLS retirement and the July 31, 2024
  repository archive:
  <https://github.com/mozilla/ichnaea/issues/2065>
* Google documents the current Geolocation API endpoint and negative dBm
  requirement:
  <https://developers.google.com/maps/documentation/geolocation/requests-geolocation>
* Yandex documents migration from `api.lbs.yandex.net/geolocation` to
  `locator.api.maps.yandex.ru/v1/locate`:
  <https://yandex.com/maps-api/docs/locator-api/migration.html>
* Yandex documents `cell` payloads with `gsm`, `wcdma`, and `lte` rows, and
  marks cell `signal_strength` as required:
  <https://yandex.com/maps-api/docs/locator-api/request.html>
* MaxMind documents current GeoIP/GeoLite web services under `/geoip/v2.1/...`
  with Basic auth:
  <https://dev.maxmind.com/geoip/docs/web-services/>
* Unwired Labs documents LocationAPI v2 regional endpoints, Wi-Fi/cell request
  rows, and `status`/`lat`/`lon`/`accuracy` responses:
  <https://locationapi.org/site/page?view=apiv2>
* Combain documents the `https://apiv2.combain.com?key=...` endpoint,
  Google/MLS-compatible migration, Wi-Fi/Bluetooth support, and response shape:
  <https://portal.combain.com/api/>
* HERE documents Network Positioning API v2 at
  `https://positioning.hereapi.com/v2/locate` and WLAN request rows:
  <https://docs.here.com/positioning/docs/example-wlan>
* ipapi.co documents `GET https://ipapi.co/{ip}/json/` and caller-IP JSON
  endpoints:
  <https://ipapi.co/api/>
* Abstract documents the IP geolocation endpoint and response coordinates:
  <https://www.abstractapi.com/api/ip-geolocation-api>
* OpenCellID documents the current `/cell/get` lookup API:
  <https://wiki.opencellid.org/index.php/API>
* ModemManager documents the 3GPP `Scan` result fields, including
  `operator-code`, `operator-long`, `operator-short`, `status`, and
  `access-technology`:
  <https://www.freedesktop.org/software/ModemManager/api/1.0.0/gdbus-org.freedesktop.ModemManager1.Modem.Modem3gpp.html>
* ip-api.com documents its no-key JSON API as `http://ip-api.com/json/{query}`:
  <https://ip-api.com/docs/api:json>
