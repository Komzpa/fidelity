import argparse
import gzip
import itertools
import json
import math
import os
import tempfile

from ..algo import Distance
from ..algo.wifi_ap_average import signal_distance, wifi_ap_average
from .binary import mac2int

DEFAULT_OBSERVATION_LOG = "data/observations.jsonl"
DEFAULT_SPATIAL_CACHE_PATH = "data/spatial_wifi_pairs.json"
ANY_HEADING_BUCKET = -360
HEADING_BUCKET_DEGREES = 60
MATERIALIZED_CACHE_VERSION = 2
DEFAULT_SPATIAL_GZIP_LEVEL = 6
SAMPLE_LONGITUDE = 0
SAMPLE_LATITUDE = 1
SAMPLE_ACCURACY = 2
SAMPLE_ALTITUDE = 3
SAMPLE_HEADING = 4

CACHE = {
    "path": None,
    "mtime": None,
    "pairs": {},
}


def heading_bucket(heading):
    if heading is None:
        return None
    try:
        heading = float(heading) % 360.0
    except (TypeError, ValueError):
        return None
    return int(math.floor(heading / HEADING_BUCKET_DEGREES) * HEADING_BUCKET_DEGREES)


def _heading_candidates(heading):
    bucket = heading_bucket(heading)
    if bucket is None:
        return {ANY_HEADING_BUCKET, None}
    return {bucket, ANY_HEADING_BUCKET, None}


def _normalise_mac(mac):
    if not mac:
        return None
    try:
        return "%012X" % mac2int(str(mac))
    except ValueError:
        return None


def _pair_key(mac1, mac2):
    macs = sorted((_normalise_mac(mac1), _normalise_mac(mac2)))
    if not all(macs) or macs[0] == macs[1]:
        return None
    return ":".join(macs)


def _pair_key_from_normalized(mac1, mac2):
    if not mac1 or not mac2 or mac1 == mac2:
        return None
    if mac1 < mac2:
        return "%s:%s" % (mac1, mac2)
    return "%s:%s" % (mac2, mac1)


def _gps_heading(gps):
    if not gps:
        return None
    for name in ("heading", "course", "track"):
        if gps.get(name) is not None:
            return gps[name]
    return None


def _as_paths(paths):
    if paths is None:
        return [observation_log_path()]
    if isinstance(paths, (str, os.PathLike)):
        return [str(paths)]
    return [str(path) for path in paths]


def _float_or_none(value):
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def max_anchor_accuracy():
    value = os.environ.get(
        "FIDELITY_SPATIAL_MAX_ANCHOR_ACCURACY",
        os.environ.get("FIDELITY_OBSERVATION_MAX_CACHE_ACCURACY", "100"),
    )
    if not value:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return 100.0


def spatial_gzip_level(value=None):
    if value is None:
        value = os.environ.get("FIDELITY_SPATIAL_GZIP_LEVEL", DEFAULT_SPATIAL_GZIP_LEVEL)
    try:
        value = int(value)
    except (TypeError, ValueError):
        return DEFAULT_SPATIAL_GZIP_LEVEL
    return min(max(value, 0), 9)


def _anchor_from_tpv(record):
    tpv = record.get("tpv") or {}
    if tpv.get("src") != "gps":
        return None
    latitude = tpv.get("lat")
    longitude = tpv.get("lon")
    if latitude is None or longitude is None:
        return None
    return {
        "source": "gps",
        "latitude": latitude,
        "longitude": longitude,
        "accuracy": tpv.get("acc"),
        "altitude": tpv.get("alt"),
        "time": tpv.get("ts"),
        "heading": tpv.get("hdg"),
    }


def _record_position(record, max_accuracy=None):
    anchor = record.get("anchor") or _anchor_from_tpv(record) or {}
    if anchor.get("source") != "gps":
        return None
    if anchor.get("latitude") is None or anchor.get("longitude") is None:
        return None
    accuracy = _float_or_none(anchor.get("accuracy")) or 100.0
    if max_accuracy is not None and accuracy > max_accuracy:
        return None
    heading = _gps_heading(anchor)
    if heading is None:
        heading = _gps_heading(record.get("gps") or {})
    return {
        "latitude": anchor["latitude"],
        "longitude": anchor["longitude"],
        "accuracy": accuracy,
        "altitude": anchor.get("altitude", 0),
        "heading_bucket": heading_bucket(heading),
    }


