#!/usr/bin/env python3

import json
import math
import os
import shutil
import subprocess
import sys
import tarfile
from collections import defaultdict
from pathlib import Path

from shapely.geometry import shape, mapping, box
from shapely.ops import unary_union


# ============================================================
# CONFIG
# ============================================================

PBF_PATH = Path("raw/central-zone.osm.pbf")

OUTPUT_ROOT = Path("data")
OUTPUT_DIR = OUTPUT_ROOT / "chunks"
TEMP_DIR = OUTPUT_ROOT / ".processor_tmp"

CHUNK_SIZE_M = 1000

# Pune processing area
MIN_LON = 73.70
MIN_LAT = 18.40
MAX_LON = 74.05
MAX_LAT = 18.70

PROGRESS_EVERY = 50_000


# ============================================================
# LOGGING
# ============================================================

def log(msg=""):
    print(msg, flush=True)


# ============================================================
# COMMAND
# ============================================================

def run(cmd):
    log("[CMD] " + " ".join(map(str, cmd)))

    result = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    if result.stdout:
        print(result.stdout, end="", flush=True)

    if result.returncode != 0:
        raise RuntimeError(
            "Command failed:\n"
            + " ".join(map(str, cmd))
        )


# ============================================================
# COORDINATE SYSTEM
# ============================================================

LAT0 = (MIN_LAT + MAX_LAT) / 2.0

METERS_PER_DEG_LAT = 111320.0
METERS_PER_DEG_LON = (
    111320.0 * math.cos(math.radians(LAT0))
)


def lonlat_to_xy(lon, lat):
    x = (lon - MIN_LON) * METERS_PER_DEG_LON
    y = (lat - MIN_LAT) * METERS_PER_DEG_LAT
    return x, y


def xy_to_lonlat(x, y):
    lon = MIN_LON + x / METERS_PER_DEG_LON
    lat = MIN_LAT + y / METERS_PER_DEG_LAT
    return lon, lat


# ============================================================
# GRID
# ============================================================

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


def chunk_id(col, row):
    return f"PUNE_{col:04d}_{row:04d}"


def chunk_bounds(col, row):

    x1 = col * CHUNK_SIZE_M
    y1 = row * CHUNK_SIZE_M

    x2 = min(
        x1 + CHUNK_SIZE_M,
        TOTAL_WIDTH_M,
    )

    y2 = min(
        y1 + CHUNK_SIZE_M,
        TOTAL_HEIGHT_M,
    )

    lon1, lat1 = xy_to_lonlat(x1, y1)
    lon2, lat2 = xy_to_lonlat(x2, y2)

    return (
        max(MIN_LON, lon1),
        max(MIN_LAT, lat1),
        min(MAX_LON, lon2),
        min(MAX_LAT, lat2),
    )


def chunk_polygon(col, row):

    min_lon, min_lat, max_lon, max_lat = (
        chunk_bounds(col, row)
    )

    return box(
        min_lon,
        min_lat,
        max_lon,
        max_lat,
    )


def geometry_chunks(geom):

    if geom.is_empty:
        return []

    minx, miny, maxx, maxy = geom.bounds

    minx = max(minx, MIN_LON)
    miny = max(miny, MIN_LAT)
    maxx = min(maxx, MAX_LON)
    maxy = min(maxy, MAX_LAT)

    if minx >= maxx or miny >= maxy:
        return []

    x1, y1 = lonlat_to_xy(minx, miny)
    x2, y2 = lonlat_to_xy(maxx, maxy)

    col1 = max(
        0,
        int(math.floor(x1 / CHUNK_SIZE_M)),
    )

    row1 = max(
        0,
        int(math.floor(y1 / CHUNK_SIZE_M)),
    )

    col2 = min(
        GRID_COLS - 1,
        int(math.floor(x2 / CHUNK_SIZE_M)),
    )

    row2 = min(
        GRID_ROWS - 1,
        int(math.floor(y2 / CHUNK_SIZE_M)),
    )

    result = []

    for row in range(row1, row2 + 1):
        for col in range(col1, col2 + 1):
            result.append((col, row))

    return result


