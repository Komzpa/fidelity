#!/usr/bin/env python3
import argparse
import json
import os
from pathlib import Path

import databases.offline.binary


MAX_ROWS_PER_TILE = 200000


def deg2num(lat_deg, lon_deg, zoom):
    n = 2.0**zoom
    xtile = int((lon_deg + 180.0) / 360.0 * n)
    ytile = int((lat_deg + 90.0) / 180.0 * n)
    return (xtile, ytile)


def _source_files(directory):
    return sorted(
        path
        for path in Path(directory).iterdir()
        if path.is_file() and path.suffix == ".bin" and ".tile" not in path.name
    )


def _tile_filename(source, tile):
    return "%s.z%s.x%s.y%s.tile.bin" % (source.stem, tile[0], tile[1], tile[2])


def _remove_existing_tiles(directory, source):
    for tile in Path(directory).glob("%s.z*.x*.y*.tile.bin" % source.stem):
        tile.unlink()


def _index_filename(output, index_filename):
    index_parent = Path(index_filename).parent
    if index_parent == Path(""):
        index_parent = Path(".")
    return os.path.relpath(output, index_parent)


def _write_wifi_rows(rows, output):
    if not rows:
        return
    from databases.offline import cache

    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        output.unlink()
    for row in rows:
        cache.savewifi(row, str(output))


def retile(directory="data/", index_filename="index.json"):
    directory = Path(directory)
    files = _source_files(directory)
    jsindex = []

    for source in files:
        _remove_existing_tiles(directory, source)
        data = list(databases.offline.binary.readwifi([source.name], directory=str(directory)))
        tiles = {
            (0, 0, 0): [
                (mac, lon, lat)
                for mac, (lon, lat) in data
                if ((abs(lat) > 0.0009 or abs(lon) > 0.0009) and mac)
            ]
        }
        while tiles:
            for tile in list(tiles.keys()):
                rows = tiles[tile]
                if not rows:
                    del tiles[tile]
                    continue
                if len(rows) > MAX_ROWS_PER_TILE:
                    print("bisecting", tile)
                    zoom = tile[0] + 1
                    for row in rows:
                        x, y = deg2num(row[2], row[1], zoom)
                        tiles.setdefault((zoom, x, y), []).append(row)
                    del tiles[tile]
                    continue

                print("saving", tile)
                bbox = [
                    rows[0][1],
                    rows[0][2],
                    rows[0][1],
                    rows[0][2],
                ]
                minmac = rows[0][0]
                maxmac = rows[0][0]
                output = directory / _tile_filename(source, tile)
                for row in rows:
                    bbox = [
                        min(bbox[0], row[1]),
                        min(bbox[1], row[2]),
                        max(bbox[2], row[1]),
                        max(bbox[3], row[2]),
                    ]
                    minmac = min(minmac, row[0])
                    maxmac = max(maxmac, row[0])
                _write_wifi_rows(rows, output)
                jsindex.append(
                    {
                        "count": len(rows),
                        "filename": _index_filename(output, index_filename),
                        "bbox": bbox,
                        "minmac": minmac,
                        "maxmac": maxmac,
                    }
                )
                del tiles[tile]
        print(source.name, len(data), len(tiles))

    with open(index_filename, "w") as index:
        json.dump(jsindex, index)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Split binary Wi-Fi caches into tiled cache files."
    )
    parser.add_argument(
        "--directory", default="data/", help="directory with input binary cache files"
    )
    parser.add_argument("--index", default="index.json", help="path for generated JSON tile index")
    args = parser.parse_args(argv)

    retile(directory=args.directory, index_filename=args.index)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