def _record_wifi(record):
    wifi = []
    event_ts = _float_or_none(record.get("event_ts"))
    for row in record.get("wifi") or []:
        mac = _normalise_mac(row.get("mac"))
        if not mac:
            continue
        ssid = row.get("ssid")
        if isinstance(ssid, str) and ssid.endswith("_nomap"):
            continue
        age = row.get("age")
        row_ts = _float_or_none(row.get("ts"))
        if age is None and event_ts is not None and row_ts is not None:
            age = max(event_ts - row_ts, 0)
        wifi.append({"mac": mac, "signal": row.get("ss"), "age": age})
    return wifi


def _iter_pair_samples_from_position_wifi(position, wifi):
    if not position:
        return
    if len(wifi) < 2:
        return

    sample = (
        position["longitude"],
        position["latitude"],
        position["accuracy"],
        position["altitude"],
        position["heading_bucket"],
    )
    for ap1, ap2 in itertools.combinations(wifi, 2):
        key = _pair_key_from_normalized(ap1["mac"], ap2["mac"])
        if not key:
            continue
        yield key, sample


def _pair_samples_from_position_wifi(position, wifi):
    return list(_iter_pair_samples_from_position_wifi(position, wifi))


def _pair_samples_from_record(record, max_accuracy=None):
    position = _record_position(record, max_accuracy=max_accuracy)
    return _pair_samples_from_position_wifi(position, _record_wifi(record))


def _sample_value(sample, field, index, default=None):
    if isinstance(sample, dict):
        return sample.get(field, default)
    return sample[index] if len(sample) > index else default


def _sample_weight(sample):
    accuracy = max(float(_sample_value(sample, "accuracy", SAMPLE_ACCURACY, 100.0) or 100.0), 1.0)
    return max(10000.0 / (accuracy * accuracy), 1.0)


def _compact_sample_dict(sample, heading):
    return {
        "longitude": _sample_value(sample, "longitude", SAMPLE_LONGITUDE),
        "latitude": _sample_value(sample, "latitude", SAMPLE_LATITUDE),
        "accuracy": _sample_value(sample, "accuracy", SAMPLE_ACCURACY, 0),
        "altitude": _sample_value(sample, "altitude", SAMPLE_ALTITUDE, 0) or 0,
        "heading_bucket": heading,
        "count": 1,
    }


def _compact_sample_group(samples, heading):
    if len(samples) == 1:
        return _compact_sample_dict(samples[0], heading)

    total_weight = sum(_sample_weight(sample) for sample in samples)
    latitude = (
        sum(
            _sample_value(sample, "latitude", SAMPLE_LATITUDE) * _sample_weight(sample)
            for sample in samples
        )
        / total_weight
    )
    longitude = (
        sum(
            _sample_value(sample, "longitude", SAMPLE_LONGITUDE) * _sample_weight(sample)
            for sample in samples
        )
        / total_weight
    )
    altitude = (
        sum(
            (_sample_value(sample, "altitude", SAMPLE_ALTITUDE, 0) or 0) * _sample_weight(sample)
            for sample in samples
        )
        / total_weight
    )
    residuals = [
        Distance(
            (longitude, latitude),
            (
                _sample_value(sample, "longitude", SAMPLE_LONGITUDE),
                _sample_value(sample, "latitude", SAMPLE_LATITUDE),
            ),
        )
        + float(_sample_value(sample, "accuracy", SAMPLE_ACCURACY, 0) or 0)
        for sample in samples
    ]
    return {
        "latitude": latitude,
        "longitude": longitude,
        "accuracy": max(residuals),
        "altitude": altitude,
        "heading_bucket": heading,
        "count": len(samples),
    }


