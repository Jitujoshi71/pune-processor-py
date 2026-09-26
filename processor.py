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
from shapely.ops import transform


# ============================================================
# CONFIG
# ============================================================

PBF_PATH = Path("raw/central-zone.osm.pbf")

OUTPUT_DIR = Path("data/chunks")
TEMP_DIR = Path("data/.processor_tmp")

CHUNK_SIZE_M = 1000

# Pune-area processing bounds
MIN_LON = 73.70
MIN_LAT = 18.40
MAX_LON = 74.05
MAX_LAT = 18.70

PROGRESS_EVERY = 50_000

# Rough meters/degree.
# Accurate enough for determining 1km chunk assignment;
# geometries themselves remain in WGS84.
METERS_PER_DEG_LAT = 111_320.0


# ============================================================
# HELPERS
# ============================================================

def log(message: str):
    print(message, flush=True)


def run_command(command):
    log("")
    log("[CMD] " + " ".join(str(x) for x in command))

    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    if result.stdout:
        print(result.stdout, end="", flush=True)

    if result.returncode != 0:
        raise RuntimeError(
            f"Command failed with exit code {result.returncode}: "
            f"{' '.join(str(x) for x in command)}"
        )


def ensure_clean_directory(path: Path):
    if path.exists():
        shutil.rmtree(path)

    path.mkdir(parents=True, exist_ok=True)


def lonlat_to_meters(lon, lat):
    """
    Local equirectangular approximation.

    Used only for chunk assignment.
    """
    lat0 = (MIN_LAT + MAX_LAT) / 2.0

    meters_per_deg_lon = (
        111_320.0 * math.cos(math.radians(lat0))
    )

    x = (lon - MIN_LON) * meters_per_deg_lon
    y = (lat - MIN_LAT) * METERS_PER_DEG_LAT

    return x, y


def meters_to_lonlat(x, y):
    lat0 = (MIN_LAT + MAX_LAT) / 2.0

    meters_per_deg_lon = (
        111_320.0 * math.cos(math.radians(lat0))
    )

    lon = MIN_LON + x / meters_per_deg_lon
    lat = MIN_LAT + y / METERS_PER_DEG_LAT

    return lon, lat


def get_grid_dimensions():
    x0, y0 = lonlat_to_meters(MIN_LON, MIN_LAT)
    x1, y1 = lonlat_to_meters(MAX_LON, MAX_LAT)

    width = abs(x1 - x0)
    height = abs(y1 - y0)

    cols = math.ceil(width / CHUNK_SIZE_M)
    rows = math.ceil(height / CHUNK_SIZE_M)

    return width, height, cols, rows


def geometry_bounds(geom):
    minx, miny, maxx, maxy = geom.bounds

    # Reject completely invalid coordinates.
    if (
        not all(
            math.isfinite(v)
            for v in [minx, miny, maxx, maxy]
        )
    ):
        return None

    return minx, miny, maxx, maxy


def get_chunk_indices(geom):
    bounds = geometry_bounds(geom)

    if bounds is None:
        return []

    min_lon, min_lat, max_lon, max_lat = bounds

    if max_lon < MIN_LON:
        return []

    if min_lon > MAX_LON:
        return []

    if max_lat < MIN_LAT:
        return []

    if min_lat > MAX_LAT:
        return []

    min_lon = max(min_lon, MIN_LON)
    max_lon = min(max_lon, MAX_LON)
    min_lat = max(min_lat, MIN_LAT)
    max_lat = min(max_lat, MAX_LAT)

    x0, y0 = lonlat_to_meters(min_lon, min_lat)
    x1, y1 = lonlat_to_meters(max_lon, max_lat)

    min_col = max(0, int(math.floor(min(x0, x1) / CHUNK_SIZE_M)))
    max_col = max(0, int(math.floor(max(x0, x1) / CHUNK_SIZE_M)))

    min_row = max(0, int(math.floor(min(y0, y1) / CHUNK_SIZE_M)))
    max_row = max(0, int(math.floor(max(y0, y1) / CHUNK_SIZE_M)))

    _, _, cols, rows = get_grid_dimensions()

    max_col = min(max_col, cols - 1)
    max_row = min(max_row, rows - 1)

    result = []

    for row in range(min_row, max_row + 1):
        for col in range(min_col, max_col + 1):
            result.append((col, row))

    return result


