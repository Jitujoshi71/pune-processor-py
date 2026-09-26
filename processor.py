#!/usr/bin/env python3

import json
import math
import os
import shutil
import subprocess
import tarfile
import time
from pathlib import Path
from collections import defaultdict

from shapely.geometry import shape, box, mapping


# ============================================================
# CONFIG
# ============================================================

RAW_DIR = Path(
    os.getenv("RAW_DIR", "raw")
)

OSM_FILE = Path(
    os.getenv(
        "OSM_FILE",
        str(RAW_DIR / "central-zone.osm.pbf"),
    )
)

OUTPUT_DIR = Path(
    os.getenv(
        "OUTPUT_DIR",
        "data/chunks",
    )
)

TEMP_DIR = Path(
    os.getenv(
        "TEMP_DIR",
        "data/.processor_tmp",
    )
)

CHUNK_SIZE_M = float(
    os.getenv(
        "CHUNK_SIZE_M",
        "1000",
    )
)

PROGRESS_EVERY = int(
    os.getenv(
        "PROGRESS_EVERY",
        "50000",
    )
)

DEFAULT_BBOX = (
    73.70,
    18.40,
    74.05,
    18.70,
)


# ============================================================
# BBOX
# ============================================================

def parse_bbox():

    value = os.getenv("BBOX")

    if not value:
        return DEFAULT_BBOX

    parts = [
        x.strip()
        for x in value.split(",")
    ]

    if len(parts) != 4:
        raise ValueError(
            "BBOX must be: "
            "min_lon,min_lat,max_lon,max_lat"
        )

    values = [
        float(x)
        for x in parts
    ]

    min_lon, min_lat, max_lon, max_lat = values

    if min_lon >= max_lon:
        raise ValueError(
            "min_lon must be smaller than max_lon"
        )

    if min_lat >= max_lat:
        raise ValueError(
            "min_lat must be smaller than max_lat"
        )

    return (
        min_lon,
        min_lat,
        max_lon,
        max_lat,
    )


# ============================================================
# APPROXIMATE METRIC CONVERSION
# ============================================================

def lon_to_m(lon, reference_lat):

    return (
        lon
        * 111320.0
        * math.cos(
            math.radians(reference_lat)
        )
    )


def lat_to_m(lat):

    return lat * 110540.0


def m_to_lon(x, reference_lat):

    return x / (
        111320.0
        * math.cos(
            math.radians(reference_lat)
        )
    )


def m_to_lat(y):

    return y / 110540.0


# ============================================================
# CHUNK ID
# ============================================================

def chunk_id(ix, iy):

    return (
        f"PUNE_"
        f"{ix:04d}_"
        f"{iy:04d}"
    )


# ============================================================
# RUN COMMAND
# ============================================================

def run_command(command):

    print()
    print("COMMAND:")
    print(
        " ".join(
            str(x)
            for x in command
        ),
        flush=True,
    )

    result = subprocess.run(
        command,
        text=True,
    )

    if result.returncode != 0:

        raise RuntimeError(
            "Command failed with exit code "
            f"{result.returncode}"
        )


# ============================================================
# WRITE GEOJSON
# ============================================================

def write_geojson(
    path,
    features,
):

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    data = {
        "type": "FeatureCollection",
        "features": features,
    }

    with path.open(
        "w",
        encoding="utf-8",
    ) as file:

        json.dump(
            data,
            file,
            ensure_ascii=False,
            separators=(",", ":"),
        )


# ============================================================
# MAIN
# ============================================================