def compact_pair_samples(samples):
    if not isinstance(samples, list):
        heading = _sample_value(samples, "heading_bucket", SAMPLE_HEADING)
        compacted = [_compact_sample_dict(samples, heading)]
        if heading is not None:
            compacted.append(_compact_sample_dict(samples, ANY_HEADING_BUCKET))
        return compacted

    by_heading = {}
    heading_specific = []
    for sample in samples:
        heading = _sample_value(sample, "heading_bucket", SAMPLE_HEADING)
        by_heading.setdefault(heading, []).append(sample)
        if heading is not None:
            heading_specific.append(sample)

    compacted = [
        _compact_sample_group(heading_samples, heading)
        for heading, heading_samples in by_heading.items()
    ]
    if heading_specific:
        compacted.append(_compact_sample_group(heading_specific, ANY_HEADING_BUCKET))
    compacted.sort(
        key=lambda sample: (
            sample["heading_bucket"] in (ANY_HEADING_BUCKET, None),
            sample["heading_bucket"] is None,
            sample["heading_bucket"] if sample["heading_bucket"] is not None else 999,
        )
    )
    return compacted


def _store_pair_sample(pairs, key, sample):
    existing = pairs.get(key)
    if existing is None:
        pairs[key] = sample
    elif isinstance(existing, list):
        existing.append(sample)
    else:
        pairs[key] = [existing, sample]


def _empty_stats():
    return {
        "records_read": 0,
        "records_used": 0,
        "wifi_rows": 0,
        "raw_pair_samples": 0,
        "pair_count": 0,
        "compact_samples": 0,
    }


def _collect_pair_samples_with_stats(records, max_accuracy=None):
    pairs = {}
    stats = _empty_stats()
    for record in records:
        stats["records_read"] += 1
        wifi = _record_wifi(record)
        stats["wifi_rows"] += len(wifi)
        position = _record_position(record, max_accuracy=max_accuracy)
        record_sample_count = 0
        for key, sample in _iter_pair_samples_from_position_wifi(position, wifi):
            record_sample_count += 1
            _store_pair_sample(pairs, key, sample)
        if record_sample_count:
            stats["records_used"] += 1
            stats["raw_pair_samples"] += record_sample_count
    return pairs, stats


def build_pair_cache_with_stats(records, max_accuracy=None):
    raw_pairs, stats = _collect_pair_samples_with_stats(
        records,
        max_accuracy=max_accuracy,
    )
    pairs = {key: compact_pair_samples(samples) for key, samples in raw_pairs.items()}
    stats["pair_count"] = len(pairs)
    stats["compact_samples"] = sum(len(samples) for samples in pairs.values())
    return pairs, stats


def build_pair_cache(records, max_accuracy=None):
    pairs, _stats = build_pair_cache_with_stats(records, max_accuracy=max_accuracy)
    return pairs


def _open_record_stream(path):
    if str(path).endswith(".gz"):
        return gzip.open(path, mode="rt", encoding="utf-8")
    return open(path, encoding="utf-8")


def _open_json_input(path):
    if str(path).endswith(".gz"):
        return gzip.open(path, mode="rt", encoding="utf-8")
    return open(path, encoding="utf-8")


def _open_json_output(path, gzip_level=None):
    if str(path).endswith(".gz"):
        return gzip.open(
            path,
            mode="wt",
            encoding="utf-8",
            compresslevel=spatial_gzip_level(gzip_level),
        )
    return open(path, mode="w", encoding="utf-8")


def _read_records(paths):
    for path in _as_paths(paths):
        yield from _read_records_from_path(path)


def _read_records_from_path(path):
    try:
        with _open_record_stream(path) as stream:
            for line in stream:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if isinstance(record, dict):
                    yield record
    except OSError:
        return


def observation_log_path():
    return os.environ.get(
        "FIDELITY_SPATIAL_OBSERVATION_LOG",
        os.environ.get("FIDELITY_OBSERVATION_LOG", DEFAULT_OBSERVATION_LOG),
    )


def materialized_cache_path():
    return os.environ.get("FIDELITY_SPATIAL_CACHE", DEFAULT_SPATIAL_CACHE_PATH)


def _read_materialized_cache(path):
    try:
        with _open_json_input(path) as stream:
            payload = json.load(stream)
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    version = payload.get("version")
    pairs = payload.get("pairs")
    if not isinstance(pairs, dict):
        return None
    if version == 1:
        return pairs
    if version != MATERIALIZED_CACHE_VERSION:
        return None
    return pairs