def chunk_name(col, row):
    return f"PUNE_{col:04d}_{row:04d}"


def chunk_bounds(col, row):
    min_x = col * CHUNK_SIZE_M
    min_y = row * CHUNK_SIZE_M

    max_x = min_x + CHUNK_SIZE_M
    max_y = min_y + CHUNK_SIZE_M

    lon1, lat1 = meters_to_lonlat(min_x, min_y)
    lon2, lat2 = meters_to_lonlat(max_x, max_y)

    return {
        "min_lon": max(MIN_LON, lon1),
        "min_lat": max(MIN_LAT, lat1),
        "max_lon": min(MAX_LON, lon2),
        "max_lat": min(MAX_LAT, lat2),
    }


def feature_record(geom, properties):
    return {
        "type": "Feature",
        "geometry": mapping(geom),
        "properties": properties or {},
    }


def write_geojson(path: Path, features):
    collection = {
        "type": "FeatureCollection",
        "features": features,
    }

    with path.open("w", encoding="utf-8") as f:
        json.dump(
            collection,
            f,
            ensure_ascii=False,
            separators=(",", ":"),
        )


# ============================================================
# OSM FILTERING
# ============================================================

def filter_osm():

    if not PBF_PATH.exists():
        raise FileNotFoundError(
            f"OSM PBF not found: {PBF_PATH}"
        )

    TEMP_DIR.mkdir(parents=True, exist_ok=True)

    buildings_pbf = TEMP_DIR / "buildings.osm.pbf"
    roads_pbf = TEMP_DIR / "roads.osm.pbf"
    waterways_pbf = TEMP_DIR / "waterways.osm.pbf"

    log("")
    log("==========================================")
    log("FILTERING OSM DATA")
    log("==========================================")

    # Buildings
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

    # Roads
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

    # Waterways
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

    return buildings_pbf, roads_pbf, waterways_pbf


# ============================================================
# GEOJSON PROCESSING
# ============================================================

def export_geojsonseq(source_pbf: Path, output_path: Path):

    log("")
    log("==========================================")
    log(f"EXPORTING {source_pbf.name}")
    log("==========================================")

    run_command([
        "osmium",
        "export",
        str(source_pbf),
        "-f",
        "geojsonseq",
        "-o",
        str(output_path),
        "--overwrite",
    ])


def classify_properties(properties, data_type):

    properties = properties or {}

    if data_type == "buildings":

        if "building" not in properties:
            return None

        return {
            "building": properties.get("building"),
            "height": properties.get("height"),
            "building:levels": properties.get("building:levels"),
            "name": properties.get("name"),
            "osm_id": properties.get("@id"),
        }

    if data_type == "roads":

        if "highway" not in properties:
            return None

        return {
            "highway": properties.get("highway"),
            "name": properties.get("name"),
            "ref": properties.get("ref"),
            "surface": properties.get("surface"),
            "lanes": properties.get("lanes"),
            "maxspeed": properties.get("maxspeed"),
            "osm_id": properties.get("@id"),
        }

    if data_type == "waterways":

        if "waterway" not in properties:
            return None

        return {
            "waterway": properties.get("waterway"),
            "name": properties.get("name"),
            "osm_id": properties.get("@id"),
        }

    return None


