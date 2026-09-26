#!/usr/bin/env python3

import json
import math
import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

from shapely.geometry import shape, mapping, box


# ============================================================
# CONFIG
# ============================================================

PBF_PATH = Path("raw/central-zone.osm.pbf")

OUTPUT_ROOT = Path("data")
CHUNKS_ROOT = OUTPUT_ROOT / "chunks"
TEMP_ROOT = OUTPUT_ROOT / ".processor_tmp"

CHUNK_SIZE_M = 1000

# Pune processing BBOX
MIN_LON = 73.70
MIN_LAT = 18.40
MAX_LON = 74.05
MAX_LAT = 18.70

PROGRESS_EVERY = 50_000


# ============================================================
# LOCAL METRIC PROJECTION
# ============================================================

LAT0 = (MIN_LAT + MAX_LAT) / 2.0

METERS_PER_DEG_LAT = 111320.0
METERS_PER_DEG_LON = (
    111320.0 * math.cos(math.radians(LAT0))
)


TOTAL_WIDTH_M = (
    MAX_LON - MIN_LON
) * METERS_PER_DEG_LON

TOTAL_HEIGHT_M = (
    MAX_LAT - MIN_LAT
) * METERS_PER_DEG_LAT

GRID_COLS = math.ceil(
    TOTAL_WIDTH_M / CHUNK_SIZE_M
)

GRID_ROWS = math.ceil(
    TOTAL_HEIGHT_M / CHUNK_SIZE_M
)


# ============================================================
# LOG
# ============================================================

def log(message=""):
    print(message, flush=True)


# ============================================================
# COMMAND
# ============================================================

def run_command(command):
    log("")
    log("[CMD] " + " ".join(map(str, command)))

    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    if result.stdout:
        print(
            result.stdout,
            end="",
            flush=True,
        )

    if result.returncode != 0:
        raise RuntimeError(
            "Command failed: "
            + " ".join(map(str, command))
        )


# ============================================================
# CHUNK FUNCTIONS
# ============================================================

def chunk_id(col, row):
    return f"PUNE_{col:04d}_{row:04d}"


def lonlat_to_xy(lon, lat):

    x = (
        lon - MIN_LON
    ) * METERS_PER_DEG_LON

    y = (
        lat - MIN_LAT
    ) * METERS_PER_DEG_LAT

    return x, y


def xy_to_lonlat(x, y):

    lon = (
        MIN_LON
        + x / METERS_PER_DEG_LON
    )

    lat = (
        MIN_LAT
        + y / METERS_PER_DEG_LAT
    )

    return lon, lat


def chunk_bounds(col, row):

    x1 = (
        col * CHUNK_SIZE_M
    )

    y1 = (
        row * CHUNK_SIZE_M
    )

    x2 = min(
        x1 + CHUNK_SIZE_M,
        TOTAL_WIDTH_M,
    )

    y2 = min(
        y1 + CHUNK_SIZE_M,
        TOTAL_HEIGHT_M,
    )

    lon1, lat1 = xy_to_lonlat(
        x1,
        y1,
    )

    lon2, lat2 = xy_to_lonlat(
        x2,
        y2,
    )

    return (
        max(MIN_LON, lon1),
        max(MIN_LAT, lat1),
        min(MAX_LON, lon2),
        min(MAX_LAT, lat2),
    )


def chunk_polygon(col, row):

    (
        min_lon,
        min_lat,
        max_lon,
        max_lat,
    ) = chunk_bounds(
        col,
        row,
    )

    return box(
        min_lon,
        min_lat,
        max_lon,
        max_lat,
    )


