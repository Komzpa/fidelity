import datetime
import gzip
import json
import struct
import threading
import time
import urllib.error
import urllib.request
from unittest.mock import patch

import databases
import bin_retile
import locate as locate_module
import observations
import server as server_module
import sensors.ble
import sensors.gpsd
import sensors.iwlist
import sensors.modemmanager
import sensors.nmea
import sensors.nmcli
from databases.algo.weighted_storage_cube import shelf
from databases.algo.wifi_ap_average import signal_distance, wifi_ap_average
from databases.offline import binary, gps, postgres, rediscache, spatial
from databases.offline.timezone import _parse_zone_tab_coords
from databases.online.free import beacondb, ip_api, ipapi
from databases.online.keyed import abstractapi, combain, google_geolocation, here, maxmind2
from databases.online.keyed import opencellid
from databases.online.keyed import unwiredlabs, yandex
from locate import filter_accuracy, format_link


def _serve_test_server(http_server):
    thread = threading.Thread(target=http_server.serve_forever)
    thread.daemon = True
    thread.start()
    return thread


def test_mac_and_ip_helpers():
    assert databases.mac2int("aa:bb:cc:00:11:22") == 0xAABBCC001122
    assert databases.mac2str("aa-bb-cc-00-11-22") == "AABBCC001122"
    assert databases.ip2int("192.168.1.1") == 0xC0A80101
    assert databases.ip2key("192.168.1.1", 8).startswith("ip:")


def test_wifi_average_accepts_legacy_rows():
    result = wifi_ap_average([[30.0, 10.0, 10], [30.0001, 10.0001, 20]])

    assert result["longitude"] > 30.0
    assert result["latitude"] > 10.0
    assert result["accuracy"] >= 0


def test_wifi_signal_distance_ports_tpv_regression():
    fresh = signal_distance(-60, lag_s=0)
    stale = signal_distance(-60, lag_s=3)

    assert 26 < fresh < 27
    assert stale == fresh + 30


def test_wifi_average_uses_signal_radius_and_geometric_median():
    result = wifi_ap_average(
        [
            {"longitude": 30.0, "latitude": 10.0, "ss": -40},
            {"longitude": 30.0001, "latitude": 10.0001, "ss": -55, "age": 2000},
            {"longitude": 31.0, "latitude": 11.0, "ss": -95},
        ]
    )

    assert result["longitude"] < 30.01
    assert result["latitude"] < 10.01
    assert result["accuracy"] > 20


def test_wifi_average_accepts_gps_prior_without_overriding_wifi():
    result = wifi_ap_average(
        [
            {"longitude": 30.0, "latitude": 10.0, "ss": -60},
            {"longitude": 30.0001, "latitude": 10.0001, "ss": -58},
        ],
        prior={"longitude": 30.00005, "latitude": 10.00005, "accuracy": 5},
    )

    assert 30.0 <= result["longitude"] <= 30.0001
    assert 10.0 <= result["latitude"] <= 10.0001
    assert result["accuracy"] >= 5


def test_shelf_round_trip_uses_bytes():
    points = shelf()
    points.add_point({"longitude": 30.0, "latitude": 10.0, "time": 1, "accuracy": 5})

    payload = points.dumps()
    assert isinstance(payload, bytes)

    loaded = shelf()
    loaded.loads(payload)
    assert loaded.get_average()["count"] == 1


def test_shelf_round_trip_preserves_multiple_cubes():
    now = time.time()
    points = shelf()
    points.add_point({"longitude": 30.0, "latitude": 10.0, "time": now, "accuracy": 5})
    points.add_point({"longitude": 31.0, "latitude": 11.0, "time": now + 1, "accuracy": 7})

    loaded = shelf()
    loaded.loads(points.dumps())
    average = loaded.get_average()

    assert average["count"] == 2
    assert 30.0 < average["longitude"] < 31.0
    assert average["accuracy"] > 7


def test_shelf_validity_reflects_live_cubes():
    points = shelf()
    assert points.is_valid() is False

    points.add_point({"longitude": 30.0, "latitude": 10.0, "time": time.time(), "accuracy": 5})

    assert points.is_valid() is True


def test_binary_cache_reads_records(tmp_path):
    cache_file = tmp_path / "wifi.bin"
    cache_file.write_bytes(struct.pack("!qff", databases.mac2int("aa:bb:cc:00:11:22"), 30.0, 10.0))

    rows = list(binary.readwifi(["wifi.bin"], directory=str(tmp_path)))
    assert rows == [(databases.mac2int("aa:bb:cc:00:11:22"), (30.0, 10.0))]


def test_binary_cache_absence_is_not_fatal(tmp_path):
    binary.CACHE = {}

    assert list(binary.readwifi(directory=str(tmp_path / "missing"))) == []
    assert binary.get_location({"wifi": []}) is False


def test_cache_save_creates_parent_directory(tmp_path):
    output = tmp_path / "nested" / "wifi.bin"

    from databases.offline import cache

    cache.savewifi(("aa:bb:cc:00:11:22", 30.0, 10.0), str(output))

    assert output.exists()


def test_observation_record_writes_log_and_gps_anchored_wifi_cache(tmp_path):
    log_path = tmp_path / "observations.jsonl"
    cache_path = tmp_path / "wifi.bin"
    sensor_state = {
        "gps": {"latitude": 10.620444, "longitude": 30.589448, "accuracy": 9},
        "wifi": [
            {"mac": "aa:bb:cc:00:11:22", "ssid": "inside", "ss": -63},
            {"mac": "aa:bb:cc:00:11:33", "ssid": "hidden_nomap", "ss": -57},
        ],
        "ble": [{"mac": "aa:bb:cc:00:11:44", "name": "example", "rssi": -81}],
    }

    result = observations.write_observation(
        sensor_state,
        log_path=str(log_path),
        wifi_cache_path=str(cache_path),
    )

    assert result == {"recorded": True, "wifi_cache_rows": 1, "spatial_cache_pairs": 0}
    record = json.loads(log_path.read_text().splitlines()[0])
    assert record["anchor"]["source"] == "gps"
    assert record["wifi"][0]["mac"] == "aa:bb:cc:00:11:22"
    assert record["ble"][0]["mac"] == "aa:bb:cc:00:11:44"
    assert list(binary.readwifi(["wifi.bin"], directory=str(tmp_path))) == [
        (databases.mac2int("aa:bb:cc:00:11:22"), (30.589448928833008, 10.620444297790527))
    ]


def test_observation_record_logs_without_learning_from_poor_gps(tmp_path):
    log_path = tmp_path / "observations.jsonl"
    cache_path = tmp_path / "wifi.bin"
    result = observations.write_observation(
        {
            "gps": {"latitude": 10.620444, "longitude": 30.589448, "accuracy": 500},
            "wifi": [{"mac": "aa:bb:cc:00:11:22", "ss": -63}],
        },
        log_path=str(log_path),
        wifi_cache_path=str(cache_path),
        max_cache_accuracy=100,
    )

    assert result == {"recorded": True, "wifi_cache_rows": 0, "spatial_cache_pairs": 0}
    assert log_path.exists()
    assert not cache_path.exists()


def test_observation_log_feeds_spatial_pair_cache(tmp_path, monkeypatch):
    log_path = tmp_path / "observations.jsonl"
    cache_path = tmp_path / "wifi.bin"
    sensor_state = {
        "gps": {
            "latitude": 10.620444,
            "longitude": 30.589448,
            "accuracy": 9,
            "heading": 75,
        },
        "wifi": [
            {"mac": "aa:bb:cc:00:11:22", "ssid": "inside", "ss": -48},
            {"mac": "aa:bb:cc:00:11:33", "ssid": "inside2", "ss": -52},
        ],
    }

    result = observations.write_observation(
        sensor_state,
        log_path=str(log_path),
        wifi_cache_path=str(cache_path),
    )

    assert result == {"recorded": True, "wifi_cache_rows": 2, "spatial_cache_pairs": 1}

    monkeypatch.setenv("FIDELITY_SPATIAL_OBSERVATION_LOG", str(log_path))
    spatial.CACHE.update({"path": None, "mtime": None, "pairs": {}})
    location = spatial.get_location(
        {
            "gps": {"heading": 80, "accuracy": 1000, "latitude": 10.7, "longitude": 30.7},
            "wifi": [
                {"mac": "AA-BB-CC-00-11-22", "ss": -50},
                {"mac": "AA-BB-CC-00-11-33", "ss": -55},
            ],
        }
    )

    assert location["service"] == "spatial Wi-Fi observation cache"
    assert location["position"]["type"] == "wifi"
    assert abs(location["position"]["latitude"] - 10.620444) < 0.000001
    assert abs(location["position"]["longitude"] - 30.589448) < 0.000001


def test_observation_pair_cache_can_run_without_binary_cache(tmp_path):
    result = observations.write_observation(
        {
            "gps": {
                "latitude": 10.620444,
                "longitude": 30.589448,
                "accuracy": 9,
                "heading": 75,
            },
            "wifi": [
                {"mac": "aa:bb:cc:00:11:22", "ssid": "inside", "ss": -48},
                {"mac": "aa:bb:cc:00:11:33", "ssid": "inside2", "ss": -52},
            ],
        },
        log_path=str(tmp_path / "observations.jsonl"),
        wifi_cache_path=str(tmp_path / "wifi.bin"),
        write_wifi_cache=False,
    )

    assert result == {"recorded": True, "wifi_cache_rows": 0, "spatial_cache_pairs": 1}
    assert (tmp_path / "observations.jsonl").exists()
    assert not (tmp_path / "wifi.bin").exists()


def test_spatial_pair_cache_uses_heading_buckets():
    pairs = spatial.build_pair_cache(
        [
            {
                "anchor": {
                    "source": "gps",
                    "latitude": 10.0,
                    "longitude": 30.0,
                    "accuracy": 8,
                    "heading": 20,
                },
                "wifi": [
                    {"mac": "aa:bb:cc:00:11:22", "ss": -40},
                    {"mac": "aa:bb:cc:00:11:33", "ss": -45},
                ],
            },
            {
                "anchor": {
                    "source": "gps",
                    "latitude": 11.0,
                    "longitude": 31.0,
                    "accuracy": 8,
                    "heading": 180,
                },
                "wifi": [
                    {"mac": "aa:bb:cc:00:11:22", "ss": -40},
                    {"mac": "aa:bb:cc:00:11:33", "ss": -45},
                ],
            },
        ]
    )

    candidates = spatial.pair_candidates(
        {
            "gps": {"heading": 30},
            "wifi": [
                {"mac": "aa:bb:cc:00:11:22", "ss": -42},
                {"mac": "aa:bb:cc:00:11:33", "ss": -47},
            ],
        },
        pairs=pairs,
    )

    assert [candidate["latitude"] for candidate in candidates] == [10.0, 10.5]
    assert candidates[0]["ss"] > candidates[1]["ss"]