def main():

    start_time = time.time()

    print()
    print("=" * 70)
    print("PUNE OSM STREAMING PROCESSOR")
    print("=" * 70)

    # --------------------------------------------------------
    # Check files
    # --------------------------------------------------------

    if not OSM_FILE.exists():

        raise FileNotFoundError(
            f"OSM file not found: {OSM_FILE}"
        )

    # --------------------------------------------------------
    # Check osmium
    # --------------------------------------------------------

    print()
    print("Checking osmium-tool...")

    result = subprocess.run(
        [
            "osmium",
            "--version",
        ],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:

        raise RuntimeError(
            "osmium-tool is not installed."
        )

    print(
        result.stdout.strip()
    )

    # --------------------------------------------------------
    # BBOX
    # --------------------------------------------------------

    (
        min_lon,
        min_lat,
        max_lon,
        max_lat,
    ) = parse_bbox()

    print()
    print("BBOX:")
    print(
        f"  min_lon = {min_lon}"
    )
    print(
        f"  min_lat = {min_lat}"
    )
    print(
        f"  max_lon = {max_lon}"
    )
    print(
        f"  max_lat = {max_lat}"
    )

    # --------------------------------------------------------
    # Projection
    # --------------------------------------------------------

    reference_lat = (
        min_lat
        + max_lat
    ) / 2.0

    x0 = lon_to_m(
        min_lon,
        reference_lat,
    )

    x1 = lon_to_m(
        max_lon,
        reference_lat,
    )

    y0 = lat_to_m(
        min_lat
    )

    y1 = lat_to_m(
        max_lat
    )

    width_m = x1 - x0
    height_m = y1 - y0

    nx = max(
        1,
        math.ceil(
            width_m
            / CHUNK_SIZE_M
        ),
    )

    ny = max(
        1,
        math.ceil(
            height_m
            / CHUNK_SIZE_M
        ),
    )

    total_chunks = nx * ny

    print()
    print("GRID:")
    print(
        f"  Width : {width_m:.2f} m"
    )
    print(
        f"  Height: {height_m:.2f} m"
    )
    print(
        f"  Chunk : {CHUNK_SIZE_M:.0f} m"
    )
    print(
        f"  Grid  : {nx} x {ny}"
    )
    print(
        f"  Total : {total_chunks:,}"
    )

    # --------------------------------------------------------
    # Clean temp
    # --------------------------------------------------------

    if TEMP_DIR.exists():

        shutil.rmtree(
            TEMP_DIR
        )

    TEMP_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    # --------------------------------------------------------
    # Clean output
    # --------------------------------------------------------

    if OUTPUT_DIR.exists():

        shutil.rmtree(
            OUTPUT_DIR
        )

    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    # --------------------------------------------------------
    # Convert PBF -> GeoJSONSeq
    # --------------------------------------------------------

    geojsonseq = (
        TEMP_DIR
        / "pune.geojsonseq"
    )

    print()
    print("=" * 70)
    print("CONVERTING OSM PBF")
    print("=" * 70)

    print(
        "PBF -> GeoJSONSeq"
    )

    print(
        "This step resolves OSM node coordinates."
    )

    run_command(
        [
            "osmium",
            "export",
            str(OSM_FILE),
            "-f",
            "geojsonseq",
            "-o",
            str(geojsonseq),
            "--overwrite",
        ]
    )

    if not geojsonseq.exists():

        raise RuntimeError(
            "osmium export did not create "
            "GeoJSONSeq output."
        )

    print()
    print(
        f"GeoJSONSeq size: "
        f"{geojsonseq.stat().st_size / 1024 / 1024:.2f} MB"
    )

    # --------------------------------------------------------
    # City bbox
    # --------------------------------------------------------

    city_bbox = box(
        min_lon,
        min_lat,
        max_lon,
        max_lat,
    )

    # --------------------------------------------------------
    # Chunk storage
    # --------------------------------------------------------

    chunks = defaultdict(
        lambda: {
            "buildings": [],
            "roads": [],
            "waterways": [],
        }
    )

    # --------------------------------------------------------
    # Counters
    # --------------------------------------------------------

    feature_count = 0
    building_count = 0
    road_count = 0
    waterway_count = 0
    invalid_count = 0
    outside_count = 0

    # --------------------------------------------------------
    # Stream GeoJSONSeq
    # --------------------------------------------------------

    print()
    print("=" * 70)
    print("STREAMING GEOMETRY")
    print("=" * 70)

    with geojsonseq.open(
        "r",
        encoding="utf-8",
    ) as file:

        for line in file:

            line = line.strip()

            if not line:
                continue

            feature_count += 1

            # ------------------------------------------------
            # Progress
            # ------------------------------------------------

            if (
                feature_count
                % PROGRESS_EVERY
                == 0
            ):

                print(
                    f"[FEATURES] "
                    f"{feature_count:,} "
                    f"buildings="
                    f"{building_count:,} "
                    f"roads="
                    f"{road_count:,} "
                    f"waterways="
                    f"{waterway_count:,}",
                    flush=True,
                )

            # ------------------------------------------------
            # Parse
            # ------------------------------------------------

            try:

                feature = json.loads(
                    line
                )

            except Exception:

                invalid_count += 1
                continue

            if (
                feature.get("type")
                != "Feature"
            ):
                continue

            properties = (
                feature.get(
                    "properties"
                )
                or {}
            )

            geometry_data = (
                feature.get(
                    "geometry"
                )
            )

            if not geometry_data:
                continue

            # ------------------------------------------------
            # Determine feature type
            # ------------------------------------------------

            building = (
                properties.get(
                    "building"
                )
            )

            highway = (
                properties.get(
                    "highway"
                )
            )

            waterway = (
                properties.get(
                    "waterway"
                )
            )

            if building:

                feature_type = "buildings"

            elif highway:

                feature_type = "roads"

            elif waterway:

                feature_type = "waterways"

            else:

                continue

            # ------------------------------------------------
            # Shapely
            # ------------------------------------------------

            try:

                geometry = shape(
                    geometry_data
                )

            except Exception:

                invalid_count += 1
                continue

            if geometry.is_empty:
                continue

            # ------------------------------------------------
            # City bbox
            # ------------------------------------------------

            try:

                if not geometry.intersects(
                    city_bbox
                ):
                    outside_count += 1
                    continue

                geometry = geometry.intersection(
                    city_bbox
                )

            except Exception:

                invalid_count += 1
                continue

            if geometry.is_empty:
                continue

            # ------------------------------------------------
            # Building validity
            # ------------------------------------------------

            if feature_type == "buildings":

                try:

                    if not geometry.is_valid:

                        geometry = (
                            geometry.buffer(
                                0
                            )
                        )

                except Exception:

                    invalid_count += 1
                    continue

                if geometry.is_empty:
                    continue

            # ------------------------------------------------
            # Geometry bounds
            # ------------------------------------------------

            try:

                (
                    geom_min_lon,
                    geom_min_lat,
                    geom_max_lon,
                    geom_max_lat,
                ) = geometry.bounds

            except Exception:

                invalid_count += 1
                continue

            # ------------------------------------------------
            # Candidate chunks
            # ------------------------------------------------

            ix0 = math.floor(
                (
                    lon_to_m(
                        geom_min_lon,
                        reference_lat,
                    )
                    - x0
                )
                / CHUNK_SIZE_M
            )

            ix1 = math.floor(
                (
                    lon_to_m(
                        geom_max_lon,
                        reference_lat,
                    )
                    - x0
                )
                / CHUNK_SIZE_M
            )

            iy0 = math.floor(
                (
                    lat_to_m(
                        geom_min_lat
                    )
                    - y0
                )
                / CHUNK_SIZE_M
            )

            iy1 = math.floor(
                (
                    lat_to_m(
                        geom_max_lat
                    )
                    - y0
                )
                / CHUNK_SIZE_M
            )

            ix0 = max(
                0,
                min(
                    nx - 1,
                    ix0,
                ),
            )

            ix1 = max(
                0,
                min(
                    nx - 1,
                    ix1,
                ),
            )

            iy0 = max(
                0,
                min(
                    ny - 1,
                    iy0,
                ),
            )

            iy1 = max(
                0,
                min(
                    ny - 1,
                    iy1,
                ),
            )

            # ------------------------------------------------
            # Feature ID
            # ------------------------------------------------

            osm_id = (
                properties.get(
                    "@id"
                )
                or properties.get(
                    "id"
                )
                or properties.get(
                    "osm_id"
                )
                or feature_count
            )

            try:
                osm_id = int(
                    str(osm_id).split("/")[-1]
                )
            except Exception:
                osm_id = feature_count

            # ------------------------------------------------
            # Add to chunks
            # ------------------------------------------------

            for iy in range(
                iy0,
                iy1 + 1,
            ):

                for ix in range(
                    ix0,
                    ix1 + 1,
                ):

                    chunk_min_x = (
                        x0
                        + ix
                        * CHUNK_SIZE_M
                    )

                    chunk_max_x = (
                        x0
                        + (ix + 1)
                        * CHUNK_SIZE_M
                    )

                    chunk_min_y = (
                        y0
                        + iy
                        * CHUNK_SIZE_M
                    )

                    chunk_max_y = (
                        y0
                        + (iy + 1)
                        * CHUNK_SIZE_M
                    )

                    chunk_min_lon = m_to_lon(
                        chunk_min_x,
                        reference_lat,
                    )

                    chunk_max_lon = m_to_lon(
                        chunk_max_x,
                        reference_lat,
                    )

                    chunk_min_lat = m_to_lat(
                        chunk_min_y
                    )

                    chunk_max_lat = m_to_lat(
                        chunk_max_y
                    )

                    chunk_bbox = box(
                        chunk_min_lon,
                        chunk_min_lat,
                        chunk_max_lon,
                        chunk_max_lat,
                    )

                    try:

                        clipped = (
                            geometry.intersection(
                                chunk_bbox
                            )
                        )

                    except Exception:

                        continue

                    if clipped.is_empty:
                        continue

                    # ----------------------------------------
                    # Properties
                    # ----------------------------------------

                    output_properties = dict(
                        properties
                    )

                    output_properties[
                        "osm_id"
                    ] = osm_id

                    output_properties[
                        "feature_type"
                    ] = feature_type

                    output_feature = {
                        "type": "Feature",
                        "properties": output_properties,
                        "geometry": mapping(
                            clipped
                        ),
                    }

                    chunks[
                        (ix, iy)
                    ][
                        feature_type
                    ].append(
                        output_feature
                    )

            # ------------------------------------------------
            # Counters
            # ------------------------------------------------

            if feature_type == "buildings":
                building_count += 1

            elif feature_type == "roads":
                road_count += 1

            elif feature_type == "waterways":
                waterway_count += 1

    # --------------------------------------------------------
    # Diagnostic
    # --------------------------------------------------------

    print()
    print("=" * 70)
    print("GEOMETRY EXTRACTION COMPLETE")
    print("=" * 70)

    print(
        f"Features read       : "
        f"{feature_count:,}"
    )

    print(
        f"Buildings           : "
        f"{building_count:,}"
    )

    print(
        f"Roads               : "
        f"{road_count:,}"
    )

    print(
        f"Waterways           : "
        f"{waterway_count:,}"
    )

    print(
        f"Invalid geometries  : "
        f"{invalid_count:,}"
    )

    print(
        f"Outside bbox        : "
        f"{outside_count:,}"
    )

    if (
        building_count == 0
        and road_count == 0
        and waterway_count == 0
    ):

        raise RuntimeError(
            "ZERO buildings/roads/waterways were "
            "extracted from the OSM data. "
            "Check the source PBF and BBOX."
        )

    # --------------------------------------------------------
    # Delete temporary GeoJSONSeq
    # --------------------------------------------------------

    try:

        geojsonseq.unlink()

    except Exception:
        pass

    # --------------------------------------------------------
    # Generate chunks
    # --------------------------------------------------------

    print()
    print("=" * 70)
    print("WRITING 1KM CHUNKS")
    print("=" * 70)

    grid_features = []

    chunk_summaries = []

    nonempty_chunks = 0

    total_buildings = 0
    total_roads = 0
    total_waterways = 0

    # --------------------------------------------------------
    # All chunks
    # --------------------------------------------------------

    for iy in range(ny):

        for ix in range(nx):

            number = (
                iy * nx
                + ix
                + 1
            )

            cid = chunk_id(
                ix,
                iy,
            )

            chunk_min_x = (
                x0
                + ix
                * CHUNK_SIZE_M
            )

            chunk_max_x = min(
                x0
                + (ix + 1)
                * CHUNK_SIZE_M,
                x1,
            )

            chunk_min_y = (
                y0
                + iy
                * CHUNK_SIZE_M
            )

            chunk_max_y = min(
                y0
                + (iy + 1)
                * CHUNK_SIZE_M,
                y1,
            )

            min_chunk_lon = m_to_lon(
                chunk_min_x,
                reference_lat,
            )

            max_chunk_lon = m_to_lon(
                chunk_max_x,
                reference_lat,
            )

            min_chunk_lat = m_to_lat(
                chunk_min_y
            )

            max_chunk_lat = m_to_lat(
                chunk_max_y
            )

            chunk_dir = (
                OUTPUT_DIR
                / cid
            )

            chunk_dir.mkdir(
                parents=True,
                exist_ok=True,
            )

            data = chunks.get(
                (ix, iy),
                {
                    "buildings": [],
                    "roads": [],
                    "waterways": [],
                },
            )

            buildings = data[
                "buildings"
            ]

            roads = data[
                "roads"
            ]

            waterways = data[
                "waterways"
            ]

            # ------------------------------------------------
            # Write geometry
            # ------------------------------------------------

            write_geojson(
                chunk_dir
                / "buildings.geojson",
                buildings,
            )

            write_geojson(
                chunk_dir
                / "roads.geojson",
                roads,
            )

            write_geojson(
                chunk_dir
                / "waterways.geojson",
                waterways,
            )

            # ------------------------------------------------
            # Metadata
            # ------------------------------------------------

            metadata = {
                "version": 3,
                "chunk_id": cid,
                "grid_x": ix,
                "grid_y": iy,
                "chunk_size_m": CHUNK_SIZE_M,
                "crs": "EPSG:4326",
                "bounds_wgs84": [
                    min_chunk_lon,
                    min_chunk_lat,
                    max_chunk_lon,
                    max_chunk_lat,
                ],
                "source": {
                    "file": OSM_FILE.name,
                    "type": "OpenStreetMap PBF",
                },
                "features": {
                    "buildings": len(
                        buildings
                    ),
                    "roads": len(
                        roads
                    ),
                    "waterways": len(
                        waterways
                    ),
                },
            }

            with (
                chunk_dir
                / "metadata.json"
            ).open(
                "w",
                encoding="utf-8",
            ) as file:

                json.dump(
                    metadata,
                    file,
                    indent=2,
                )

            # ------------------------------------------------
            # Counts
            # ------------------------------------------------

            total_buildings += len(
                buildings
            )

            total_roads += len(
                roads
            )

            total_waterways += len(
                waterways
            )

            if (
                buildings
                or roads
                or waterways
            ):

                nonempty_chunks += 1

            # ------------------------------------------------
            # Grid
            # ------------------------------------------------

            polygon = box(
                min_chunk_lon,
                min_chunk_lat,
                max_chunk_lon,
                max_chunk_lat,
            )

            grid_features.append(
                {
                    "type": "Feature",
                    "properties": {
                        "chunk_id": cid,
                        "grid_x": ix,
                        "grid_y": iy,
                        "buildings": len(
                            buildings
                        ),
                        "roads": len(
                            roads
                        ),
                        "waterways": len(
                            waterways
                        ),
                    },
                    "geometry": mapping(
                        polygon
                    ),
                }
            )

            chunk_summaries.append(
                metadata
            )

            # ------------------------------------------------
            # Progress
            # ------------------------------------------------

            if (
                number % 25 == 0
                or number == total_chunks
            ):

                percent = (
                    number
                    / total_chunks
                    * 100
                )

                print(
                    f"[CHUNKS] "
                    f"{number:,}/"
                    f"{total_chunks:,} "
                    f"({percent:.1f}%) "
                    f"buildings="
                    f"{total_buildings:,} "
                    f"roads="
                    f"{total_roads:,} "
                    f"waterways="
                    f"{total_waterways:,}",
                    flush=True,
                )

    # --------------------------------------------------------
    # grid.geojson
    # --------------------------------------------------------

    write_geojson(
        OUTPUT_DIR
        / "grid.geojson",
        grid_features,
    )

    # --------------------------------------------------------
    # summary.json
    # --------------------------------------------------------

    summary = {
        "version": 3,
        "processor": "pune-processor-py",
        "method": "osmium-export-geojsonseq",
        "chunk_size_m": CHUNK_SIZE_M,
        "crs": "EPSG:4326",
        "bbox_wgs84": [
            min_lon,
            min_lat,
            max_lon,
            max_lat,
        ],
        "grid": {
            "nx": nx,
            "ny": ny,
            "total_chunks": total_chunks,
            "nonempty_chunks": nonempty_chunks,
        },
        "feature_counts": {
            "buildings": total_buildings,
            "roads": total_roads,
            "waterways": total_waterways,
        },
        "source": {
            "osm_file": OSM_FILE.name,
            "osm_size_bytes": OSM_FILE.stat().st_size,
        },
        "chunks": chunk_summaries,
    }

    with (
        OUTPUT_DIR
        / "summary.json"
    ).open(
        "w",
        encoding="utf-8",
    ) as file:

        json.dump(
            summary,
            file,
            indent=2,
        )

    # --------------------------------------------------------
    # Archive
    # --------------------------------------------------------

    archive_path = (
        OUTPUT_DIR.parent.parent
        / "pune-1km-chunks.tar.gz"
    )

    if archive_path.exists():
        archive_path.unlink()

    print()
    print(
        "Creating archive..."
    )

    with tarfile.open(
        archive_path,
        "w:gz",
    ) as archive:

        archive.add(
            OUTPUT_DIR,
            arcname="data/chunks",
        )

    # --------------------------------------------------------
    # Cleanup
    # --------------------------------------------------------

    if TEMP_DIR.exists():

        shutil.rmtree(
            TEMP_DIR
        )

    # --------------------------------------------------------
    # Final
    # --------------------------------------------------------

    elapsed = (
        time.time()
        - start_time
    )

    archive_mb = (
        archive_path.stat().st_size
        / 1024
        / 1024
    )

    print()
    print("=" * 70)
    print("PROCESSING COMPLETE")
    print("=" * 70)

    print(
        f"Total chunks     : "
        f"{total_chunks:,}"
    )

    print(
        f"Non-empty chunks : "
        f"{nonempty_chunks:,}"
    )

    print(
        f"Buildings        : "
        f"{total_buildings:,}"
    )

    print(
        f"Roads            : "
        f"{total_roads:,}"
    )

    print(
        f"Waterways        : "
        f"{total_waterways:,}"
    )

    print(
        f"Archive size     : "
        f"{archive_mb:.2f} MB"
    )

    print(
        f"Processing time  : "
        f"{elapsed / 60:.2f} minutes"
    )

    print(
        f"Output           : "
        f"{OUTPUT_DIR}"
    )

    print(
        f"Archive          : "
        f"{archive_path}"
    )

    print("=" * 70)


if __name__ == "__main__":
    main()