def process_geojsonseq(
    geojsonseq_path: Path,
    data_type: str,
    chunk_features,
    counters,
):

    log("")
    log("==========================================")
    log(f"PROCESSING {data_type}")
    log(f"FILE: {geojsonseq_path}")
    log("==========================================")

    feature_count = 0
    accepted_count = 0

    with geojsonseq_path.open(
        "r",
        encoding="utf-8",
    ) as f:

        for line in f:

            line = line.strip()

            if not line:
                continue

            feature_count += 1

            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                counters["invalid_json"] += 1
                continue

            geometry_data = obj.get("geometry")

            if not geometry_data:
                counters["no_geometry"] += 1
                continue

            geometry_type = geometry_data.get("type")

            # Ignore point features.
            if geometry_type == "Point":
                counters["points_skipped"] += 1
                continue

            try:
                geom = shape(geometry_data)
            except Exception:
                counters["invalid_geometry"] += 1
                continue

            if geom.is_empty:
                counters["empty_geometry"] += 1
                continue

            properties = obj.get("properties") or {}

            clean_properties = classify_properties(
                properties,
                data_type,
            )

            if clean_properties is None:
                counters["wrong_tag"] += 1
                continue

            chunk_indices = get_chunk_indices(geom)

            if not chunk_indices:
                counters["outside_bbox"] += 1
                continue

            for col, row in chunk_indices:

                name = chunk_name(col, row)

                chunk_features[name][data_type].append(
                    feature_record(
                        geom,
                        clean_properties,
                    )
                )

            accepted_count += 1

            if feature_count % PROGRESS_EVERY == 0:

                log(
                    f"[{data_type}] "
                    f"features={feature_count:,} "
                    f"accepted={accepted_count:,} "
                    f"chunks={len(chunk_features):,}"
                )

    log(
        f"[{data_type}] COMPLETE "
        f"features={feature_count:,} "
        f"accepted={accepted_count:,}"
    )

    return feature_count, accepted_count


# ============================================================
# CHUNK OUTPUT
# ============================================================