def test_spatial_pair_cache_adds_all_heading_geometry():
    pairs = spatial.build_pair_cache(
        [
            {
                "anchor": {
                    "source": "gps",
                    "latitude": 10.0,
                    "longitude": 30.0,
                    "accuracy": 8,
                    "heading": 20,
                },
                "wifi": [
                    {"mac": "aa:bb:cc:00:11:22", "ss": -40},
                    {"mac": "aa:bb:cc:00:11:33", "ss": -45},
                ],
            },
            {
                "anchor": {
                    "source": "gps",
                    "latitude": 10.0002,
                    "longitude": 30.0002,
                    "accuracy": 8,
                    "heading": 80,
                },
                "wifi": [
                    {"mac": "aa:bb:cc:00:11:22", "ss": -40},
                    {"mac": "aa:bb:cc:00:11:33", "ss": -45},
                ],
            },
        ]
    )

    key = next(iter(pairs))
    assert [sample["heading_bucket"] for sample in pairs[key]] == [0, 60, -360]

    candidates = spatial.pair_candidates(
        {
            "wifi": [
                {"mac": "aa:bb:cc:00:11:22", "ss": -42},
                {"mac": "aa:bb:cc:00:11:33", "ss": -47},
            ],
        },
        pairs=pairs,
    )

    assert len(candidates) == 1
    assert 10.0 < candidates[0]["latitude"] < 10.0002


def test_spatial_materialized_cache_is_used_before_observation_log(tmp_path, monkeypatch):
    observations_path = tmp_path / "observations.jsonl"
    cache_path = tmp_path / "spatial.json"
    observations_path.write_text(
        json.dumps(
            {
                "anchor": {
                    "source": "gps",
                    "latitude": 10.0,
                    "longitude": 30.0,
                    "accuracy": 8,
                    "heading": 20,
                },
                "wifi": [
                    {"mac": "aa:bb:cc:00:11:22", "ss": -40},
                    {"mac": "aa:bb:cc:00:11:33", "ss": -45},
                ],
            }
        )
        + "\n"
    )

    result = spatial.write_materialized_cache(
        observation_log=str(observations_path),
        output_path=str(cache_path),
    )

    assert spatial.CACHE == {"path": None, "mtime": None, "pairs": {}}
    assert result == {
        "compact_samples": 2,
        "pair_count": 1,
        "path": str(cache_path),
        "raw_pair_samples": 1,
        "records_read": 1,
        "records_used": 1,
        "wifi_rows": 2,
    }
    observations_path.write_text("")
    monkeypatch.setenv("FIDELITY_SPATIAL_OBSERVATION_LOG", str(observations_path))
    monkeypatch.setenv("FIDELITY_SPATIAL_CACHE", str(cache_path))
    spatial.CACHE.update({"path": None, "mtime": None, "pairs": {}})

    candidates = spatial.pair_candidates(
        {
            "gps": {"heading": 30},
            "wifi": [
                {"mac": "aa:bb:cc:00:11:22", "ss": -42},
                {"mac": "aa:bb:cc:00:11:33", "ss": -47},
            ],
        }
    )

    assert candidates[0]["latitude"] == 10.0