def get_chunks_for_geometry(geom):

    if geom.is_empty:
        return []

    minx, miny, maxx, maxy = (
        geom.bounds
    )

    # Completely outside Pune BBOX
    if maxx < MIN_LON:
        return []

    if minx > MAX_LON:
        return []

    if maxy < MIN_LAT:
        return []

    if miny > MAX_LAT:
        return []

    minx = max(
        minx,
        MIN_LON,
    )

    maxx = min(
        maxx,
        MAX_LON,
    )

    miny = max(
        miny,
        MIN_LAT,
    )

    maxy = min(
        maxy,
        MAX_LAT,
    )

    x1, y1 = lonlat_to_xy(
        minx,
        miny,
    )

    x2, y2 = lonlat_to_xy(
        maxx,
        maxy,
    )

    col1 = max(
        0,
        int(
            math.floor(
                x1 / CHUNK_SIZE_M
            )
        ),
    )

    row1 = max(
        0,
        int(
            math.floor(
                y1 / CHUNK_SIZE_M
            )
        ),
    )

    col2 = min(
        GRID_COLS - 1,
        int(
            math.floor(
                x2 / CHUNK_SIZE_M
            )
        ),
    )

    row2 = min(
        GRID_ROWS - 1,
        int(
            math.floor(
                y2 / CHUNK_SIZE_M
            )
        ),
    )

    result = []

    for row in range(
        row1,
        row2 + 1,
    ):

        for col in range(
            col1,
            col2 + 1,
        ):

            result.append(
                (
                    col,
                    row,
                )
            )

    return result


# ============================================================
# PROPERTIES
# ============================================================

def clean_properties(
    properties,
    data_type,
):

    properties = properties or {}

    if data_type == "buildings":

        if "building" not in properties:
            return None

        return {
            "osm_id": properties.get(
                "@id"
            ),
            "building": properties.get(
                "building"
            ),
            "height": properties.get(
                "height"
            ),
            "building:levels": properties.get(
                "building:levels"
            ),
            "name": properties.get(
                "name"
            ),
        }

    if data_type == "roads":

        if "highway" not in properties:
            return None

        return {
            "osm_id": properties.get(
                "@id"
            ),
            "highway": properties.get(
                "highway"
            ),
            "name": properties.get(
                "name"
            ),
            "ref": properties.get(
                "ref"
            ),
            "surface": properties.get(
                "surface"
            ),
            "lanes": properties.get(
                "lanes"
            ),
            "maxspeed": properties.get(
                "maxspeed"
            ),
        }

    if data_type == "waterways":

        if "waterway" not in properties:
            return None

        return {
            "osm_id": properties.get(
                "@id"
            ),
            "waterway": properties.get(
                "waterway"
            ),
            "name": properties.get(
                "name"
            ),
        }

    return None


# ============================================================
# DISK STREAMING WRITER
# ============================================================

