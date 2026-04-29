# System integration options

Fidelity is most useful when it can sit between local radio/GPS sensors and other
software that expects a standard location service. The useful integration shapes
are:

1. Consume system location sources when they already exist.
2. Expose fidelity's cache/provider stack over a small local HTTP API.
3. Let desktop location brokers, browsers, or local tools call that HTTP API.

## HTTP endpoint

`fidelity-serve` exposes a small stdlib HTTP server. It binds to localhost by
default:

```bash
fidelity-serve --host 127.0.0.1 --port 8765
```

Health check:

```bash
curl http://127.0.0.1:8765/health
```

Locate the server machine using the same sensor/provider chain as
`fidelity-locate`:

```bash
curl 'http://127.0.0.1:8765/v1/location?diagnostics=1'
```

Add `--record-observations` when `/v1/location` should also append the local
Wi-Fi/BLE/GPS observation log and learn GPS-anchored Wi-Fi rows into the offline
binary cache. The same local JSONL log also feeds the richer pairwise/heading
Wi-Fi cache. Remote `/v1/geolocate` requests are lookup-only and are never used
to train the local cache.

Query the geolocation database/API stack with an Ichnaea/MLS-style request:

```bash
curl -s http://127.0.0.1:8765/v1/geolocate \
  -H 'content-type: application/json' \
  -d '{
    "wifiAccessPoints": [
      {"macAddress": "aa:bb:cc:00:11:22", "ssid": "example", "signalStrength": -63}
    ],
    "bluetoothBeacons": [
      {"macAddress": "aa:bb:cc:00:11:33", "name": "example", "signalStrength": -57}
    ],
    "considerIp": false
  }'
```

The response shape matches the common geolocation service contract:

```json
{
  "location": {"lat": 41.0, "lng": 44.0},
  "accuracy": 250
}
```

Use `?diagnostics=1` to include the normalized sensor request, provider decisions,
and candidate locations. Use `--offline-only` when the endpoint should answer
only from local/offline data and never proxy to online providers. Use
`--online-profile free`, `keyed`, or `all` to control provider selection.

`/v1/geolocate` treats the request as a remote lookup. Its offline path uses
cache-like providers for the submitted radio fingerprint: the pairwise/heading
observation cache first, then the legacy binary Wi-Fi cache. It does not fall
back to the server machine's GPS or timezone. Use `/v1/location` for the server
machine's own location.

By default the server uses the direct TCP peer address as the caller IP when
`considerIp` is enabled. If the service is deliberately placed behind a trusted
reverse proxy, `--trust-forwarded-for` allows `X-Forwarded-For` to supply that IP.

## Ichnaea/MLS and Google compatibility

The geolocation endpoint is intentionally wire-compatible with the common
Ichnaea/MLS and Google Geolocation API shape:

* `POST /v1/geolocate` accepts the Ichnaea/MLS path. A `?key=...` query parameter
  is accepted for client compatibility and ignored by the local server.
* `POST /geolocation/v1/geolocate` is accepted as a Google-style path alias.
* Request bodies may contain `wifiAccessPoints`, `bluetoothBeacons`,
  `cellTowers`, `radioType`, `considerIp`, and `fallbacks`.
* Wi-Fi records accept `macAddress`, `age`, `channel`, `frequency`,
  `signalStrength`, `signalToNoiseRatio`, and `ssid`.
* Bluetooth records accept `macAddress`, `age`, `name`, and `signalStrength`.
* Cell records accept `radioType`, `mobileCountryCode`, `mobileNetworkCode`,
  `locationAreaCode`, `cellId`, `newRadioCellId`, `age`, `psc`,
  `signalStrength`, and `timingAdvance`.
* `fallbacks.ipf` takes precedence over `considerIp`, matching Ichnaea's behavior.
  Set either `considerIp: false` or `fallbacks: {"ipf": false}` when the caller IP
  must not be used.
* Not-found and parse errors use the Google/Ichnaea-style `error.errors[]` JSON
  envelope so clients can distinguish `notFound` from transport failures.

The endpoint also answers CORS preflight requests, which makes browser-based
local tools easier to point at a loopback fidelity service.

## GeoClue

GeoClue is the Linux desktop location broker exposed on D-Bus as
`org.freedesktop.GeoClue2`. Applications create a client through the manager,
start it, and receive `LocationUpdated` with latitude, longitude, accuracy, speed,
heading, and timestamp properties.

Fidelity can be useful around GeoClue in two ways:

| Direction | Status | Notes |
| --- | --- | --- |
| GeoClue -> fidelity | Possible future sensor. | A `sensors.geoclue` reader could call the GeoClue D-Bus client API and treat the current desktop location as another `gps`-like input. This needs care to avoid permission-agent failures and loops if GeoClue itself is using fidelity as its Wi-Fi provider. |
| fidelity -> GeoClue Wi-Fi source | Practical with the HTTP endpoint. | GeoClue's Wi-Fi source uses a Mozilla/Ichnaea-style network geolocation URL. Pointing that URL at `http://127.0.0.1:8765/v1/geolocate` lets desktop consumers use fidelity's offline cache plus selected online providers through the normal GeoClue path. |

The second shape is the better first integration because it does not require
fidelity to impersonate a D-Bus location service. It also keeps GeoClue's normal
permission and desktop-client model intact.

## gpsd

gpsd is best treated as an input, not as an output. Fidelity already consumes it
through `gpspipe -w -n 10` and parses the first usable `TPV` fix.

Serving fidelity's Wi-Fi/IP estimate back as a fake gpsd device would be
misleading: gpsd's protocol represents receiver data such as TPV/SKY reports from
real GNSS/AIS-style sources, while fidelity's result can be a fused Wi-Fi/BLE/cell
or IP estimate. Applications that need fidelity's fused result should use
`fidelity-serve` instead.

## Data submission

Ichnaea also documents geosubmit APIs for uploading known-location observations
for Wi-Fi, Bluetooth, and cell networks. Fidelity should not auto-submit by
default: submissions need an explicit known-good location and can publish nearby
radio identifiers. A future `fidelity-submit` command could be useful if it takes
an explicit GPS fix, an explicit endpoint, and a dry-run mode.

## Suggested priority

| Priority | Work | Why |
| --- | --- | --- |
| 1 | HTTP `/v1/geolocate` endpoint | Makes fidelity immediately usable by local tools and GeoClue-style clients without new dependencies. |
| 2 | GeoClue configuration docs | Lets desktop apps consume fidelity through the existing Linux location broker. |
| 3 | Optional GeoClue sensor | Useful only when another provider already gives GeoClue a better location than fidelity can compute itself. |
| 4 | Explicit geosubmit command | Useful for building a database, but only with strong opt-in and privacy guardrails. |

## Sources

* GeoClue Manager, Client, and Location D-Bus APIs:
  <https://www.freedesktop.org/software/geoclue/docs/>
* gpsd JSON protocol, including `WATCH`, `POLL`, and `TPV` reports:
  <https://gpsd.gitlab.io/gpsd/gpsd_json.html>
* Ichnaea geolocate and geosubmit APIs:
  <https://ichnaea.readthedocs.io/en/stable/api/>