def test_spatial_invalid_materialized_cache_falls_back_to_observation_log(tmp_path, monkeypatch):
    observations_path = tmp_path / "observations.jsonl"
    cache_path = tmp_path / "spatial.json"
    observations_path.write_text(
        json.dumps(
            {
                "anchor": {
                    "source": "gps",
                    "latitude": 10.0,
                    "longitude": 30.0,
                    "accuracy": 8,
                    "heading": 20,
                },
                "wifi": [
                    {"mac": "aa:bb:cc:00:11:22", "ss": -40},
                    {"mac": "aa:bb:cc:00:11:33", "ss": -45},
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    cache_path.write_text('{"version":999,"pairs":{}}', encoding="utf-8")
    monkeypatch.setenv("FIDELITY_SPATIAL_OBSERVATION_LOG", str(observations_path))
    monkeypatch.setenv("FIDELITY_SPATIAL_CACHE", str(cache_path))
    spatial.CACHE.update({"path": None, "mtime": None, "pairs": {}})

    candidates = spatial.pair_candidates(
        {
            "gps": {"heading": 30},
            "wifi": [
                {"mac": "aa:bb:cc:00:11:22", "ss": -42},
                {"mac": "aa:bb:cc:00:11:33", "ss": -47},
            ],
        }
    )

    assert candidates[0]["latitude"] == 10.0
    assert spatial.CACHE["path"] == str(observations_path)


def test_spatial_materialized_cache_reads_gzip_tpv_wifi_dump(tmp_path, monkeypatch):
    dump_path = tmp_path / "observations.jsonl.gz"
    cache_path = tmp_path / "spatial.json.gz"
    with gzip.open(dump_path, mode="wt", encoding="utf-8") as out:
        out.write(
            json.dumps(
                {
                    "event_ts": 1_500_000_100_000,
                    "tpv": {
                        "src": "gps",
                        "lat": 10.25,
                        "lon": 30.75,
                        "acc": 12,
                        "hdg": 95,
                        "ts": 1_500_000_099_500,
                    },
                    "wifi": [
                        {
                            "mac": "aa:bb:cc:00:11:22",
                            "ssid": "lab",
                            "ss": -42,
                            "ts": 1_500_000_099_000,
                        },
                        {
                            "mac": "aa:bb:cc:00:11:33",
                            "ssid": "lab-2",
                            "ss": -50,
                            "ts": 1_500_000_099_250,
                        },
                    ],
                }
            )
            + "\n"
        )

    result = spatial.write_materialized_cache(
        observation_log=[str(dump_path)],
        output_path=str(cache_path),
    )

    assert result == {
        "compact_samples": 2,
        "pair_count": 1,
        "path": str(cache_path),
        "raw_pair_samples": 1,
        "records_read": 1,
        "records_used": 1,
        "wifi_rows": 2,
    }
    with gzip.open(cache_path, mode="rt", encoding="utf-8") as stream:
        payload = json.load(stream)
    assert payload["version"] == 2
    assert payload["source_count"] == 1
    assert payload["stats"]["records_read"] == 1
    packed_samples = next(iter(payload["pairs"].values()))
    assert packed_samples == [[30.75, 10.25, 12.0, 0, 60, 1], [30.75, 10.25, 12.0, 0, -360, 1]]

    monkeypatch.setenv("FIDELITY_SPATIAL_CACHE", str(cache_path))
    monkeypatch.setenv("FIDELITY_SPATIAL_OBSERVATION_LOG", str(tmp_path / "missing.jsonl"))
    spatial.CACHE.update({"path": None, "mtime": None, "pairs": {}})
    location = spatial.get_location(
        {
            "gps": {"heading": 100},
            "wifi": [
                {"mac": "AA-BB-CC-00-11-22", "ss": -43},
                {"mac": "AA-BB-CC-00-11-33", "ss": -51},
            ],
        }
    )

    assert location["service"] == "spatial Wi-Fi observation cache"
    assert location["position"]["type"] == "wifi"


def test_spatial_gzip_level_is_clamped(monkeypatch):
    monkeypatch.setenv("FIDELITY_SPATIAL_GZIP_LEVEL", "99")
    assert spatial.spatial_gzip_level() == 9

    monkeypatch.setenv("FIDELITY_SPATIAL_GZIP_LEVEL", "-4")
    assert spatial.spatial_gzip_level() == 0

    monkeypatch.setenv("FIDELITY_SPATIAL_GZIP_LEVEL", "not-a-number")
    assert spatial.spatial_gzip_level() == spatial.DEFAULT_SPATIAL_GZIP_LEVEL

    assert spatial.spatial_gzip_level(12) == 9
    assert spatial.spatial_gzip_level(-1) == 0


def test_spatial_materialized_cache_accepts_explicit_gzip_level(tmp_path, monkeypatch):
    dump_path = tmp_path / "observations.jsonl"
    cache_path = tmp_path / "spatial.json.gz"
    dump_path.write_text(
        json.dumps(
            {
                "anchor": {
                    "source": "gps",
                    "latitude": 10.25,
                    "longitude": 30.75,
                    "accuracy": 12,
                },
                "wifi": [
                    {"mac": "aa:bb:cc:00:11:22", "ss": -42},
                    {"mac": "aa:bb:cc:00:11:33", "ss": -50},
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )

    seen_gzip_levels = []
    original_open_json_output = spatial._open_json_output

    def capture_open_json_output(path, gzip_level=None):
        seen_gzip_levels.append(gzip_level)
        return original_open_json_output(path, gzip_level=gzip_level)

    monkeypatch.setattr(spatial, "_open_json_output", capture_open_json_output)

    result = spatial.write_materialized_cache(
        observation_log=[str(dump_path)],
        output_path=str(cache_path),
        gzip_level=8,
    )

    assert result["pair_count"] == 1
    assert seen_gzip_levels == [8]
    with gzip.open(cache_path, mode="rt", encoding="utf-8") as stream:
        assert json.load(stream)["version"] == 2


def test_spatial_materialized_cache_reads_legacy_v1_payload(tmp_path, monkeypatch):
    cache_path = tmp_path / "spatial-v1.json"
    cache_path.write_text(
        json.dumps(
            {
                "version": 1,
                "pairs": {
                    "AABBCC001122:AABBCC001133": [
                        {
                            "longitude": 30.75,
                            "latitude": 10.25,
                            "accuracy": 12,
                            "altitude": 0,
                            "heading_bucket": 60,
                            "count": 1,
                        }
                    ]
                },
            }
        )
        + "\n"
    )

    monkeypatch.setenv("FIDELITY_SPATIAL_CACHE", str(cache_path))
    monkeypatch.setenv("FIDELITY_SPATIAL_OBSERVATION_LOG", str(tmp_path / "missing.jsonl"))
    spatial.CACHE.update({"path": None, "mtime": None, "pairs": {}})
    location = spatial.get_location(
        {
            "gps": {"heading": 100},
            "wifi": [
                {"mac": "AA-BB-CC-00-11-22", "ss": -43},
                {"mac": "AA-BB-CC-00-11-33", "ss": -51},
            ],
        }
    )

    assert location["service"] == "spatial Wi-Fi observation cache"


def test_observation_record_can_use_estimated_anchor_for_log_only(tmp_path):
    log_path = tmp_path / "observations.jsonl"
    cache_path = tmp_path / "wifi.bin"
    result = observations.write_observation(
        {"wifi": [{"mac": "aa:bb:cc:00:11:22", "ss": -63}]},
        [
            {
                "position": {"latitude": 10.620444, "longitude": 30.589448, "accuracy": 90},
                "service": "binary offline cache",
            }
        ],
        log_path=str(log_path),
        wifi_cache_path=str(cache_path),
        allow_estimated=True,
    )

    assert result == {"recorded": True, "wifi_cache_rows": 0, "spatial_cache_pairs": 0}
    record = json.loads(log_path.read_text())
    assert record["anchor"]["source"] == "binary offline cache"
    assert not cache_path.exists()


def test_observation_env_requires_opt_in(monkeypatch):
    monkeypatch.delenv("FIDELITY_RECORD_OBSERVATIONS", raising=False)

    assert observations.write_observation_from_env({"gps": {"latitude": 10, "longitude": 30}}) == {
        "recorded": False,
        "wifi_cache_rows": 0,
        "spatial_cache_pairs": 0,
    }


def test_retile_accepts_binary_cache_rows(tmp_path):
    source = tmp_path / "wifi.bin"
    source.write_bytes(struct.pack("!qff", databases.mac2int("aa:bb:cc:00:11:22"), 30.0, 10.0))
    index = tmp_path / "index.json"

    bin_retile.retile(directory=str(tmp_path), index_filename=str(index))

    assert index.exists()
    tiles = list(tmp_path.glob("wifi.z*.tile.bin"))
    assert len(tiles) == 1
    assert list(binary.readwifi([tiles[0].name], directory=str(tmp_path))) == [
        (databases.mac2int("aa:bb:cc:00:11:22"), (30.0, 10.0))
    ]
    assert json.loads(index.read_text()) == [
        {
            "count": 1,
            "filename": tiles[0].name,
            "bbox": [30.0, 10.0, 30.0, 10.0],
            "minmac": databases.mac2int("aa:bb:cc:00:11:22"),
            "maxmac": databases.mac2int("aa:bb:cc:00:11:22"),
        }
    ]


def test_retile_is_repeatable_and_overwrites_tiles(tmp_path):
    source = tmp_path / "wifi.bin"
    source.write_bytes(
        b"".join(
            [
                struct.pack("!qff", databases.mac2int("aa:bb:cc:00:11:22"), 30.0, 10.0),
                struct.pack("!qff", databases.mac2int("aa:bb:cc:00:11:33"), 31.0, 11.0),
            ]
        )
    )
    index = tmp_path / "index.json"

    bin_retile.retile(directory=str(tmp_path), index_filename=str(index))
    bin_retile.retile(directory=str(tmp_path), index_filename=str(index))

    tiles = list(tmp_path.glob("wifi.z*.tile.bin"))
    assert len(tiles) == 1
    rows = list(binary.readwifi([tiles[0].name], directory=str(tmp_path)))
    assert rows == [
        (databases.mac2int("aa:bb:cc:00:11:22"), (30.0, 10.0)),
        (databases.mac2int("aa:bb:cc:00:11:33"), (31.0, 11.0)),
    ]
    assert json.loads(index.read_text())[0]["count"] == 2


def test_retile_drops_stale_tiles_when_cache_has_no_usable_rows(tmp_path):
    source = tmp_path / "wifi.bin"
    source.write_bytes(struct.pack("!qff", databases.mac2int("aa:bb:cc:00:11:22"), 30.0, 10.0))
    index = tmp_path / "index.json"

    bin_retile.retile(directory=str(tmp_path), index_filename=str(index))
    assert list(tmp_path.glob("wifi.z*.tile.bin"))

    source.write_bytes(struct.pack("!qff", databases.mac2int("aa:bb:cc:00:11:22"), 0.0, 0.0))
    bin_retile.retile(directory=str(tmp_path), index_filename=str(index))

    assert list(tmp_path.glob("wifi.z*.tile.bin")) == []
    assert json.loads(index.read_text()) == []


def test_locate_output_helpers():
    good = {
        "position": {"latitude": 10, "longitude": 30, "accuracy": 50},
        "service": "test",
    }
    bad = {
        "position": {"latitude": 11, "longitude": 31, "accuracy": 5000},
        "service": "test",
    }

    is_good, locations = filter_accuracy([bad, None, good], 100)
    assert is_good is True
    assert locations[0] is good
    assert "openstreetmap.org" in format_link(good)


def test_locate_diagnostics_explain_fallback(monkeypatch):
    def sensor():
        return {"wifi": [{"mac": "aa:bb:cc:00:11:22", "ssid": "inside"}]}

    def timezone_provider(req):
        return {
            "position": {
                "latitude": 41.7167,
                "longitude": 44.8167,
                "accuracy": 150000,
            },
            "service": "timezone",
        }

    def online_provider(req):
        return False

    monkeypatch.setattr(locate_module, "SENSORS", (sensor,))
    monkeypatch.setattr(locate_module, "OFFLINE_PROVIDERS", (timezone_provider,))
    monkeypatch.setattr(locate_module, "ONLINE_PROVIDERS", (online_provider,))

    sensor_state, locations, diagnostics = locate_module.locate(
        accuracy=100000,
        collect_diagnostics=True,
    )

    assert sensor_state == {"wifi": [{"mac": "AA:BB:CC:00:11:22", "ssid": "inside"}]}
    assert locations[0]["service"] == "timezone"
    assert diagnostics["online_attempted"] is True
    assert diagnostics["fallback"] == "best location is worse than requested accuracy"
    assert diagnostics["sensors"][0]["status"] == "used"
    assert diagnostics["providers"] == [
        {
            "name": "test_core.timezone_provider",
            "branch": "legacy",
            "status": "location",
            "service": "timezone",
            "accuracy": 150000,
        },
        {
            "name": "test_core.online_provider",
            "branch": "legacy",
            "status": "no_location",
            "service": None,
            "accuracy": None,
        },
    ]


def test_server_maps_geolocate_payload_to_sensor_state():
    state = server_module.sensor_state_from_geolocate(
        {
            "wifiAccessPoints": [
                {
                    "macAddress": "aa:bb:cc:00:11:22",
                    "ssid": "inside",
                    "signalStrength": -63,
                }
            ],
            "bluetoothBeacons": [
                {
                    "macAddress": "aa:bb:cc:00:11:33",
                    "name": "ATC",
                    "signalStrength": -57,
                }
            ],
            "cellTowers": [
                {
                    "cellId": 40,
                    "locationAreaCode": 27837,
                    "mobileCountryCode": 282,
                    "mobileNetworkCode": 1,
                    "radioType": "lte",
                    "signalStrength": -91,
                }
            ],
            "considerIp": True,
        },
        client_ip="203.0.113.9",
    )

    assert state == {
        "wifi": [{"mac": "AA:BB:CC:00:11:22", "ssid": "inside", "ss": -63}],
        "ble": [{"mac": "AA:BB:CC:00:11:33", "name": "ATC", "rssi": -57}],
        "cell": [
            {
                "cellid": 40,
                "lac": 27837,
                "mcc": 282,
                "mnc": 1,
                "radio": "LTE",
                "signal": -91,
            }
        ],
        "ip": "203.0.113.9",
    }


def test_server_geolocate_payload_accepts_mls_extensions():
    state = server_module.sensor_state_from_geolocate(
        {
            "radioType": "nr",
            "wifiAccessPoints": [
                {
                    "macAddress": "aa-bb-cc-00-11-22",
                    "age": 3,
                    "channel": 11,
                    "frequency": 2412,
                    "signalStrength": -63,
                    "signalToNoiseRatio": 13,
                }
            ],
            "bluetoothBeacons": [
                {
                    "macAddress": "aa:bb:cc:00:11:33",
                    "age": 2000,
                    "name": "ATC",
                    "signalStrength": -57,
                }
            ],
            "cellTowers": [
                {
                    "newRadioCellId": 40,
                    "locationAreaCode": 27837,
                    "mobileCountryCode": 282,
                    "mobileNetworkCode": 1,
                    "psc": 3,
                    "timingAdvance": 1,
                    "signalStrength": -91,
                }
            ],
            "considerIp": True,
            "fallbacks": {"ipf": False},
        },
        client_ip="203.0.113.9",
    )

    assert state == {
        "wifi": [
            {
                "mac": "AA-BB-CC-00-11-22",
                "age": 3,
                "channel": 11,
                "frequency": 2412,
                "snr": 13,
                "ss": -63,
            }
        ],
        "ble": [
            {
                "mac": "AA:BB:CC:00:11:33",
                "age": 2000,
                "name": "ATC",
                "rssi": -57,
            }
        ],
        "cell": [
            {
                "cellid": 40,
                "lac": 27837,
                "mcc": 282,
                "mnc": 1,
                "radio": "NR",
                "psc": 3,
                "timing_advance": 1,
                "signal": -91,
            }
        ],
    }


def test_server_geolocate_consider_ip_accepts_string_values():
    state = server_module.sensor_state_from_geolocate(
        {"considerIp": "false"},
        client_ip="203.0.113.9",
    )

    assert state == {}


def test_server_geolocate_endpoint_returns_ichnaea_shape(monkeypatch):
    requests = []

    def fake_provider(req):
        requests.append(req)
        return {
            "position": {
                "type": "mixed",
                "latitude": 10.620444,
                "longitude": 30.589448,
                "accuracy": 90,
            },
            "service": "fake",
        }

    monkeypatch.setattr(server_module, "GEOLOCATE_OFFLINE_PROVIDERS", ())
    monkeypatch.setattr(
        server_module.locate,
        "ONLINE_FREE_PROVIDERS",
        (server_module.locate.Provider(fake_provider, branch="free"),),
    )
    http_server = server_module.FidelityHTTPServer(
        ("127.0.0.1", 0),
        server_module.FidelityHandler,
        online_profile="free",
        offline_only=False,
        quiet=True,
    )
    thread = _serve_test_server(http_server)
    try:
        payload = {
            "wifiAccessPoints": [{"macAddress": "aa:bb:cc:00:11:22", "signalStrength": -63}],
            "considerIp": False,
        }
        request = urllib.request.Request(
            "http://127.0.0.1:%s/geolocation/v1/geolocate?key=test&diagnostics=1"
            % http_server.server_port,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        response = urllib.request.urlopen(request, timeout=5)
        body = json.loads(response.read().decode("utf-8"))
    finally:
        http_server.shutdown()
        http_server.server_close()
        thread.join(timeout=5)

    assert requests == [{"wifi": [{"mac": "AA:BB:CC:00:11:22", "ss": -63}]}]
    assert body["location"] == {"lat": 10.620444, "lng": 30.589448}
    assert body["accuracy"] == 90
    assert body["diagnostics"]["online_attempted"] is True


def test_server_location_endpoint_uses_locate(monkeypatch):
    def fake_locate(include_online, collect_diagnostics, online_profile):
        assert include_online is False
        assert collect_diagnostics is True
        assert online_profile == "keyed"
        return {"wifi": []}, [], {"online_attempted": False}

    monkeypatch.setattr(server_module.locate, "locate", fake_locate)
    http_server = server_module.FidelityHTTPServer(
        ("127.0.0.1", 0),
        server_module.FidelityHandler,
        online_profile="keyed",
        offline_only=True,
        quiet=True,
    )
    thread = _serve_test_server(http_server)
    try:
        response = urllib.request.urlopen(
            "http://127.0.0.1:%s/v1/location?diagnostics=1" % http_server.server_port,
            timeout=5,
        )
        body = json.loads(response.read().decode("utf-8"))
    finally:
        http_server.shutdown()
        http_server.server_close()
        thread.join(timeout=5)

    assert body == {
        "diagnostics": {"online_attempted": False},
        "locations": [],
        "sensors": {"wifi": []},
    }


def test_server_location_endpoint_can_record_observations(monkeypatch, tmp_path):
    def fake_locate(include_online, collect_diagnostics, online_profile):
        return (
            {
                "gps": {"latitude": 10.620444, "longitude": 30.589448, "accuracy": 5},
                "wifi": [{"mac": "aa:bb:cc:00:11:22", "ss": -63}],
            },
            [],
        )

    monkeypatch.setattr(server_module.locate, "locate", fake_locate)
    http_server = server_module.FidelityHTTPServer(
        ("127.0.0.1", 0),
        server_module.FidelityHandler,
        online_profile="free",
        offline_only=True,
        quiet=True,
        record_observations=True,
        observation_log=str(tmp_path / "observations.jsonl"),
        observation_wifi_cache=str(tmp_path / "wifi.bin"),
    )
    thread = _serve_test_server(http_server)
    try:
        response = urllib.request.urlopen(
            "http://127.0.0.1:%s/v1/location" % http_server.server_port,
            timeout=5,
        )
        body = json.loads(response.read().decode("utf-8"))
    finally:
        http_server.shutdown()
        http_server.server_close()
        thread.join(timeout=5)

    assert body["observation"] == {
        "recorded": True,
        "wifi_cache_rows": 1,
        "spatial_cache_pairs": 0,
    }
    assert (tmp_path / "observations.jsonl").exists()
    assert list(binary.readwifi(["wifi.bin"], directory=str(tmp_path)))


def test_server_geolocate_endpoint_returns_404_when_no_location(monkeypatch):
    monkeypatch.setattr(server_module, "GEOLOCATE_OFFLINE_PROVIDERS", ())
    monkeypatch.setattr(server_module.locate, "ONLINE_FREE_PROVIDERS", ())
    http_server = server_module.FidelityHTTPServer(
        ("127.0.0.1", 0),
        server_module.FidelityHandler,
        online_profile="free",
        offline_only=False,
        quiet=True,
    )
    thread = _serve_test_server(http_server)
    try:
        request = urllib.request.Request(
            "http://127.0.0.1:%s/v1/geolocate" % http_server.server_port,
            data=b"{}",
            headers={"Content-Type": "application/json"},
        )
        try:
            urllib.request.urlopen(request, timeout=5)
            assert False, "expected HTTP 404"
        except urllib.error.HTTPError as exc:
            assert exc.code == 404
            body = json.loads(exc.read().decode("utf-8"))
    finally:
        http_server.shutdown()
        http_server.server_close()
        thread.join(timeout=5)

    assert body == {
        "error": {
            "errors": [
                {
                    "domain": "geolocation",
                    "reason": "notFound",
                    "message": "Not found",
                }
            ],
            "code": 404,
            "message": "Not found",
        }
    }


def test_server_geolocate_endpoint_accepts_cors_preflight():
    http_server = server_module.FidelityHTTPServer(
        ("127.0.0.1", 0),
        server_module.FidelityHandler,
        online_profile="free",
        offline_only=True,
        quiet=True,
    )
    thread = _serve_test_server(http_server)
    try:
        request = urllib.request.Request(
            "http://127.0.0.1:%s/v1/geolocate" % http_server.server_port,
            method="OPTIONS",
        )
        response = urllib.request.urlopen(request, timeout=5)
    finally:
        http_server.shutdown()
        http_server.server_close()
        thread.join(timeout=5)

    assert response.status == 204
    assert response.headers["Access-Control-Allow-Origin"] == "*"


def test_server_geolocate_offline_path_uses_local_caches():
    assert server_module.GEOLOCATE_OFFLINE_PROVIDERS == (
        spatial.get_location,
        binary.get_location,
    )


def test_online_profile_splits_free_and_keyed_providers(monkeypatch):
    calls = []

    def sensor():
        return {"ip": "8.8.8.8"}

    def offline_provider(req):
        return {
            "position": {"latitude": 41.7, "longitude": 44.8, "accuracy": 150000},
            "service": "offline",
        }

    def free_provider(req):
        calls.append("free")
        return False

    def keyed_provider(req):
        calls.append("keyed")
        return False

    free = locate_module.Provider(free_provider, branch="free")
    keyed = locate_module.Provider(
        keyed_provider,
        branch="keyed",
        credential_envs=("FIDELITY_TEST_KEY",),
    )

    monkeypatch.delenv("FIDELITY_TEST_KEY", raising=False)
    monkeypatch.setattr(locate_module, "SENSORS", (sensor,))
    monkeypatch.setattr(locate_module, "OFFLINE_PROVIDERS", (offline_provider,))
    monkeypatch.setattr(locate_module, "ONLINE_FREE_PROVIDERS", (free,))
    monkeypatch.setattr(locate_module, "ONLINE_KEYED_PROVIDERS", (keyed,))
    monkeypatch.setattr(locate_module, "ONLINE_PROVIDERS", (free, keyed))

    _, _, diagnostics = locate_module.locate(
        accuracy=100000,
        collect_diagnostics=True,
        online_profile="all",
    )

    assert calls == ["free"]
    assert diagnostics["providers"][1:] == [
        {
            "name": "test_core.free_provider",
            "branch": "free",
            "status": "no_location",
            "service": None,
            "accuracy": None,
        },
        {
            "name": "test_core.keyed_provider",
            "branch": "keyed",
            "status": "skipped_missing_credentials",
            "service": None,
            "accuracy": None,
        },
    ]

    calls.clear()
    monkeypatch.setenv("FIDELITY_TEST_KEY", "secret")
    locate_module.locate(
        accuracy=100000,
        collect_diagnostics=True,
        online_profile="keyed",
    )

    assert calls == ["keyed"]


def test_sensor_state_merges_wifi_sources(monkeypatch):
    def networkmanager():
        return {"wifi": [{"mac": "aa:bb:cc:00:11:22", "ss": 63, "ssid": "inside"}]}

    def iwlist():
        return {
            "wifi": [
                {"mac": "AA:BB:CC:00:11:22", "ss": -52, "ssid": "inside"},
                {"mac": "aa:bb:cc:00:11:33", "ss": -57, "ssid": "inside"},
            ]
        }

    monkeypatch.setattr(locate_module, "SENSORS", (networkmanager, iwlist))

    assert locate_module.get_sensor_state() == {
        "wifi": [
            {"mac": "AA:BB:CC:00:11:22", "ss": -52, "ssid": "inside"},
            {"mac": "AA:BB:CC:00:11:33", "ss": -57, "ssid": "inside"},
        ]
    }


def test_sensor_state_merges_gps_and_clock(monkeypatch):
    def gps_sensor():
        return {
            "clock": {"currentTime": 1700000000000},
            "gps": {
                "latitude": 10.620444,
                "longitude": 30.589448,
                "accuracy": 5,
                "time": 1700000000000,
            },
        }

    monkeypatch.setattr(locate_module, "SENSORS", (gps_sensor,))

    assert locate_module.get_sensor_state() == {
        "clock": {"currentTime": 1700000000000},
        "gps": {
            "latitude": 10.620444,
            "longitude": 30.589448,
            "accuracy": 5,
            "time": 1700000000000,
        },
    }


def test_sensor_state_merges_cell(monkeypatch):
    def cell_sensor():
        return {
            "cell": [
                {
                    "radio": "LTE",
                    "mcc": 282,
                    "mnc": 1,
                    "lac": 27837,
                    "tac": 27837,
                    "cellid": 40,
                }
            ]
        }

    monkeypatch.setattr(locate_module, "SENSORS", (cell_sensor,))

    assert locate_module.get_sensor_state() == {
        "cell": [
            {
                "radio": "LTE",
                "mcc": 282,
                "mnc": 1,
                "lac": 27837,
                "tac": 27837,
                "cellid": 40,
            }
        ]
    }


def test_sensor_state_merges_modem_operator_diagnostics(monkeypatch):
    def modem_sensor():
        return {
            "modem": {
                "model": "Example Modem",
                "state": "failed",
                "state_failed_reason": "sim-missing",
            },
            "operators": [{"operator_code": "28201", "mcc": 282, "mnc": 1}],
        }

    monkeypatch.setattr(locate_module, "SENSORS", (modem_sensor,))

    assert locate_module.get_sensor_state() == {
        "modem": {
            "model": "Example Modem",
            "state": "failed",
            "state_failed_reason": "sim-missing",
        },
        "operators": [{"operator_code": "28201", "mcc": 282, "mnc": 1}],
    }


def test_locate_uses_gps_sensor_before_coarse_fallback(monkeypatch):
    def gps_sensor():
        return {
            "clock": {"currentTime": 1700000000000},
            "gps": {
                "latitude": 10.620444,
                "longitude": 30.589448,
                "accuracy": 5,
                "time": 1700000000000,
            },
        }

    def coarse_provider(req):
        return {
            "position": {
                "latitude": 41.7167,
                "longitude": 44.8167,
                "accuracy": 150000,
            },
            "service": "timezone",
        }

    monkeypatch.setattr(locate_module, "SENSORS", (gps_sensor,))
    monkeypatch.setattr(locate_module, "OFFLINE_PROVIDERS", (gps.get_location, coarse_provider))

    sensor_state, locations, diagnostics = locate_module.locate(
        include_online=False,
        collect_diagnostics=True,
    )

    assert sensor_state["gps"]["accuracy"] == 5
    assert locations[0]["service"] == "gps proxy"
    assert diagnostics["providers"][0]["name"] == "databases.offline.gps.get_location"
    assert diagnostics["providers"][0]["status"] == "location"
    assert diagnostics["fallback"] is None


def test_beacondb_formats_ichnaea_wifi_request():
    request = {
        "wifi": [
            {"mac": "aa:bb:cc:00:11:22", "ss": 63.8, "ssid": "inside"},
            {"mac": "aa:bb:cc:00:11:33"},
            {"mac": "aa:bb:cc:00:11:44", "ss": 40, "ssid": "private_nomap"},
        ]
    }

    assert beacondb._wifi_access_points(request) == [
        {"macAddress": "AA:BB:CC:00:11:22", "signalStrength": -63},
    ]


def test_beacondb_formats_ichnaea_ble_request():
    request = {
        "ble": [
            {"mac": "aa:bb:cc:00:11:22", "rssi": -63, "name": "ATC"},
            {"mac": "aa:bb:cc:00:11:22", "rssi": -70, "name": "duplicate"},
            {"mac": "00:00:00:00:00:00", "rssi": -30},
            {"name": "anonymous"},
        ]
    }

    assert beacondb._bluetooth_beacons(request) == [
        {"macAddress": "AA:BB:CC:00:11:22", "name": "ATC", "signalStrength": -63},
    ]


def test_beacondb_returns_wifi_location(monkeypatch):
    requests = []

    class FakeResponse:
        def read(self):
            return b'{"location": {"lat": 10.620444, "lng": 30.589448}, "accuracy": 272}'

    def fake_urlopen(request, timeout):
        requests.append((request, timeout))
        return FakeResponse()

    monkeypatch.setattr(beacondb.urllib.request, "urlopen", fake_urlopen)

    result = beacondb.get_location(
        {
            "wifi": [
                {"mac": "aa:bb:cc:00:11:22", "ss": 63, "ssid": "inside"},
                {"mac": "aa:bb:cc:00:11:33", "ss": 57, "ssid": "inside"},
            ]
        }
    )

    payload = json.loads(requests[0][0].data.decode("utf-8"))
    assert payload == {
        "considerIp": False,
        "fallbacks": {"ipf": False, "lacf": False},
        "wifiAccessPoints": [
            {"macAddress": "AA:BB:CC:00:11:22", "signalStrength": -63},
            {"macAddress": "AA:BB:CC:00:11:33", "signalStrength": -57},
        ],
    }
    assert requests[0][0].headers["User-agent"].startswith("fidelity/")
    assert requests[0][1] == 5
    assert result == {
        "position": {
            "type": "wifi",
            "latitude": 10.620444,
            "longitude": 30.589448,
            "accuracy": 272,
        },
        "service": "beaconDB",
    }


def test_beacondb_returns_ble_location(monkeypatch):
    requests = []

    class FakeResponse:
        def read(self):
            return b'{"location": {"lat": 10.620444, "lng": 30.589448}, "accuracy": 90}'

    def fake_urlopen(request, timeout):
        requests.append((request, timeout))
        return FakeResponse()

    monkeypatch.setattr(beacondb.urllib.request, "urlopen", fake_urlopen)

    result = beacondb.get_location(
        {
            "ble": [
                {"mac": "aa:bb:cc:00:11:22", "rssi": -63, "name": "ATC"},
                {"mac": "aa:bb:cc:00:11:33", "rssi": -57},
                {"mac": "00:00:00:00:00:00", "rssi": -30},
            ]
        }
    )

    payload = json.loads(requests[0][0].data.decode("utf-8"))
    assert payload == {
        "considerIp": False,
        "fallbacks": {"ipf": False, "lacf": False},
        "bluetoothBeacons": [
            {"macAddress": "AA:BB:CC:00:11:22", "name": "ATC", "signalStrength": -63},
            {"macAddress": "AA:BB:CC:00:11:33", "signalStrength": -57},
        ],
    }
    assert result == {
        "position": {
            "type": "ble",
            "latitude": 10.620444,
            "longitude": 30.589448,
            "accuracy": 90,
        },
        "service": "beaconDB",
    }


def test_zone_tab_coordinate_parser():
    lat, lon = _parse_zone_tab_coords("+4143+04449")

    assert round(lat, 4) == 41.7167
    assert round(lon, 4) == 44.8167


def test_empty_gps_and_legacy_redis_unpack_are_stable():
    assert gps.get_location({}) is False
    assert rediscache.unpack_ll(b"(30.0, 10.0)") == (30.0, 10.0)


def test_redis_lookup_is_optional_when_server_is_unavailable(monkeypatch):
    class BrokenRedis:
        def get(self, key):
            raise rediscache.RedisError("redis unavailable")

    monkeypatch.setattr(rediscache, "r", BrokenRedis())

    assert rediscache.get_location({"wifi": [{"mac": "aa:bb:cc:00:11:22", "ss": -30}]}) is False
    assert rediscache.get_location({"ip": "8.8.8.8"}) is False
    assert rediscache.savewifi("aa:bb:cc:00:11:22", {"longitude": 30, "latitude": 10}) is False
    assert rediscache.saveip("8.8.8.8", {"longitude": 30, "latitude": 10}) is None
    assert rediscache.dropwifi("aa:bb:cc:00:11:22") is None


def test_postgres_lookup_is_optional_when_database_is_unavailable(monkeypatch):
    def unavailable_cursor():
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(postgres, "_cursor", unavailable_cursor)

    assert postgres.get_location({"ip": "8.8.8.8"}) is False


def test_google_geolocation_requires_api_key(monkeypatch):
    monkeypatch.delenv("FIDELITY_GOOGLE_GEOLOCATION_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_GEOLOCATION_API_KEY", raising=False)

    assert google_geolocation.get_location({"wifi": [{"mac": "aa:bb:cc:00:11:22"}]}) is False


def test_google_geolocation_uses_current_api(monkeypatch):
    requests = []

    class FakeResponse:
        def read(self):
            return b'{"location": {"lat": 10.620444, "lng": 30.589448}, "accuracy": 272}'

    def fake_urlopen(request, timeout):
        requests.append((request, timeout))
        return FakeResponse()

    monkeypatch.setenv("FIDELITY_GOOGLE_GEOLOCATION_API_KEY", "secret")
    monkeypatch.setattr(google_geolocation.urllib.request, "urlopen", fake_urlopen)

    result = google_geolocation.get_location(
        {"wifi": [{"mac": "aa:bb:cc:00:11:22", "ss": 63, "ssid": "inside"}]}
    )

    assert requests[0][0].full_url.endswith("?key=secret")
    assert json.loads(requests[0][0].data.decode("utf-8")) == {
        "considerIp": False,
        "wifiAccessPoints": [{"macAddress": "AA:BB:CC:00:11:22", "signalStrength": -63}],
    }
    assert result["service"] == "google geolocation"
    assert result["position"]["accuracy"] == 272


def test_google_geolocation_can_use_cell_towers(monkeypatch):
    requests = []

    class FakeResponse:
        def read(self):
            return b'{"location": {"lat": 10.620444, "lng": 30.589448}, "accuracy": 1200}'

    def fake_urlopen(request, timeout):
        requests.append((request, timeout))
        return FakeResponse()

    monkeypatch.setenv("FIDELITY_GOOGLE_GEOLOCATION_API_KEY", "secret")
    monkeypatch.setattr(google_geolocation.urllib.request, "urlopen", fake_urlopen)

    result = google_geolocation.get_location(
        {"cell": [{"radio": "LTE", "mcc": 282, "mnc": 1, "lac": 27837, "cellid": 40}]}
    )

    assert json.loads(requests[0][0].data.decode("utf-8")) == {
        "considerIp": False,
        "cellTowers": [
            {
                "cellId": 40,
                "locationAreaCode": 27837,
                "mobileCountryCode": 282,
                "mobileNetworkCode": 1,
                "radioType": "lte",
            }
        ],
    }
    assert result["service"] == "google geolocation"
    assert result["position"]["type"] == "cell"


def test_yandex_locator_requires_api_key(monkeypatch):
    monkeypatch.delenv("FIDELITY_YANDEX_LOCATOR_API_KEY", raising=False)
    monkeypatch.delenv("YANDEX_LOCATOR_API_KEY", raising=False)

    assert yandex.get_location({"wifi": [{"mac": "aa:bb:cc:00:11:22"}]}) is False


def test_yandex_locator_uses_current_api(monkeypatch):
    requests = []

    class FakeResponse:
        def read(self):
            return b'{"location": {"point": {"lat": 10.620444, "lon": 30.589448}, "accuracy": 272}}'

    def fake_urlopen(request, timeout):
        requests.append((request, timeout))
        return FakeResponse()

    monkeypatch.setenv("FIDELITY_YANDEX_LOCATOR_API_KEY", "secret")
    monkeypatch.setattr(yandex.urllib.request, "urlopen", fake_urlopen)

    result = yandex.get_location(
        {"wifi": [{"mac": "aa:bb:cc:00:11:22", "ss": -30, "ssid": "inside"}]}
    )

    assert requests[0][0].full_url.endswith("?apikey=secret")
    assert json.loads(requests[0][0].data.decode("utf-8")) == {
        "wifi": [{"bssid": "AA:BB:CC:00:11:22", "age": 0, "signal_strength": -30}]
    }
    assert result["service"] == "yandex locator"
    assert result["position"]["accuracy"] == 272


def test_yandex_locator_can_use_lte_cells(monkeypatch):
    requests = []

    class FakeResponse:
        def read(self):
            return (
                b'{"location": {"point": {"lat": 10.620444, "lon": 30.589448}, "accuracy": 1200}}'
            )

    def fake_urlopen(request, timeout):
        requests.append((request, timeout))
        return FakeResponse()

    monkeypatch.setenv("FIDELITY_YANDEX_LOCATOR_API_KEY", "secret")
    monkeypatch.setattr(yandex.urllib.request, "urlopen", fake_urlopen)

    result = yandex.get_location(
        {
            "cell": [
                {
                    "radio": "LTE",
                    "mcc": 282,
                    "mnc": 1,
                    "lac": 27837,
                    "tac": 27837,
                    "cellid": 40,
                    "signal": -92,
                }
            ]
        }
    )

    assert json.loads(requests[0][0].data.decode("utf-8")) == {
        "cell": [
            {
                "lte": {
                    "mcc": 282,
                    "mnc": 1,
                    "signal_strength": -92,
                    "tac": 27837,
                    "ci": 40,
                }
            }
        ]
    }
    assert result["service"] == "yandex locator"
    assert result["position"]["type"] == "cell"


def test_maxmind_requires_credentials(monkeypatch):
    monkeypatch.delenv("FIDELITY_MAXMIND_ACCOUNT_ID", raising=False)
    monkeypatch.delenv("FIDELITY_MAXMIND_LICENSE_KEY", raising=False)
    monkeypatch.delenv("MAXMIND_ACCOUNT_ID", raising=False)
    monkeypatch.delenv("MAXMIND_LICENSE_KEY", raising=False)

    assert maxmind2.get_location({"ip": "8.8.8.8"}) is False


def test_maxmind_uses_current_geolite_web_service(monkeypatch):
    requests = []

    class FakeResponse:
        def read(self):
            return (
                b'{"location": {"latitude": 10.620444, "longitude": 30.589448, '
                b'"accuracy_radius": 5000}}'
            )

    def fake_urlopen(request, timeout):
        requests.append((request, timeout))
        return FakeResponse()

    monkeypatch.setenv("FIDELITY_MAXMIND_ACCOUNT_ID", "42")
    monkeypatch.setenv("FIDELITY_MAXMIND_LICENSE_KEY", "secret")
    monkeypatch.setattr(maxmind2.urllib.request, "urlopen", fake_urlopen)

    result = maxmind2.get_location({"ip": "8.8.8.8"})

    assert requests[0][0].full_url == "https://geolite.info/geoip/v2.1/city/8.8.8.8"
    assert requests[0][0].headers["Authorization"].startswith("Basic ")
    assert result == {
        "position": {
            "type": "ip",
            "latitude": 10.620444,
            "longitude": 30.589448,
            "accuracy": 5000,
        },
        "service": "maxmind geolite2 city",
    }


def test_here_network_positioning_requires_api_key(monkeypatch):
    monkeypatch.delenv("FIDELITY_HERE_API_KEY", raising=False)
    monkeypatch.delenv("HERE_API_KEY", raising=False)

    assert here.get_location({"wifi": [{"mac": "aa:bb:cc:00:11:22", "ssid": "inside"}]}) is False


def test_here_network_positioning_uses_wlan_api(monkeypatch):
    requests = []

    class FakeResponse:
        def read(self):
            return b'{"location": {"lat": 10.620444, "lng": 30.589448, "accuracy": 272}}'

    def fake_urlopen(request, timeout):
        requests.append((request, timeout))
        return FakeResponse()

    monkeypatch.setenv("FIDELITY_HERE_API_KEY", "secret")
    monkeypatch.setattr(here.urllib.request, "urlopen", fake_urlopen)

    result = here.get_location(
        {
            "wifi": [
                {"mac": "aa:bb:cc:00:11:22", "ss": 63, "ssid": "inside"},
                {"mac": "aa:bb:cc:00:11:33", "ss": 57, "ssid": "inside"},
            ]
        }
    )

    assert requests[0][0].full_url.endswith("?apiKey=secret")
    assert json.loads(requests[0][0].data.decode("utf-8")) == {
        "wlan": [
            {"mac": "AA:BB:CC:00:11:22", "rss": -63},
            {"mac": "AA:BB:CC:00:11:33", "rss": -57},
        ]
    }
    assert result["service"] == "here network positioning"
    assert result["position"]["accuracy"] == 272


def test_unwiredlabs_requires_api_token(monkeypatch):
    monkeypatch.delenv("FIDELITY_UNWIREDLABS_TOKEN", raising=False)
    monkeypatch.delenv("UNWIREDLABS_TOKEN", raising=False)

    assert unwiredlabs.get_location({"wifi": [{"mac": "aa:bb:cc:00:11:22"}]}) is False


def test_unwiredlabs_uses_location_api(monkeypatch):
    requests = []

    class FakeResponse:
        def read(self):
            return b'{"status": "ok", "lat": 10.620444, "lon": 30.589448, "accuracy": 272}'

    def fake_urlopen(request, timeout):
        requests.append((request, timeout))
        return FakeResponse()

    monkeypatch.setenv("FIDELITY_UNWIREDLABS_TOKEN", "secret")
    monkeypatch.setattr(unwiredlabs.urllib.request, "urlopen", fake_urlopen)

    result = unwiredlabs.get_location(
        {"wifi": [{"mac": "aa:bb:cc:00:11:22", "ss": 63, "ssid": "inside"}]}
    )

    assert requests[0][0].full_url == "https://us1.unwiredlabs.com/v2/process.php"
    assert json.loads(requests[0][0].data.decode("utf-8")) == {
        "token": "secret",
        "wifi": [{"bssid": "AA:BB:CC:00:11:22", "signal": -63}],
        "address": 0,
    }
    assert result["service"] == "unwiredlabs"
    assert result["position"]["accuracy"] == 272


def test_unwiredlabs_can_use_cells(monkeypatch):
    requests = []

    class FakeResponse:
        def read(self):
            return b'{"status": "ok", "lat": 10.620444, "lon": 30.589448, "accuracy": 1200}'

    def fake_urlopen(request, timeout):
        requests.append((request, timeout))
        return FakeResponse()

    monkeypatch.setenv("FIDELITY_UNWIREDLABS_TOKEN", "secret")
    monkeypatch.setattr(unwiredlabs.urllib.request, "urlopen", fake_urlopen)

    result = unwiredlabs.get_location(
        {
            "cell": [
                {
                    "radio": "LTE",
                    "mcc": 282,
                    "mnc": 1,
                    "lac": 27837,
                    "cellid": 40,
                    "signal": -92,
                }
            ]
        }
    )

    assert json.loads(requests[0][0].data.decode("utf-8")) == {
        "token": "secret",
        "address": 0,
        "cells": [{"lac": 27837, "cid": 40, "signal": -92}],
        "radio": "lte",
        "mcc": 282,
        "mnc": 1,
    }
    assert result["service"] == "unwiredlabs"
    assert result["position"]["type"] == "cell"


def test_opencellid_requires_api_key(monkeypatch):
    monkeypatch.delenv("FIDELITY_OPENCELLID_API_KEY", raising=False)
    monkeypatch.delenv("OPENCELLID_API_KEY", raising=False)

    assert opencellid.get_location({"cell": [{"mcc": 282, "mnc": 1, "lac": 27837}]}) is False


def test_opencellid_uses_cell_get_api(monkeypatch):
    requests = []

    class FakeResponse:
        def read(self):
            return b'{"lat": 10.620444, "lon": 30.589448, "range": 1200}'

    def fake_urlopen(url, timeout):
        requests.append((url, timeout))
        return FakeResponse()

    monkeypatch.setenv("FIDELITY_OPENCELLID_API_KEY", "secret")
    monkeypatch.setattr(opencellid.urllib.request, "urlopen", fake_urlopen)

    result = opencellid.get_location(
        {"cell": [{"radio": "LTE", "mcc": 282, "mnc": 1, "lac": 27837, "cellid": 40}]}
    )

    assert requests == [
        (
            "https://opencellid.org/cell/get?"
            "key=secret&mcc=282&mnc=1&lac=27837&cellid=40&format=json&radio=LTE",
            5,
        )
    ]
    assert result == {
        "position": {
            "type": "cell",
            "latitude": 10.620444,
            "longitude": 30.589448,
            "accuracy": 1200,
        },
        "service": "opencellid",
    }


def test_combain_requires_api_key(monkeypatch):
    monkeypatch.delenv("FIDELITY_COMBAIN_API_KEY", raising=False)
    monkeypatch.delenv("COMBAIN_API_KEY", raising=False)

    assert combain.get_location({"wifi": [{"mac": "aa:bb:cc:00:11:22"}]}) is False


def test_combain_uses_location_api(monkeypatch):
    requests = []

    class FakeResponse:
        def read(self):
            return b'{"location": {"lat": 10.620444, "lng": 30.589448}, "accuracy": 272}'

    def fake_urlopen(request, timeout):
        requests.append((request, timeout))
        return FakeResponse()

    monkeypatch.setenv("FIDELITY_COMBAIN_API_KEY", "secret")
    monkeypatch.setattr(combain.urllib.request, "urlopen", fake_urlopen)

    result = combain.get_location(
        {
            "wifi": [
                {"mac": "aa:bb:cc:00:11:22", "ss": 63, "ssid": "inside"},
                {"mac": "aa:bb:cc:00:11:33", "ss": 57, "ssid": "inside"},
            ]
        }
    )

    assert requests[0][0].full_url.endswith("?key=secret")
    assert json.loads(requests[0][0].data.decode("utf-8")) == {
        "wifiAccessPoints": [
            {"macAddress": "AA:BB:CC:00:11:22", "ssid": "inside", "signalStrength": -63},
            {"macAddress": "AA:BB:CC:00:11:33", "ssid": "inside", "signalStrength": -57},
        ],
        "fallbacks": {"all": 0},
        "address": 0,
    }
    assert result["service"] == "combain"
    assert result["position"]["accuracy"] == 272


def test_combain_uses_bluetooth_beacons(monkeypatch):
    requests = []

    class FakeResponse:
        def read(self):
            return b'{"location": {"lat": 10.620444, "lng": 30.589448}, "accuracy": 90}'

    def fake_urlopen(request, timeout):
        requests.append((request, timeout))
        return FakeResponse()

    monkeypatch.setenv("FIDELITY_COMBAIN_API_KEY", "secret")
    monkeypatch.setattr(combain.urllib.request, "urlopen", fake_urlopen)

    result = combain.get_location(
        {
            "ble": [
                {"mac": "aa:bb:cc:00:11:22", "rssi": -63, "name": "ATC"},
                {"mac": "aa:bb:cc:00:11:33", "rssi": -57},
                {"mac": "FF:FF:FF:FF:FF:FF", "rssi": -30},
            ]
        }
    )

    assert json.loads(requests[0][0].data.decode("utf-8")) == {
        "fallbacks": {"all": 0},
        "address": 0,
        "bluetoothBeacons": [
            {"macAddress": "AA:BB:CC:00:11:22", "name": "ATC", "signalStrength": -63},
            {"macAddress": "AA:BB:CC:00:11:33", "signalStrength": -57},
        ],
    }
    assert result["service"] == "combain"
    assert result["position"]["type"] == "ble"
    assert result["position"]["accuracy"] == 90


def test_ipapi_uses_caller_ip_json_endpoint(monkeypatch):
    requests = []

    class FakeResponse:
        def read(self):
            return b'{"latitude": 10.620444, "longitude": 30.589448}'

    def fake_urlopen(url, timeout):
        requests.append((url, timeout))
        return FakeResponse()

    monkeypatch.setattr(ipapi.urllib.request, "urlopen", fake_urlopen)

    assert ipapi.get_location({})["service"] == "ipapi.co"
    assert requests == [("https://ipapi.co/json/", 5)]


def test_ipapi_uses_json_endpoint(monkeypatch):
    requests = []

    class FakeResponse:
        def read(self):
            return b'{"latitude": 10.620444, "longitude": 30.589448}'

    def fake_urlopen(url, timeout):
        requests.append((url, timeout))
        return FakeResponse()

    monkeypatch.setattr(ipapi.urllib.request, "urlopen", fake_urlopen)

    result = ipapi.get_location({"ip": "8.8.8.8"})

    assert requests == [("https://ipapi.co/8.8.8.8/json/", 5)]
    assert result == {
        "position": {
            "type": "ip",
            "latitude": 10.620444,
            "longitude": 30.589448,
            "accuracy": 50000,
        },
        "service": "ipapi.co",
    }


def test_ip_api_uses_json_endpoint(monkeypatch):
    requests = []

    class FakeResponse:
        def read(self):
            return b'{"status": "success", "lat": 10.620444, "lon": 30.589448}'

    def fake_urlopen(url, timeout):
        requests.append((url, timeout))
        return FakeResponse()

    monkeypatch.setattr(ip_api.urllib.request, "urlopen", fake_urlopen)

    result = ip_api.get_location({"ip": "8.8.8.8"})

    assert requests == [("http://ip-api.com/json/8.8.8.8?fields=status%2Cmessage%2Clat%2Clon", 5)]
    assert result["service"] == "ip-api.com"
    assert result["position"]["accuracy"] == 50000


def test_abstract_api_requires_api_key(monkeypatch):
    monkeypatch.delenv("FIDELITY_ABSTRACT_API_KEY", raising=False)
    monkeypatch.delenv("ABSTRACT_API_KEY", raising=False)

    assert abstractapi.get_location({"ip": "8.8.8.8"}) is False


def test_abstract_api_uses_ip_geolocation_endpoint(monkeypatch):
    requests = []

    class FakeResponse:
        def read(self):
            return b'{"latitude": 10.620444, "longitude": 30.589448}'

    def fake_urlopen(url, timeout):
        requests.append((url, timeout))
        return FakeResponse()

    monkeypatch.setenv("FIDELITY_ABSTRACT_API_KEY", "secret")
    monkeypatch.setattr(abstractapi.urllib.request, "urlopen", fake_urlopen)

    result = abstractapi.get_location({"ip": "8.8.8.8"})

    assert requests == [
        ("https://ipgeolocation.abstractapi.com/v1/?api_key=secret&ip_address=8.8.8.8", 5)
    ]
    assert result["service"] == "abstract ip geolocation"
    assert result["position"]["accuracy"] == 50000


def test_iwlist_parser_keeps_last_access_point():
    sample = b"""
wlan0     Scan completed :
          Cell 01 - Address: AA:BB:CC:00:11:22
                    Quality=42/100  Signal level=-68 dBm
                    ESSID:"one"
          Cell 02 - Address: AA:BB:CC:00:11:33
                    Quality=70/100  Signal level=-40 dBm
                    ESSID:"two"
"""

    class FakeStdout:
        def read(self):
            return sample

    class FakePopen:
        def __init__(self, *args, **kwargs):
            self.stdout = FakeStdout()

    with patch("subprocess.Popen", FakePopen):
        state = sensors.iwlist.get_state()

    assert state == {
        "wifi": [
            {"mac": "AA:BB:CC:00:11:22", "ss": -68.0, "ssid": "one"},
            {"mac": "AA:BB:CC:00:11:33", "ss": -40.0, "ssid": "two"},
        ]
    }


def test_iwlist_parser_ignores_unsupported_scan_output():
    sample = b"""
lo        Interface doesn't support scanning.
eth0      Interface doesn't support scanning.
"""

    class FakeStdout:
        def read(self):
            return sample

    class FakePopen:
        def __init__(self, *args, **kwargs):
            self.stdout = FakeStdout()

    with patch("subprocess.Popen", FakePopen):
        state = sensors.iwlist.get_state()

    assert state is None


def test_nmcli_parser_reads_escaped_bssid_and_ssid(monkeypatch):
    class FakeResult:
        stdout = "AA\\:BB\\:CC\\:00\\:11\\:22:inside\\:lab:63\n"

    def fake_run(*args, **kwargs):
        return FakeResult()

    monkeypatch.setattr(sensors.nmcli.subprocess, "run", fake_run)

    assert sensors.nmcli.get_state() == {
        "wifi": [{"mac": "AA:BB:CC:00:11:22", "ssid": "inside:lab", "ss": 63.0}]
    }


def test_nmcli_sensor_is_optional_when_command_is_missing(monkeypatch):
    def missing_command(*args, **kwargs):
        raise OSError("nmcli missing")

    monkeypatch.setattr(sensors.nmcli.subprocess, "run", missing_command)

    assert sensors.nmcli.get_state() is None


def test_ble_sensor_is_opt_in(monkeypatch):
    monkeypatch.delenv("FIDELITY_BLE_SCAN", raising=False)

    assert sensors.ble.get_state() is None


def test_ble_scan_parser_reads_rssi_txpower_and_manufacturer_data():
    output = "\n".join(
        [
            "SetDiscoveryFilter success",
            "Discovery started",
            "[\x1b[0;92mNEW\x1b[0m] Device FC:DF:00:64:7A:47 example-ble",
            "[\x1b[0;93mCHG\x1b[0m] Device FC:DF:00:64:7A:47 RSSI: 0xffffffaf (-81)",
            "[\x1b[0;93mCHG\x1b[0m] Device FC:DF:00:64:7A:47 TxPower: 0x0009 (9)",
            "[\x1b[0;93mCHG\x1b[0m] Device FC:DF:00:64:7A:47 AddressType: public",
            "[\x1b[0;93mCHG\x1b[0m] Device FC:DF:00:64:7A:47 ManufacturerData.Key: 0x06a8 (1704)",
            "  01 41 1c 32 47 7a 64 00 df fc 00                 .A.2Gzd....",
        ]
    )

    assert sensors.ble._parse_scan(output) == [
        {
            "mac": "FC:DF:00:64:7A:47",
            "name": "example-ble",
            "rssi": -81,
            "tx_power": 9,
            "address_type": "public",
            "manufacturer_data": {"0x06a8": "01411c32477a6400dffc00"},
        }
    ]


def test_ble_sensor_runs_bluetoothctl_when_enabled(monkeypatch):
    class FakeResult:
        stdout = (
            "[NEW] Device FC:DF:00:64:7A:47 example-ble\n[CHG] Device FC:DF:00:64:7A:47 RSSI: (-81)"
        )

    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        return FakeResult()

    monkeypatch.setenv("FIDELITY_BLE_SCAN", "1")
    monkeypatch.setenv("FIDELITY_BLE_SCAN_SECONDS", "3")
    monkeypatch.setattr(sensors.ble.subprocess, "run", fake_run)

    assert sensors.ble.get_state() == {
        "ble": [{"mac": "FC:DF:00:64:7A:47", "name": "example-ble", "rssi": -81}]
    }
    assert calls[0][0] == ["bluetoothctl", "--timeout", "3", "scan", "le"]
    assert calls[0][1]["timeout"] == 8


def test_gpsd_parser_uses_first_valid_tpv(monkeypatch):
    class FakeResult:
        stdout = "\n".join(
            [
                '{"class": "VERSION"}',
                (
                    '{"class": "TPV", "mode": 3, "lat": 10.620444, "lon": 30.589448, '
                    '"epx": 7.0, "epy": 9.0, "time": "2026-04-30T12:00:00Z", '
                    '"speed": 1.5, "track": 75.0, "altHAE": 12.0}'
                ),
            ]
        )

    def fake_run(*args, **kwargs):
        return FakeResult()

    monkeypatch.setattr(sensors.gpsd.subprocess, "run", fake_run)

    assert sensors.gpsd.get_state() == {
        "gps": {
            "latitude": 10.620444,
            "longitude": 30.589448,
            "accuracy": 9.0,
            "time": 1777550400000,
            "speed": 1.5,
            "heading": 75.0,
            "altitude": 12.0,
        }
    }


def test_gpsd_sensor_is_optional_when_command_is_missing(monkeypatch):
    def missing_command(*args, **kwargs):
        raise OSError("gpspipe missing")

    monkeypatch.setattr(sensors.gpsd.subprocess, "run", missing_command)

    assert sensors.gpsd.get_state() is None


def test_nmea_sensor_is_opt_in(monkeypatch):
    monkeypatch.delenv("FIDELITY_NMEA_DEVICE", raising=False)

    assert sensors.nmea.get_state() is None


def test_nmea_parser_reads_rmc_fix():
    gps = sensors.nmea._gps_from_lines(
        [
            "$GPGSV,1,1,04,01,40,083,41*7E",
            "$GPRMC,120000.00,A,1037.2266,N,03035.3669,E,1.5,0.0,300426,,,A*77",
        ]
    )

    assert gps == {
        "latitude": 10.6204433333333322,
        "longitude": 30.589448333333333,
        "accuracy": 25.0,
        "time": 1777550400000,
        "speed": 0.771666,
        "heading": 0.0,
    }


def test_nmea_parser_reads_gga_fix(monkeypatch):
    class FakeDatetime(datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 4, 30, tzinfo=tz)

    monkeypatch.setattr(sensors.nmea.datetime, "datetime", FakeDatetime)

    gps = sensors.nmea._gps_from_lines(
        ["$GNGGA,120000.00,1037.2266,N,03035.3669,E,1,09,0.9,12.3,M,0.0,M,,*75"]
    )

    assert gps == {
        "latitude": 10.6204433333333322,
        "longitude": 30.589448333333333,
        "accuracy": 5.0,
        "time": 1777550400000,
        "altitude": 12.3,
    }


def test_nmea_sensor_can_send_opt_in_start_command(monkeypatch):
    calls = []

    def fake_read_lines(device, baud, read_seconds, start_command=None):
        calls.append((device, baud, read_seconds, start_command))
        return ["$GPRMC,120000.00,A,1037.2266,N,03035.3669,E,0.0,0.0,300426,,,A*77"]

    monkeypatch.setenv("FIDELITY_NMEA_DEVICE", "/dev/ttyUSB2")
    monkeypatch.setenv("FIDELITY_NMEA_START_COMMAND", "$GPS_START")
    monkeypatch.setattr(sensors.nmea, "_read_lines", fake_read_lines)

    assert sensors.nmea.get_state()["gps"]["latitude"] == 10.6204433333333322
    assert calls == [("/dev/ttyUSB2", 9600, 5.0, "$GPS_START")]


def test_modemmanager_reads_lte_cell_and_gps(monkeypatch):
    class FakeResult:
        def __init__(self, stdout):
            self.stdout = stdout

    def fake_run(args, **kwargs):
        if "--location-get" in args:
            return FakeResult(
                json.dumps(
                    {
                        "modem": {
                            "location": {
                                "3gpp": {
                                    "mcc": "282",
                                    "mnc": "1",
                                    "lac": "--",
                                    "tac": "27837",
                                    "cid": "40",
                                },
                                "gps": {
                                    "latitude": "10.620444",
                                    "longitude": "30.589448",
                                    "altitude": "12.0",
                                    "utc": "2026-04-30T12:00:00Z",
                                },
                            }
                        }
                    }
                )
            )
        if "--signal-get" in args:
            return FakeResult(json.dumps({"modem": {"signal": {"lte": {"rsrp": "-92"}}}}))
        return FakeResult(json.dumps({"modem": {"generic": {"access-technologies": ["lte"]}}}))

    monkeypatch.setattr(sensors.modemmanager.subprocess, "run", fake_run)

    assert sensors.modemmanager.get_state() == {
        "cell": [
            {
                "mcc": 282,
                "mnc": 1,
                "lac": 27837,
                "cellid": 40,
                "tac": 27837,
                "radio": "LTE",
                "signal": -92,
            }
        ],
        "gps": {
            "latitude": 10.620444,
            "longitude": 30.589448,
            "accuracy": 50.0,
            "altitude": 12.0,
            "time": 1777550400000,
        },
    }


def test_modemmanager_sensor_is_optional_when_command_is_missing(monkeypatch):
    def missing_command(*args, **kwargs):
        raise OSError("mmcli missing")

    monkeypatch.setattr(sensors.modemmanager.subprocess, "run", missing_command)

    assert sensors.modemmanager.get_state() is None


def test_modemmanager_retries_location_with_noninteractive_sudo(monkeypatch):
    class FakeResult:
        def __init__(self, stdout="", stderr=""):
            self.stdout = stdout
            self.stderr = stderr

    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        if args[:4] == ["mmcli", "-m", "any", "--location-get"]:
            return FakeResult(
                stderr=(
                    "GDBus.Error:org.freedesktop.ModemManager1.Error.Core.Unauthorized: "
                    "Unauthorized: PolicyKit authorization failed"
                )
            )
        if args[:3] == ["sudo", "-n", "mmcli"] and "--location-get" in args:
            return FakeResult(
                json.dumps(
                    {
                        "modem": {
                            "location": {
                                "gps": {
                                    "latitude": "10.620444",
                                    "longitude": "30.589448",
                                }
                            }
                        }
                    }
                )
            )
        return FakeResult(json.dumps({"modem": {"generic": {"model": "Example Modem"}}}))

    monkeypatch.setattr(sensors.modemmanager.subprocess, "run", fake_run)

    assert sensors.modemmanager.get_state()["gps"] == {
        "latitude": 10.620444,
        "longitude": 30.589448,
        "accuracy": 50.0,
    }
    assert ["sudo", "-n", "mmcli", "-m", "any", "--location-get", "--output-json"] in calls


def test_modemmanager_reads_gps_from_nmea_sentences(monkeypatch):
    class FakeResult:
        def __init__(self, stdout):
            self.stdout = stdout

    def fake_run(args, **kwargs):
        if "--location-get" in args:
            return FakeResult(
                json.dumps(
                    {
                        "modem": {
                            "location": {
                                "gps": {
                                    "latitude": "--",
                                    "longitude": "--",
                                    "nmea": [
                                        (
                                            "$GPRMC,120000.00,A,1037.2266,N,"
                                            "03035.3669,E,0.0,0.0,300426,,,A*77"
                                        )
                                    ],
                                }
                            }
                        }
                    }
                )
            )
        return FakeResult(json.dumps({"modem": {"generic": {"model": "Example Modem"}}}))

    monkeypatch.setattr(sensors.modemmanager.subprocess, "run", fake_run)

    assert sensors.modemmanager.get_state()["gps"] == {
        "latitude": 10.6204433333333322,
        "longitude": 30.589448333333333,
        "accuracy": 25.0,
        "time": 1777550400000,
        "speed": 0.0,
        "heading": 0.0,
    }


def test_modemmanager_can_enable_gps_with_opt_in(monkeypatch):
    class FakeResult:
        def __init__(self, stdout="{}"):
            self.stdout = stdout

    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        return FakeResult(json.dumps({"modem": {"generic": {"model": "Example Modem"}}}))

    monkeypatch.setenv("FIDELITY_MODEMMANAGER_ENABLE_GPS", "1")
    monkeypatch.setattr(sensors.modemmanager.subprocess, "run", fake_run)

    sensors.modemmanager.get_state()

    assert [
        "mmcli",
        "-m",
        "any",
        "--location-enable-gps-nmea",
        "--location-enable-gps-raw",
    ] in calls


def test_modemmanager_can_enable_3gpp_with_opt_in(monkeypatch):
    class FakeResult:
        def __init__(self, stdout="{}"):
            self.stdout = stdout

    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        return FakeResult(json.dumps({"modem": {"generic": {"model": "Example Modem"}}}))

    monkeypatch.setenv("FIDELITY_MODEMMANAGER_ENABLE_3GPP", "1")
    monkeypatch.setattr(sensors.modemmanager.subprocess, "run", fake_run)

    sensors.modemmanager.get_state()

    assert ["mmcli", "-m", "any", "--location-enable-3gpp"] in calls


def test_modemmanager_can_configure_supl_and_agps_with_opt_in(monkeypatch):
    class FakeResult:
        def __init__(self, stdout="{}"):
            self.stdout = stdout

    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        return FakeResult(json.dumps({"modem": {"generic": {"model": "Example Modem"}}}))

    monkeypatch.setenv("FIDELITY_MODEMMANAGER_SUPL_SERVER", "supl.google.com:7276")
    monkeypatch.setenv("FIDELITY_MODEMMANAGER_ENABLE_AGPS_MSB", "1")
    monkeypatch.setenv("FIDELITY_MODEMMANAGER_ENABLE_AGPS_MSA", "1")
    monkeypatch.setattr(sensors.modemmanager.subprocess, "run", fake_run)

    sensors.modemmanager.get_state()

    assert ["mmcli", "-m", "any", "--location-set-supl-server=supl.google.com:7276"] in calls
    assert ["mmcli", "-m", "any", "--location-enable-agps-msb"] in calls
    assert ["mmcli", "-m", "any", "--location-enable-agps-msa"] in calls


def test_modemmanager_setup_commands_include_system_location_knobs():
    assert sensors.modemmanager.setup_commands(
        modem="0",
        supl_server="supl.example.net:7276",
        enable_3gpp=True,
        enable_agps_msa=True,
        enable_agps_msb=True,
        enable_gps=True,
        enable_gps_unmanaged=True,
        gps_refresh_rate=5,
        enable_signals=True,
        assistance_data="/tmp/xtra.bin",
    ) == [
        ["-m", "0", "--location-set-supl-server=supl.example.net:7276"],
        ["-m", "0", "--location-set-gps-refresh-rate=5"],
        ["-m", "0", "--location-set-enable-signal"],
        ["-m", "0", "--location-enable-3gpp"],
        ["-m", "0", "--location-enable-agps-msb"],
        ["-m", "0", "--location-enable-agps-msa"],
        ["-m", "0", "--location-inject-assistance-data=/tmp/xtra.bin"],
        [
            "-m",
            "0",
            "--location-enable-gps-nmea",
            "--location-enable-gps-raw",
            "--location-enable-gps-unmanaged",
        ],
    ]


def test_modemmanager_setup_commands_from_env_include_new_flags(monkeypatch):
    monkeypatch.setenv("FIDELITY_MODEMMANAGER_ENABLE_GPS_UNMANAGED", "1")
    monkeypatch.setenv("FIDELITY_MODEMMANAGER_GPS_REFRESH_RATE", "0")
    monkeypatch.setenv("FIDELITY_MODEMMANAGER_ENABLE_LOCATION_SIGNALS", "yes")
    monkeypatch.setenv("FIDELITY_MODEMMANAGER_ASSISTANCE_DATA", "/tmp/xtra.bin")

    assert sensors.modemmanager.setup_commands_from_env(modem="0") == [
        ["-m", "0", "--location-set-gps-refresh-rate=0"],
        ["-m", "0", "--location-set-enable-signal"],
        ["-m", "0", "--location-inject-assistance-data=/tmp/xtra.bin"],
        ["-m", "0", "--location-enable-gps-unmanaged"],
    ]


def test_modemmanager_setup_cli_is_dry_run_by_default(capsys):
    assert (
        sensors.modemmanager.main(
            [
                "--modem",
                "0",
                "--enable-3gpp",
                "--enable-gps",
                "--gps-unmanaged",
            ]
        )
        == 0
    )

    assert capsys.readouterr().out.splitlines() == [
        "mmcli -m 0 --location-enable-3gpp",
        "mmcli -m 0 --location-enable-gps-nmea --location-enable-gps-raw "
        "--location-enable-gps-unmanaged",
    ]


def test_modemmanager_setup_cli_apply_runs_commands(monkeypatch):
    class FakeResult:
        returncode = 0
        stderr = ""

    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        return FakeResult()

    monkeypatch.setattr(sensors.modemmanager.subprocess, "run", fake_run)

    assert sensors.modemmanager.main(["--apply", "--enable-3gpp"]) == 0
    assert calls == [["mmcli", "-m", "any", "--location-enable-3gpp"]]


def test_modemmanager_reports_safe_status_and_opt_in_operator_scan(monkeypatch):
    class FakeResult:
        def __init__(self, stdout):
            self.stdout = stdout

    def fake_run(args, **kwargs):
        if "--location-get" in args:
            return FakeResult(
                json.dumps(
                    {
                        "modem": {
                            "location": {
                                "3gpp": {
                                    "mcc": "--",
                                    "mnc": "--",
                                    "lac": "--",
                                    "tac": "--",
                                    "cid": "--",
                                },
                                "gps": {
                                    "latitude": "--",
                                    "longitude": "--",
                                },
                            }
                        }
                    }
                )
            )
        if "--location-status" in args:
            return FakeResult(
                json.dumps(
                    {
                        "modem": {
                            "location": {
                                "capabilities": ["3gpp-lac-ci"],
                                "enabled": [],
                            }
                        }
                    }
                )
            )
        if "--3gpp-scan" in args:
            return FakeResult(
                json.dumps(
                    {
                        "modem": {
                            "3gpp": {
                                "scan-networks": [
                                    {
                                        "operator-code": "28201",
                                        "operator-name": "EXAMPLE-CELL",
                                        "access-technologies": "lte",
                                        "availability": "available",
                                    },
                                    (
                                        '{"operator-code":"28202","operator-name":"OTHER",'
                                        '"access-technologies":"umts, gsm",'
                                        '"availability":"forbidden"}'
                                    ),
                                ]
                            }
                        }
                    }
                )
            )
        return FakeResult(
            json.dumps(
                {
                    "modem": {
                        "3gpp": {"imei": "864949030545456"},
                        "generic": {
                            "manufacturer": "EXAMPLE",
                            "model": "Example Modem",
                            "state": "failed",
                            "state-failed-reason": "sim-missing",
                            "power-state": "low",
                        },
                    }
                }
            )
        )

    monkeypatch.setenv("FIDELITY_MODEMMANAGER_SCAN_OPERATORS", "1")
    monkeypatch.setattr(sensors.modemmanager.subprocess, "run", fake_run)

    assert sensors.modemmanager.get_state() == {
        "modem": {
            "manufacturer": "EXAMPLE",
            "model": "Example Modem",
            "state": "failed",
            "state_failed_reason": "sim-missing",
            "power_state": "low",
            "location_capabilities": ["3gpp-lac-ci"],
        },
        "operators": [
            {
                "operator_code": "28201",
                "mcc": 282,
                "mnc": 1,
                "operator_name": "EXAMPLE-CELL",
                "access_technologies": ["lte"],
                "availability": "available",
            },
            {
                "operator_code": "28202",
                "mcc": 282,
                "mnc": 2,
                "operator_name": "OTHER",
                "access_technologies": ["umts", "gsm"],
                "availability": "forbidden",
            },
        ],
    }