class ChunkWriter:

    def __init__(self):

        self.handles = {}
        self.counts = {}
        self.chunk_metadata = {}

    def _chunk_dir(
        self,
        cid,
    ):

        directory = (
            CHUNKS_ROOT / cid
        )

        directory.mkdir(
            parents=True,
            exist_ok=True,
        )

        return directory

    def _open(
        self,
        cid,
        data_type,
    ):

        key = (
            cid,
            data_type,
        )

        if key in self.handles:
            return self.handles[key]

        directory = self._chunk_dir(
            cid
        )

        path = (
            directory
            / f"{data_type}.geojson"
        )

        handle = open(
            path,
            "a",
            encoding="utf-8",
        )

        self.handles[key] = handle

        self.counts.setdefault(
            cid,
            {
                "buildings": 0,
                "roads": 0,
                "waterways": 0,
            },
        )

        return handle

    def write(
        self,
        cid,
        data_type,
        feature,
    ):

        handle = self._open(
            cid,
            data_type,
        )

        handle.write(
            json.dumps(
                feature,
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )

        handle.write("\n")

        self.counts[cid][
            data_type
        ] += 1

    def close(self):

        for handle in self.handles.values():
            handle.close()

        self.handles.clear()

        # Convert streamed GeoJSON lines into
        # proper FeatureCollection files.
        for cid, counts in self.counts.items():

            directory = (
                CHUNKS_ROOT / cid
            )

            for data_type in [
                "buildings",
                "roads",
                "waterways",
            ]:

                count = counts[
                    data_type
                ]

                if count == 0:
                    continue

                path = (
                    directory
                    / f"{data_type}.geojson"
                )

                temp = (
                    directory
                    / f"{data_type}.geojson.tmp"
                )

                with open(
                    path,
                    "r",
                    encoding="utf-8",
                ) as source, open(
                    temp,
                    "w",
                    encoding="utf-8",
                ) as target:

                    target.write(
                        '{"type":"FeatureCollection","features":['
                    )

                    first = True

                    for line in source:

                        line = line.strip()

                        if not line:
                            continue

                        if not first:
                            target.write(",")

                        target.write(line)

                        first = False

                    target.write(
                        "]}"
                    )

                os.replace(
                    temp,
                    path,
                )

            # metadata
            try:
                col = int(
                    cid.split("_")[1]
                )

                row = int(
                    cid.split("_")[2]
                )

            except Exception:
                continue

            (
                min_lon,
                min_lat,
                max_lon,
                max_lat,
            ) = chunk_bounds(
                col,
                row,
            )

            metadata = {
                "chunk_id": cid,
                "col": col,
                "row": row,
                "chunk_size_m": CHUNK_SIZE_M,
                "bounds": {
                    "min_lon": min_lon,
                    "min_lat": min_lat,
                    "max_lon": max_lon,
                    "max_lat": max_lat,
                },
                "crs": "EPSG:4326",
                "source": "OpenStreetMap",
                "features": counts,
            }

            with open(
                directory
                / "metadata.json",
                "w",
                encoding="utf-8",
            ) as f:

                json.dump(
                    metadata,
                    f,
                    indent=2,
                )


# ============================================================
# PROCESS GEOJSONSEQ
# ============================================================

def process_geojsonseq(
    path,
    data_type,
    writer,
    stats,
):

    log("")
    log("=" * 50)
    log(
        f"PROCESSING {data_type}"
    )
    log(
        f"FILE: {path}"
    )
    log("=" * 50)

    total = 0
    accepted = 0
    chunk_features = 0

    with open(
        path,
        "r",
        encoding="utf-8",
    ) as f:

        for line in f:

            line = line.strip()

            if not line:
                continue

            total += 1

            try:
                obj = json.loads(
                    line
                )

            except json.JSONDecodeError:
                stats[
                    "invalid_json"
                ] += 1

                continue

            geometry_data = obj.get(
                "geometry"
            )

            if not geometry_data:

                stats[
                    "no_geometry"
                ] += 1

                continue

            try:

                geom = shape(
                    geometry_data
                )

            except Exception:

                stats[
                    "invalid_geometry"
                ] += 1

                continue

            if geom.is_empty:

                stats[
                    "empty_geometry"
                ] += 1

                continue

            properties = clean_properties(
                obj.get("properties"),
                data_type,
            )

            if properties is None:

                stats[
                    "wrong_tag"
                ] += 1

                continue

            chunk_list = (
                get_chunks_for_geometry(
                    geom
                )
            )

            if not chunk_list:

                stats[
                    "outside_bbox"
                ] += 1

                continue

            accepted += 1

            for col, row in chunk_list:

                cid = chunk_id(
                    col,
                    row,
                )

                chunk_box = chunk_polygon(
                    col,
                    row,
                )

                try:

                    clipped = (
                        geom.intersection(
                            chunk_box
                        )
                    )

                except Exception:

                    stats[
                        "intersection_errors"
                    ] += 1

                    continue

                if clipped.is_empty:
                    continue

                if not clipped.is_valid:

                    try:
                        clipped = (
                            clipped.buffer(
                                0
                            )
                        )

                    except Exception:
                        continue

                if clipped.is_empty:
                    continue

                feature = {
                    "type": "Feature",
                    "geometry": mapping(
                        clipped
                    ),
                    "properties": properties,
                }

                writer.write(
                    cid,
                    data_type,
                    feature,
                )

                chunk_features += 1

            if (
                total
                % PROGRESS_EVERY
                == 0
            ):

                log(
                    f"[{data_type}] "
                    f"processed={total:,} "
                    f"accepted={accepted:,} "
                    f"chunk_features={chunk_features:,}"
                )

    log("")
    log(
        f"[{data_type}] COMPLETE"
    )

    log(
        f"  total: "
        f"{total:,}"
    )

    log(
        f"  accepted: "
        f"{accepted:,}"
    )

    log(
        f"  chunk features: "
        f"{chunk_features:,}"
    )

    return {
        "total": total,
        "accepted": accepted,
        "chunk_features": chunk_features,
    }


# ============================================================
# GRID
# ============================================================

def write_grid():

    features = []

    for row in range(
        GRID_ROWS
    ):

        for col in range(
            GRID_COLS
        ):

            polygon = chunk_polygon(
                col,
                row,
            )

            features.append(
                {
                    "type": "Feature",
                    "geometry": mapping(
                        polygon
                    ),
                    "properties": {
                        "chunk_id": chunk_id(
                            col,
                            row,
                        ),
                        "col": col,
                        "row": row,
                    },
                }
            )

    grid = {
        "type": "FeatureCollection",
        "features": features,
    }

    with open(
        OUTPUT_ROOT
        / "grid.geojson",
        "w",
        encoding="utf-8",
    ) as f:

        json.dump(
            grid,
            f,
            ensure_ascii=False,
            separators=(",", ":"),
        )


# ============================================================
# SUMMARY
# ============================================================

def write_summary(
    feature_stats,
    processing_stats,
    writer,
):

    active_chunks = len(
        writer.counts
    )

    summary = {
        "bbox": {
            "min_lon": MIN_LON,
            "min_lat": MIN_LAT,
            "max_lon": MAX_LON,
            "max_lat": MAX_LAT,
        },
        "chunk_size_m": CHUNK_SIZE_M,
        "grid": {
            "columns": GRID_COLS,
            "rows": GRID_ROWS,
            "total": (
                GRID_COLS * GRID_ROWS
            ),
        },
        "features": feature_stats,
        "processing": dict(
            processing_stats
        ),
        "active_chunks": active_chunks,
    }

    with open(
        OUTPUT_ROOT
        / "summary.json",
        "w",
        encoding="utf-8",
    ) as f:

        json.dump(
            summary,
            f,
            indent=2,
        )

    return active_chunks


# ============================================================
# ARCHIVE
# ============================================================

def create_archive():

    archive = Path(
        "pune-1km-chunks.tar.gz"
    )

    if archive.exists():
        archive.unlink()

    log("")
    log("=" * 50)
    log("CREATING ARCHIVE")
    log("=" * 50)

    with tarfile.open(
        archive,
        "w:gz",
        compresslevel=1,
    ) as tar:

        tar.add(
            CHUNKS_ROOT,
            arcname="chunks",
        )

        tar.add(
            OUTPUT_ROOT
            / "grid.geojson",
            arcname="grid.geojson",
        )

        tar.add(
            OUTPUT_ROOT
            / "summary.json",
            arcname="summary.json",
        )

    size_mb = (
        archive.stat().st_size
        / 1024
        / 1024
    )

    log(
        f"Archive size: "
        f"{size_mb:.2f} MB"
    )


# ============================================================
# MAIN
# ============================================================

def main():

    log("")
    log("=" * 60)
    log("PUNE 1KM PROCESSOR v3")
    log("=" * 60)

    if not PBF_PATH.exists():
        raise FileNotFoundError(
            f"Missing: {PBF_PATH}"
        )

    if shutil.which("osmium") is None:
        raise RuntimeError(
            "osmium-tool not installed"
        )

    log("")
    log("PUNE BBOX:")
    log(
        f"{MIN_LON}, "
        f"{MIN_LAT}, "
        f"{MAX_LON}, "
        f"{MAX_LAT}"
    )

    log("")
    log("GRID:")
    log(
        f"{GRID_COLS} x {GRID_ROWS}"
    )

    log(
        f"Total chunks: "
        f"{GRID_COLS * GRID_ROWS:,}"
    )

    # Clean old generated data
    if OUTPUT_ROOT.exists():
        shutil.rmtree(
            OUTPUT_ROOT
        )

    OUTPUT_ROOT.mkdir(
        parents=True,
        exist_ok=True,
    )

    CHUNKS_ROOT.mkdir(
        parents=True,
        exist_ok=True,
    )

    TEMP_ROOT.mkdir(
        parents=True,
        exist_ok=True,
    )

    # ========================================================
    # FILTER DIRECTLY FROM ORIGINAL PBF
    # ========================================================

    buildings_pbf = (
        TEMP_ROOT
        / "buildings.osm.pbf"
    )

    roads_pbf = (
        TEMP_ROOT
        / "roads.osm.pbf"
    )

    waterways_pbf = (
        TEMP_ROOT
        / "waterways.osm.pbf"
    )

    log("")
    log("=" * 50)
    log("FILTER BUILDINGS")
    log("=" * 50)

    run_command([
        "osmium",
        "tags-filter",
        str(PBF_PATH),
        "w/building",
        "r/building",
        "-o",
        str(buildings_pbf),
        "--overwrite",
    ])

    log("")
    log("=" * 50)
    log("FILTER ROADS")
    log("=" * 50)

    run_command([
        "osmium",
        "tags-filter",
        str(PBF_PATH),
        "w/highway",
        "r/highway",
        "-o",
        str(roads_pbf),
        "--overwrite",
    ])

    log("")
    log("=" * 50)
    log("FILTER WATERWAYS")
    log("=" * 50)

    run_command([
        "osmium",
        "tags-filter",
        str(PBF_PATH),
        "w/waterway",
        "r/waterway",
        "-o",
        str(waterways_pbf),
        "--overwrite",
    ])

    # ========================================================
    # EXPORT
    # ========================================================

    buildings_json = (
        TEMP_ROOT
        / "buildings.geojsonseq"
    )

    roads_json = (
        TEMP_ROOT
        / "roads.geojsonseq"
    )

    waterways_json = (
        TEMP_ROOT
        / "waterways.geojsonseq"
    )

    log("")
    log("=" * 50)
    log("EXPORT BUILDINGS")
    log("=" * 50)

    run_command([
        "osmium",
        "export",
        str(buildings_pbf),
        "-f",
        "geojsonseq",
        "-o",
        str(buildings_json),
        "--overwrite",
    ])

    log("")
    log("=" * 50)
    log("EXPORT ROADS")
    log("=" * 50)

    run_command([
        "osmium",
        "export",
        str(roads_pbf),
        "-f",
        "geojsonseq",
        "-o",
        str(roads_json),
        "--overwrite",
    ])

    log("")
    log("=" * 50)
    log("EXPORT WATERWAYS")
    log("=" * 50)

    run_command([
        "osmium",
        "export",
        str(waterways_pbf),
        "-f",
        "geojsonseq",
        "-o",
        str(waterways_json),
        "--overwrite",
    ])

    # ========================================================
    # STREAM PROCESS
    # ========================================================

    writer = ChunkWriter()

    processing_stats = {
        "invalid_json": 0,
        "no_geometry": 0,
        "invalid_geometry": 0,
        "empty_geometry": 0,
        "wrong_tag": 0,
        "outside_bbox": 0,
        "intersection_errors": 0,
    }

    feature_stats = {}

    feature_stats[
        "buildings"
    ] = process_geojsonseq(
        buildings_json,
        "buildings",
        writer,
        processing_stats,
    )

    feature_stats[
        "roads"
    ] = process_geojsonseq(
        roads_json,
        "roads",
        writer,
        processing_stats,
    )

    feature_stats[
        "waterways"
    ] = process_geojsonseq(
        waterways_json,
        "waterways",
        writer,
        processing_stats,
    )

    # Close all streaming files
    writer.close()

    # ========================================================
    # GRID
    # ========================================================

    write_grid()

    # ========================================================
    # SUMMARY
    # ========================================================

    active_chunks = write_summary(
        feature_stats,
        processing_stats,
        writer,
    )

    # ========================================================
    # ARCHIVE
    # ========================================================

    create_archive()

    log("")
    log("=" * 60)
    log("PROCESSING COMPLETE")
    log("=" * 60)

    log(
        f"Active chunks: "
        f"{active_chunks:,}"
    )

    for name, stats in (
        feature_stats.items()
    ):

        log(
            f"{name}: "
            f"{stats['accepted']:,} / "
            f"{stats['total']:,}"
        )

    log("")
    log(
        "Output:"
    )

    log(
        f"  {CHUNKS_ROOT}"
    )

    log(
        "  pune-1km-chunks.tar.gz"
    )


if __name__ == "__main__":

    try:

        main()

    except KeyboardInterrupt:

        log(
            "\nProcessing cancelled."
        )

        sys.exit(130)

    except Exception as exc:

        log("")
        log(
            "PROCESSING FAILED:"
        )
        log(
            repr(exc)
        )

        sys.exit(1)