# ============================================================
# GEOJSON
# ============================================================

def write_geojson(path, features):

    data = {
        "type": "FeatureCollection",
        "features": features,
    }

    with open(
        path,
        "w",
        encoding="utf-8",
    ) as f:

        json.dump(
            data,
            f,
            ensure_ascii=False,
            separators=(",", ":"),
        )


# ============================================================
# PUNE BBOX EXTRACTION
# ============================================================

def extract_pune_pbf():

    TEMP_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    pune_pbf = (
        TEMP_DIR / "pune.osm.pbf"
    )

    log("")
    log("=" * 50)
    log("EXTRACTING PUNE BBOX")
    log("=" * 50)

    # -b = bounding box
    # -o = output
    #
    # We intentionally create a Pune-only PBF first.
    run([
        "osmium",
        "extract",
        "-b",
        f"{MIN_LON},{MIN_LAT},{MAX_LON},{MAX_LAT}",
        str(PBF_PATH),
        "-o",
        str(pune_pbf),
        "--overwrite",
    ])

    if not pune_pbf.exists():
        raise RuntimeError(
            "Pune PBF was not created."
        )

    log("")
    log("Pune PBF:")
    log(
        f"  {pune_pbf}"
    )
    log(
        f"  {pune_pbf.stat().st_size / 1024 / 1024:.2f} MB"
    )

    return pune_pbf


# ============================================================
# TAG FILTERING
# ============================================================

def create_filtered_pbf(
    pune_pbf,
    name,
    filters,
):

    output = (
        TEMP_DIR / f"{name}.osm.pbf"
    )

    log("")
    log("=" * 50)
    log(f"FILTERING {name}")
    log("=" * 50)

    run([
        "osmium",
        "tags-filter",
        str(pune_pbf),
        *filters,
        "-o",
        str(output),
        "--overwrite",
    ])

    return output


# ============================================================
# EXPORT
# ============================================================

def export_geojsonseq(
    pbf_path,
    name,
):

    output = (
        TEMP_DIR / f"{name}.geojsonseq"
    )

    log("")
    log("=" * 50)
    log(f"EXPORTING {name}")
    log("=" * 50)

    run([
        "osmium",
        "export",
        str(pbf_path),
        "-f",
        "geojsonseq",
        "-o",
        str(output),
        "--overwrite",
    ])

    return output


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
            "osm_id": properties.get("@id"),
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
            "osm_id": properties.get("@id"),
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
            "osm_id": properties.get("@id"),
            "waterway": properties.get(
                "waterway"
            ),
            "name": properties.get(
                "name"
            ),
        }

    return None


# ============================================================
# PROCESS ONE GEOJSONSEQ
# ============================================================