def _pack_materialized_sample(sample):
    return [
        sample.get("longitude"),
        sample.get("latitude"),
        sample.get("accuracy", 0),
        sample.get("altitude", 0),
        sample.get("heading_bucket"),
        sample.get("count", 1),
    ]


def _unpack_materialized_sample(sample):
    if isinstance(sample, dict):
        return sample
    if not isinstance(sample, list) or len(sample) < 6:
        return {}
    return {
        "longitude": sample[0],
        "latitude": sample[1],
        "accuracy": sample[2],
        "altitude": sample[3],
        "heading_bucket": sample[4],
        "count": sample[5],
    }


def _dump_compact_json(out, value):
    json.dump(value, out, separators=(",", ":"))


def _write_materialized_cache_payload(out, *, source_count, max_accuracy, stats, pairs):
    out.write('{"max_anchor_accuracy":')
    _dump_compact_json(out, max_accuracy)
    out.write(',"pairs":{')
    first_pair = True
    pair_count = 0
    compact_samples = 0
    for key, samples in pairs.items():
        compacted = compact_pair_samples(samples)
        if first_pair:
            first_pair = False
        else:
            out.write(",")
        _dump_compact_json(out, key)
        out.write(":")
        _dump_compact_json(out, [_pack_materialized_sample(sample) for sample in compacted])
        pair_count += 1
        compact_samples += len(compacted)
    stats["pair_count"] = pair_count
    stats["compact_samples"] = compact_samples
    out.write('},"source_count":')
    _dump_compact_json(out, source_count)
    out.write(',"stats":')
    _dump_compact_json(out, stats)
    out.write(',"version":')
    _dump_compact_json(out, MATERIALIZED_CACHE_VERSION)
    out.write("}\n")