def create_chunk_output(chunk_features):

    log("")
    log("==========================================")
    log("WRITING 1 KM CHUNKS")
    log("==========================================")

    _, _, cols, rows = get_grid_dimensions()

    all_chunk_names = set(chunk_features.keys())

    for row in range(rows):
        for col in range(cols):

            name = chunk_name(col, row)

            if name not in all_chunk_names:
                continue

            data = chunk_features[name]

            chunk_dir = OUTPUT_DIR / name
            chunk_dir.mkdir(parents=True, exist_ok=True)

            bounds = chunk_bounds(col, row)

            metadata = {
                "chunk_id": name,
                "col": col,
                "row": row,
                "chunk_size_m": CHUNK_SIZE_M,
                "bounds": bounds,
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

            with (
                chunk_dir / "metadata.json"
            ).open(
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

                features = data[data_type]

                if not features:
                    continue

                write_geojson(
                    chunk_dir / f"{data_type}.geojson",
                    features,
                )

    log(
        f"Chunks written: {len(all_chunk_names):,}"
    )


# ============================================================
# GRID
# ============================================================

def create_grid():

    _, _, cols, rows = get_grid_dimensions()

    features = []

    for row in range(rows):
        for col in range(cols):

            bounds = chunk_bounds(col, row)

            polygon = box(
                bounds["min_lon"],
                bounds["min_lat"],
                bounds["max_lon"],
                bounds["max_lat"],
            )

            features.append(
                {
                    "type": "Feature",
                    "geometry": mapping(polygon),
                    "properties": {
                        "chunk_id": chunk_name(
                            col,
                            row,
                        ),
                        "col": col,
                        "row": row,
                    },
                }
            )

    path = OUTPUT_DIR.parent / "grid.geojson"

    write_geojson(
        path,
        features,
    )


# ============================================================
# SUMMARY
# ============================================================

def create_summary(
    counters,
    feature_stats,
):

    width, height, cols, rows = (
        get_grid_dimensions()
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
            "columns": cols,
            "rows": rows,
            "total": cols * rows,
        },
        "dimensions_m": {
            "width": width,
            "height": height,
        },
        "features": feature_stats,
        "processing": counters,
    }

    path = OUTPUT_DIR.parent / "summary.json"

    with path.open(
        "w",
        encoding="utf-8",
    ) as f:

        json.dump(
            summary,
            f,
            indent=2,
        )


# ============================================================
# TAR
# ============================================================

def create_archive():

    archive = Path(
        "pune-1km-chunks.tar.gz"
    )

    if archive.exists():
        archive.unlink()

    log("")
    log("==========================================")
    log("CREATING ARCHIVE")
    log("==========================================")

    with tarfile.open(
        archive,
        "w:gz",
        compresslevel=1,
    ) as tar:

        tar.add(
            OUTPUT_DIR.parent,
            arcname="chunks",
        )

    size_mb = archive.stat().st_size / (
        1024 * 1024
    )

    log(
        f"Archive created: {archive}"
    )
    log(
        f"Archive size: {size_mb:.2f} MB"
    )


# ============================================================
# MAIN
# ============================================================

def main():

    log("")
    log("==========================================")
    log("PUNE 1 KM PROCESSOR")
    log("==========================================")

    if not shutil.which("osmium"):
        raise RuntimeError(
            "osmium command not found. "
            "Install osmium-tool first."
        )

    log("")
    log("OSM PBF:")
    log(f"  {PBF_PATH}")

    if not PBF_PATH.exists():
        raise FileNotFoundError(
            PBF_PATH
        )

    width, height, cols, rows = (
        get_grid_dimensions()
    )

    log("")
    log("BBOX:")
    log(f"  min_lon = {MIN_LON}")
    log(f"  min_lat = {MIN_LAT}")
    log(f"  max_lon = {MAX_LON}")
    log(f"  max_lat = {MAX_LAT}")

    log("")
    log("GRID:")
    log(f"  Width  : {width:.2f} m")
    log(f"  Height : {height:.2f} m")
    log(f"  Chunk  : {CHUNK_SIZE_M} m")
    log(f"  Grid   : {cols} x {rows}")
    log(f"  Total  : {cols * rows:,}")

    ensure_clean_directory(OUTPUT_DIR)
    ensure_clean_directory(TEMP_DIR)

    # --------------------------------------------------------
    # Filter PBF
    # --------------------------------------------------------

    buildings_pbf, roads_pbf, waterways_pbf = (
        filter_osm()
    )

    # --------------------------------------------------------
    # Export only relevant data
    # --------------------------------------------------------

    buildings_geojsonseq = (
        TEMP_DIR / "buildings.geojsonseq"
    )

    roads_geojsonseq = (
        TEMP_DIR / "roads.geojsonseq"
    )

    waterways_geojsonseq = (
        TEMP_DIR / "waterways.geojsonseq"
    )

    export_geojsonseq(
        buildings_pbf,
        buildings_geojsonseq,
    )

    export_geojsonseq(
        roads_pbf,
        roads_geojsonseq,
    )

    export_geojsonseq(
        waterways_pbf,
        waterways_geojsonseq,
    )

    # --------------------------------------------------------
    # Process
    # --------------------------------------------------------

    chunk_features = defaultdict(
        lambda: {
            "buildings": [],
            "roads": [],
            "waterways": [],
        }
    )

    counters = defaultdict(int)

    feature_stats = {}

    for data_type, path in [
        (
            "buildings",
            buildings_geojsonseq,
        ),
        (
            "roads",
            roads_geojsonseq,
        ),
        (
            "waterways",
            waterways_geojsonseq,
        ),
    ]:

        total, accepted = process_geojsonseq(
            path,
            data_type,
            chunk_features,
            counters,
        )

        feature_stats[data_type] = {
            "total": total,
            "accepted": accepted,
        }

    # --------------------------------------------------------
    # Output
    # --------------------------------------------------------

    create_chunk_output(
        chunk_features
    )

    create_grid()

    create_summary(
        counters,
        feature_stats,
    )

    create_archive()

    # --------------------------------------------------------
    # Final report
    # --------------------------------------------------------

    log("")
    log("==========================================")
    log("PROCESSING COMPLETE")
    log("==========================================")

    log(
        f"Active chunks: "
        f"{len(chunk_features):,}"
    )

    for data_type, stats in feature_stats.items():

        log(
            f"{data_type}: "
            f"{stats['accepted']:,} / "
            f"{stats['total']:,}"
        )

    log("")
    log("OUTPUT:")
    log(
        f"  {OUTPUT_DIR.parent}"
    )
    log(
        "  pune-1km-chunks.tar.gz"
    )


if __name__ == "__main__":

    try:
        main()

    except KeyboardInterrupt:

        log("")
        log("Processing cancelled.")

        sys.exit(130)

    except Exception as exc:

        log("")
        log("==========================================")
        log("PROCESSING FAILED")
        log("==========================================")
        log(str(exc))

        sys.exit(1)