def process_file(
    path,
    data_type,
    chunks,
    stats,
):

    log("")
    log("=" * 50)
    log(f"PROCESSING {data_type}")
    log("=" * 50)

    total = 0
    accepted = 0
    clipped = 0

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
                obj = json.loads(line)
            except Exception:
                stats["invalid_json"] += 1
                continue

            geom_data = obj.get(
                "geometry"
            )

            if not geom_data:
                stats["no_geometry"] += 1
                continue

            try:
                geom = shape(
                    geom_data
                )
            except Exception:
                stats["invalid_geometry"] += 1
                continue

            if geom.is_empty:
                stats["empty_geometry"] += 1
                continue

            props = clean_properties(
                obj.get("properties"),
                data_type,
            )

            if props is None:
                stats["wrong_tag"] += 1
                continue

            chunks_for_geom = geometry_chunks(
                geom
            )

            if not chunks_for_geom:
                stats["outside_bbox"] += 1
                continue

            accepted += 1

            for col, row in chunks_for_geom:

                cid = chunk_id(
                    col,
                    row,
                )

                chunk_box = chunk_polygon(
                    col,
                    row,
                )

                try:
                    clipped_geom = (
                        geom.intersection(
                            chunk_box
                        )
                    )
                except Exception:
                    stats[
                        "intersection_errors"
                    ] += 1
                    continue

                if clipped_geom.is_empty:
                    continue

                # Fix invalid polygon geometries
                # where possible.
                if not clipped_geom.is_valid:

                    try:
                        clipped_geom = (
                            clipped_geom.buffer(0)
                        )
                    except Exception:
                        continue

                if clipped_geom.is_empty:
                    continue

                chunks[cid][
                    data_type
                ].append(
                    {
                        "type": "Feature",
                        "geometry": mapping(
                            clipped_geom
                        ),
                        "properties": props,
                    }
                )

                clipped += 1

            if total % PROGRESS_EVERY == 0:

                log(
                    f"[{data_type}] "
                    f"processed={total:,} "
                    f"accepted={accepted:,} "
                    f"chunk_geometries={clipped:,} "
                    f"active_chunks={len(chunks):,}"
                )

    log("")
    log(
        f"[{data_type}] COMPLETE"
    )
    log(
        f"  total    = {total:,}"
    )
    log(
        f"  accepted = {accepted:,}"
    )
    log(
        f"  chunks   = {clipped:,}"
    )

    return {
        "total": total,
        "accepted": accepted,
        "chunk_geometries": clipped,
    }


# ============================================================
# WRITE CHUNKS
# ============================================================

def write_chunks(chunks):

    log("")
    log("=" * 50)
    log("WRITING CHUNKS")
    log("=" * 50)

    written = 0

    for cid in sorted(chunks):

        data = chunks[cid]

        if not any(
            data[key]
            for key in [
                "buildings",
                "roads",
                "waterways",
            ]
        ):
            continue

        try:
            col = int(
                cid.split("_")[1]
            )

            row = int(
                cid.split("_")[2]
            )
        except Exception:
            continue

        directory = (
            OUTPUT_DIR / cid
        )

        directory.mkdir(
            parents=True,
            exist_ok=True,
        )

        min_lon, min_lat, max_lon, max_lat = (
            chunk_bounds(
                col,
                row,
            )
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
            "features": {
                "buildings": len(
                    data["buildings"]
                ),
                "roads": len(
                    data["roads"]
                ),
                "waterways": len(
                    data["waterways"]
                ),
            },
        }

        with open(
            directory / "metadata.json",
            "w",
            encoding="utf-8",
        ) as f:

            json.dump(
                metadata,
                f,
                indent=2,
            )

        for data_type in [
            "buildings",
            "roads",
            "waterways",
        ]:

            features = data[
                data_type
            ]

            if not features:
                continue

            write_geojson(
                directory
                / f"{data_type}.geojson",
                features,
            )

        written += 1

        if written % 100 == 0:

            log(
                f"Written chunks: "
                f"{written:,}"
            )

    log(
        f"TOTAL CHUNKS WRITTEN: "
        f"{written:,}"
    )

    return written


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

    write_geojson(
        OUTPUT_ROOT
        / "grid.geojson",
        features,
    )


# ============================================================
# SUMMARY
# ============================================================

def write_summary(
    feature_stats,
    processing_stats,
    active_chunks,
):

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
        OUTPUT_ROOT / "summary.json",
        "w",
        encoding="utf-8",
    ) as f:

        json.dump(
            summary,
            f,
            indent=2,
        )


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
            OUTPUT_DIR,
            arcname="chunks",
        )

        tar.add(
            OUTPUT_ROOT / "grid.geojson",
            arcname="grid.geojson",
        )

        tar.add(
            OUTPUT_ROOT / "summary.json",
            arcname="summary.json",
        )

    size = (
        archive.stat().st_size
        / 1024
        / 1024
    )

    log(
        f"Archive: {archive}"
    )

    log(
        f"Size: {size:.2f} MB"
    )