def write_materialized_cache(
    *,
    observation_log=None,
    output_path=None,
    max_accuracy=None,
    gzip_level=None,
):
    observation_log = _as_paths(observation_log)
    output_path = output_path or materialized_cache_path()
    if max_accuracy is None:
        max_accuracy = max_anchor_accuracy()

    pairs, stats = _collect_pair_samples_with_stats(
        _read_records(observation_log),
        max_accuracy=max_accuracy,
    )
    output_dir = os.path.dirname(output_path) or "."
    os.makedirs(output_dir, exist_ok=True)
    suffix = ".json.gz" if str(output_path).endswith(".gz") else ".json"
    fd, tmp_path = tempfile.mkstemp(prefix=".spatial_wifi_pairs.", suffix=suffix, dir=output_dir)
    os.close(fd)
    try:
        with _open_json_output(tmp_path, gzip_level=gzip_level) as out:
            _write_materialized_cache_payload(
                out,
                source_count=len(observation_log),
                max_accuracy=max_accuracy,
                stats=stats,
                pairs=pairs,
            )
        os.replace(tmp_path, output_path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
    CACHE.update({"path": None, "mtime": None, "pairs": {}})
    return {"path": output_path, **stats}


def _load_pair_cache_from_observations():
    path = observation_log_path()
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return {}

    if CACHE["path"] == path and CACHE["mtime"] == mtime:
        return CACHE["pairs"]

    pairs = build_pair_cache(_read_records(path), max_accuracy=max_anchor_accuracy())
    CACHE.update({"path": path, "mtime": mtime, "pairs": pairs})
    return pairs


def load_pair_cache(path=None):
    path = path or materialized_cache_path()
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return _load_pair_cache_from_observations()

    if CACHE["path"] == path and CACHE["mtime"] == mtime:
        return CACHE["pairs"]

    pairs = _read_materialized_cache(path)
    if pairs is None:
        if path == materialized_cache_path():
            return _load_pair_cache_from_observations()
        pairs = build_pair_cache(_read_records(path), max_accuracy=max_anchor_accuracy())
    CACHE.update({"path": path, "mtime": mtime, "pairs": pairs})
    return pairs


def _query_wifi(req):
    wifi = []
    for row in req.get("wifi") or []:
        mac = _normalise_mac(row.get("mac"))
        if not mac:
            continue
        wifi.append({"mac": mac, "signal": row.get("ss"), "age": row.get("age")})
    return wifi


def _lag_seconds(row):
    if row.get("age") is None:
        return 0.0
    try:
        return max(float(row["age"]) / 1000.0, 0.0)
    except (TypeError, ValueError):
        return 0.0


def _pair_radius(ap1, ap2):
    radii = []
    for ap in (ap1, ap2):
        radius = signal_distance(ap.get("signal"), lag_s=_lag_seconds(ap))
        if radius is not None:
            radii.append(radius)
    if not radii:
        return 100.0
    return sum(radii) / len(radii)


def _pair_weight(ap1, ap2):
    weights = []
    for ap in (ap1, ap2):
        try:
            signal = float(ap.get("signal"))
        except (TypeError, ValueError):
            continue
        if signal <= 0:
            weights.append(max(100.0 + signal, 1.0))
        else:
            weights.append(max(signal, 1.0))
    if not weights:
        return 1.0
    return sum(weights) / len(weights)


def _heading_multiplier(sample_bucket, query_buckets):
    if sample_bucket not in query_buckets:
        return 0.0
    if sample_bucket is None:
        return 0.6
    if sample_bucket == ANY_HEADING_BUCKET:
        return 0.8
    return 1.4


def _gps_heading_from_req(req):
    return _gps_heading(req.get("gps") or {})


def pair_candidates(req, pairs=None):
    wifi = _query_wifi(req)
    if len(wifi) < 2:
        return []

    pairs = pairs if pairs is not None else load_pair_cache()
    query_buckets = _heading_candidates(_gps_heading_from_req(req))
    candidates = []
    for ap1, ap2 in itertools.combinations(wifi, 2):
        key = _pair_key_from_normalized(ap1["mac"], ap2["mac"])
        if not key:
            continue
        seen_positions = set()
        for packed_sample in pairs.get(key, []):
            sample = _unpack_materialized_sample(packed_sample)
            if sample.get("latitude") is None or sample.get("longitude") is None:
                continue
            multiplier = _heading_multiplier(sample.get("heading_bucket"), query_buckets)
            if multiplier <= 0:
                continue
            position_key = (
                round(float(sample["latitude"]), 7),
                round(float(sample["longitude"]), 7),
            )
            if position_key in seen_positions:
                continue
            seen_positions.add(position_key)
            radius = max(float(sample.get("accuracy") or 0), _pair_radius(ap1, ap2))
            candidates.append(
                {
                    "longitude": sample["longitude"],
                    "latitude": sample["latitude"],
                    "accuracy": sample.get("accuracy", 0),
                    "altitude": sample.get("altitude", 0),
                    "ss": max(_pair_weight(ap1, ap2) * multiplier, 1.0),
                    "radius": radius,
                }
            )
    return candidates


def get_location(req):
    candidates = pair_candidates(req)
    if not candidates:
        return False

    res = wifi_ap_average(candidates, prior=req.get("gps"))
    if not res:
        return False
    res["type"] = "wifi"
    return {"position": res, "service": "spatial Wi-Fi observation cache"}


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Build a compact pairwise/heading Wi-Fi spatial cache from observations."
    )
    parser.add_argument(
        "--observations",
        nargs="+",
        default=observation_log_path(),
        help="input JSONL or JSONL.gz observation log path(s)",
    )
    parser.add_argument(
        "--output",
        default=materialized_cache_path(),
        help="output spatial Wi-Fi pair cache path",
    )
    parser.add_argument(
        "--max-anchor-accuracy",
        type=float,
        default=max_anchor_accuracy(),
        help="maximum GPS anchor accuracy accepted for pair cache learning",
    )
    parser.add_argument(
        "--gzip-level",
        type=int,
        default=None,
        help="gzip compression level for .gz output paths; overrides FIDELITY_SPATIAL_GZIP_LEVEL",
    )
    parser.add_argument("--json", action="store_true", help="print result as JSON")
    args = parser.parse_args(argv)

    result = write_materialized_cache(
        observation_log=args.observations,
        output_path=args.output,
        max_accuracy=args.max_anchor_accuracy,
        gzip_level=args.gzip_level,
    )
    if args.json:
        print(json.dumps(result, sort_keys=True))
    else:
        print("spatial cache pairs: %s, path: %s" % (result["pair_count"], result["path"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