# ============================================================
# MAIN
# ============================================================

def main():

    log("=" * 60)
    log("PUNE 1KM PROCESSOR v2")
    log("=" * 60)

    if not PBF_PATH.exists():
        raise FileNotFoundError(
            f"Missing PBF: {PBF_PATH}"
        )

    if shutil.which("osmium") is None:
        raise RuntimeError(
            "osmium-tool is not installed."
        )

    log("")
    log("PUNE BBOX")
    log(
        f"{MIN_LON},"
        f"{MIN_LAT},"
        f"{MAX_LON},"
        f"{MAX_LAT}"
    )

    log("")
    log("GRID")
    log(
        f"{GRID_COLS} x {GRID_ROWS} "
        f"= {GRID_COLS * GRID_ROWS:,} chunks"
    )

    # Clean old output
    if OUTPUT_DIR.exists():
        shutil.rmtree(
            OUTPUT_DIR
        )

    if TEMP_DIR.exists():
        shutil.rmtree(
            TEMP_DIR
        )

    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    TEMP_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    # --------------------------------------------------------
    # STEP 1
    # --------------------------------------------------------

    pune_pbf = extract_pune_pbf()

    # --------------------------------------------------------
    # STEP 2
    # --------------------------------------------------------

    buildings_pbf = create_filtered_pbf(
        pune_pbf,
        "buildings",
        [
            "w/building",
            "r/building",
        ],
    )

    roads_pbf = create_filtered_pbf(
        pune_pbf,
        "roads",
        [
            "w/highway",
            "r/highway",
        ],
    )

    waterways_pbf = create_filtered_pbf(
        pune_pbf,
        "waterways",
        [
            "w/waterway",
            "r/waterway",
        ],
    )

    # --------------------------------------------------------
    # STEP 3
    # --------------------------------------------------------

    buildings_json = export_geojsonseq(
        buildings_pbf,
        "buildings",
    )

    roads_json = export_geojsonseq(
        roads_pbf,
        "roads",
    )

    waterways_json = export_geojsonseq(
        waterways_pbf,
        "waterways",
    )

    # --------------------------------------------------------
    # STEP 4
    # --------------------------------------------------------

    chunks = defaultdict(
        lambda: {
            "buildings": [],
            "roads": [],
            "waterways": [],
        }
    )

    processing_stats = defaultdict(
        int
    )

    feature_stats = {}

    feature_stats[
        "buildings"
    ] = process_file(
        buildings_json,
        "buildings",
        chunks,
        processing_stats,
    )

    feature_stats[
        "roads"
    ] = process_file(
        roads_json,
        "roads",
        chunks,
        processing_stats,
    )

    feature_stats[
        "waterways"
    ] = process_file(
        waterways_json,
        "waterways",
        chunks,
        processing_stats,
    )

    # --------------------------------------------------------
    # STEP 5
    # --------------------------------------------------------

    active_chunks = write_chunks(
        chunks
    )

    write_grid()

    write_summary(
        feature_stats,
        processing_stats,
        active_chunks,
    )

    # --------------------------------------------------------
    # STEP 6
    # --------------------------------------------------------

    create_archive()

    log("")
    log("=" * 60)
    log("PROCESSING COMPLETE")
    log("=" * 60)

    log(
        f"Active chunks: "
        f"{active_chunks:,}"
    )

    for name, stats in feature_stats.items():

        log(
            f"{name}: "
            f"{stats['accepted']:,} / "
            f"{stats['total']:,}"
        )


if __name__ == "__main__":

    try:
        main()

    except KeyboardInterrupt:

        log(
            "\nProcessing cancelled."
        )

        sys.exit(130)

    except Exception as e:

        log(
            "\nPROCESSING FAILED:"
        )

        log(
            repr(e)
        )

        sys.exit(1)
