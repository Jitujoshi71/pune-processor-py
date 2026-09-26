#!/usr/bin/env python3

import json
import os
import shutil
import subprocess
import sys
from collections import OrderedDict
from pathlib import Path

from shapely.geometry import box, shape, mapping


# ============================================================
# PUNE PROCESSOR v5
# ============================================================

VERSION = "v5"

# ------------------------------------------------------------
# Source
# ------------------------------------------------------------

RAW_PBF = Path("raw/central-zone.osm.pbf")

# ------------------------------------------------------------
# Output
# ------------------------------------------------------------

DATA_DIR = Path("data")
TMP_DIR = DATA_DIR / ".processor_tmp"
CHUNKS_DIR = DATA_DIR / "chunks"

ARCHIVE = Path("pune-1km-chunks.tar.gz")

# ------------------------------------------------------------
# Pune geographic extent
# ------------------------------------------------------------

MIN_LON = 73.70
MIN_LAT = 18.40
MAX_LON = 74.05
MAX_LAT = 18.70

# Approximately 1 km at Pune latitude.
# We keep the existing 0.01 degree grid requested.
CHUNK_SIZE_DEG = 0.01

# ------------------------------------------------------------
# Safety
# ------------------------------------------------------------

MAX_OPEN_FILES = 48

# ============================================================
# OSM FILTERS
# ============================================================

BUILDING_FILTERS = [
    "w/building",
    "r/building",
]

ROAD_FILTERS = [
    "w/highway",
    "r/highway",
]

WATERWAY_FILTERS = [
    "w/waterway",
    "r/waterway",
]

# ============================================================
# HELPERS
# ============================================================


def log(message=""):
    print(message, flush=True)


def run(cmd, cwd=None):
    cmd = [str(x) for x in cmd]

    log()
    log("=" * 70)
    log("[CMD] " + " ".join(cmd))
    log("=" * 70)

    result = subprocess.run(
        cmd,
        cwd=cwd,
        stdout=None,
        stderr=None,
        text=True,
    )

    if result.returncode != 0:
        raise RuntimeError(
            "Command failed with exit code "
            f"{result.returncode}: {' '.join(cmd)}"
        )

    return result


def require_nonempty_file(path: Path, label=None):
    label = label or str(path)

    if not path.exists():
        raise RuntimeError(
            f"{label} does not exist: {path}"
        )

    size = path.stat().st_size

    if size <= 0:
        raise RuntimeError(
            f"{label} is EMPTY: {path}"
        )

    return size


def file_mb(path: Path):
    return path.stat().st_size / 1024 / 1024


def write_json(path: Path, data):
    path.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    with open(
        path,
        "w",
        encoding="utf-8"
    ) as f:
        json.dump(
            data,
            f,
            ensure_ascii=False,
            indent=2
        )


# ============================================================
# GRID
# ============================================================


def create_grid():
    log()
    log("=" * 70)
    log("CREATING 1KM GRID")
    log("=" * 70)

    columns = int(
        round(
            (MAX_LON - MIN_LON) /
            CHUNK_SIZE_DEG
        )
    )

    rows = int(
        round(
            (MAX_LAT - MIN_LAT) /
            CHUNK_SIZE_DEG
        )
    )

    features = []

    for x in range(columns):
        for y in range(rows):

            minx = (
                MIN_LON +
                x * CHUNK_SIZE_DEG
            )

            miny = (
                MIN_LAT +
                y * CHUNK_SIZE_DEG
            )

            maxx = min(
                minx + CHUNK_SIZE_DEG,
                MAX_LON
            )

            maxy = min(
                miny + CHUNK_SIZE_DEG,
                MAX_LAT
            )

            name = f"PUNE_{x:04d}_{y:04d}"

            features.append({
                "type": "Feature",
                "properties": {
                    "chunk": name,
                    "x": x,
                    "y": y,
                },
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[
                        [minx, miny],
                        [maxx, miny],
                        [maxx, maxy],
                        [minx, maxy],
                        [minx, miny],
                    ]]
                }
            })

    grid = {
        "type": "FeatureCollection",
        "features": features
    }

    write_json(
        DATA_DIR / "grid.geojson",
        grid
    )

    log(
        f"Grid: {columns} x {rows} = "
        f"{len(features):,} chunks"
    )

    return columns, rows


# ============================================================
# PUNE EXTRACT
# ============================================================


def create_pune_extract():
    """
    Create a geographically clipped PBF first.

    IMPORTANT:
    Correct osmium syntax:

        osmium extract
        --bbox LEFT,BOTTOM,RIGHT,TOP
        INPUT
        -o OUTPUT
    """

    log()
    log("=" * 70)
    log("CREATING PUNE GEOGRAPHIC EXTRACT")
    log("=" * 70)

    output = TMP_DIR / "pune.osm.pbf"

    if output.exists():
        output.unlink()

    bbox = (
        f"{MIN_LON},"
        f"{MIN_LAT},"
        f"{MAX_LON},"
        f"{MAX_LAT}"
    )

    run([
        "osmium",
        "extract",
        "--bbox",
        bbox,
        str(RAW_PBF),
        "-o",
        str(output),
        "--overwrite",
    ])

    size = require_nonempty_file(
        output,
        "Pune PBF"
    )

    log(
        f"Pune PBF size: "
        f"{size / 1024 / 1024:.2f} MB"
    )

    # --------------------------------------------------------
    # File information
    # --------------------------------------------------------

    run([
        "osmium",
        "fileinfo",
        "--extended",
        str(output),
    ])

    return output


# ============================================================
# FILTER
# ============================================================


def create_filtered_pbf(
    pune_pbf: Path,
    name: str,
    expressions,
):
    log()
    log("=" * 70)
    log(f"FILTER {name.upper()}")
    log("=" * 70)

    output = TMP_DIR / f"{name}.osm.pbf"

    if output.exists():
        output.unlink()

    cmd = [
        "osmium",
        "tags-filter",
        str(pune_pbf),
    ]

    cmd.extend(expressions)

    cmd.extend([
        "-o",
        str(output),
        "--overwrite",
    ])

    run(cmd)

    size = require_nonempty_file(
        output,
        f"{name} filtered PBF"
    )

    log(
        f"{name} PBF: "
        f"{size / 1024 / 1024:.2f} MB"
    )

    return output


# ============================================================
# EXPORT
# ============================================================


def export_geojsonseq(
    pbf: Path,
    name: str
):
    log()
    log("=" * 70)
    log(f"EXPORT {name.upper()}")
    log("=" * 70)

    output = (
        TMP_DIR /
        f"{name}.geojsonseq"
    )

    if output.exists():
        output.unlink()

    run([
        "osmium",
        "export",
        str(pbf),
        "--output-format=geojsonseq",
        "--format-option=print_record_separator=false",
        "-o",
        str(output),
        "--overwrite",
    ])

    size = require_nonempty_file(
        output,
        f"{name} GeoJSONSeq"
    )

    log(
        f"{name} GeoJSONSeq: "
        f"{size / 1024 / 1024:.2f} MB"
    )

    return output


# ============================================================
# FEATURE READER
# ============================================================


def read_geojsonseq_line(line):
    """
    GeoJSON Text Sequence can contain:
      - normal newline separated JSON
      - RS (0x1e) record separators

    Handle both safely.
    """

    line = line.strip()

    if not line:
        return None

    if line.startswith("\x1e"):
        line = line[1:].lstrip()

    if not line:
        return None

    return json.loads(line)


# ============================================================
# LRU WRITER
# ============================================================


class ChunkWriter:

    def __init__(
        self,
        root: Path,
        max_open=48
    ):
        self.root = root
        self.max_open = max_open

        self.handles = OrderedDict()

        self.created_chunks = set()

    def _open(
        self,
        chunk_name,
        feature_type
    ):
        key = (
            chunk_name,
            feature_type
        )

        if key in self.handles:

            fh = self.handles.pop(key)

            self.handles[key] = fh

            return fh

        if len(self.handles) >= self.max_open:

            old_key, old_handle = (
                self.handles.popitem(
                    last=False
                )
            )

            old_handle.close()

        chunk_dir = (
            self.root /
            chunk_name
        )

        chunk_dir.mkdir(
            parents=True,
            exist_ok=True
        )

        path = (
            chunk_dir /
            f"{feature_type}.geojsonseq"
        )

        fh = open(
            path,
            "a",
            encoding="utf-8"
        )

        self.handles[key] = fh

        self.created_chunks.add(
            chunk_name
        )

        return fh

    def write(
        self,
        chunk_name,
        feature_type,
        feature
    ):
        fh = self._open(
            chunk_name,
            feature_type
        )

        fh.write(
            json.dumps(
                feature,
                ensure_ascii=False,
                separators=(",", ":")
            )
        )

        fh.write("\n")

    def close(self):
        for fh in self.handles.values():
            try:
                fh.close()
            except Exception:
                pass

        self.handles.clear()


# ============================================================
# CHUNK CALCULATION
# ============================================================


def chunk_name(x, y):
    return f"PUNE_{x:04d}_{y:04d}"


def chunk_indices_for_geometry(
    geom
):
    if geom.is_empty:
        return []

    minx, miny, maxx, maxy = (
        geom.bounds
    )

    # Clamp to Pune
    minx = max(
        minx,
        MIN_LON
    )

    miny = max(
        miny,
        MIN_LAT
    )

    maxx = min(
        maxx,
        MAX_LON
    )

    maxy = min(
        maxy,
        MAX_LAT
    )

    if minx >= maxx or miny >= maxy:
        return []

    columns = int(
        round(
            (MAX_LON - MIN_LON) /
            CHUNK_SIZE_DEG
        )
    )

    rows = int(
        round(
            (MAX_LAT - MIN_LAT) /
            CHUNK_SIZE_DEG
        )
    )

    x0 = int(
        (minx - MIN_LON) /
        CHUNK_SIZE_DEG
    )

    y0 = int(
        (miny - MIN_LAT) /
        CHUNK_SIZE_DEG
    )

    x1 = int(
        (maxx - MIN_LON) /
        CHUNK_SIZE_DEG
    )

    y1 = int(
        (maxy - MIN_LAT) /
        CHUNK_SIZE_DEG
    )

    x0 = max(
        0,
        min(
            x0,
            columns - 1
        )
    )

    x1 = max(
        0,
        min(
            x1,
            columns - 1
        )
    )

    y0 = max(
        0,
        min(
            y0,
            rows - 1
        )
    )

    y1 = max(
        0,
        min(
            y1,
            rows - 1
        )
    )

    result = []

    for x in range(
        x0,
        x1 + 1
    ):
        for y in range(
            y0,
            y1 + 1
        ):
            result.append(
                (x, y)
            )

    return result


def clip_to_chunk(
    geom,
    x,
    y
):
    minx = (
        MIN_LON +
        x * CHUNK_SIZE_DEG
    )

    miny = (
        MIN_LAT +
        y * CHUNK_SIZE_DEG
    )

    maxx = min(
        minx + CHUNK_SIZE_DEG,
        MAX_LON
    )

    maxy = min(
        miny + CHUNK_SIZE_DEG,
        MAX_LAT
    )

    tile = box(
        minx,
        miny,
        maxx,
        maxy
    )

    if not geom.intersects(tile):
        return None

    clipped = geom.intersection(
        tile
    )

    if clipped.is_empty:
        return None

    return clipped


# ============================================================
# SAMPLE DIAGNOSTIC
# ============================================================


def diagnostic_sample(
    path: Path,
    name: str,
    count=5
):
    log()
    log("=" * 70)
    log(f"DIAGNOSTIC SAMPLE: {name.upper()}")
    log("=" * 70)

    pune_box = box(
        MIN_LON,
        MIN_LAT,
        MAX_LON,
        MAX_LAT
    )

    total = 0
    valid = 0
    intersects = 0

    with open(
        path,
        "r",
        encoding="utf-8"
    ) as f:

        for line in f:

            if total >= count:
                break

            try:
                feature = (
                    read_geojsonseq_line(
                        line
                    )
                )

                if feature is None:
                    continue

                total += 1

                geometry_json = (
                    feature.get(
                        "geometry"
                    )
                )

                properties = (
                    feature.get(
                        "properties",
                        {}
                    )
                )

                log()
                log(
                    f"FEATURE #{total}"
                )

                log(
                    "Geometry type: "
                    + str(
                        (
                            geometry_json or {}
                        ).get(
                            "type"
                        )
                    )
                )

                log(
                    "Properties keys: "
                    + str(
                        list(
                            properties.keys()
                        )[:30]
                    )
                )

                log(
                    "building="
                    + str(
                        properties.get(
                            "building"
                        )
                    )
                )

                log(
                    "highway="
                    + str(
                        properties.get(
                            "highway"
                        )
                    )
                )

                log(
                    "waterway="
                    + str(
                        properties.get(
                            "waterway"
                        )
                    )
                )

                if not geometry_json:
                    log(
                        "NO GEOMETRY"
                    )
                    continue

                geom = shape(
                    geometry_json
                )

                valid += 1

                log(
                    "Bounds: "
                    + str(
                        tuple(
                            round(
                                float(v),
                                6
                            )
                            for v in geom.bounds
                        )
                    )
                )

                log(
                    "Centroid: "
                    + str(
                        (
                            round(
                                geom.centroid.x,
                                6
                            ),
                            round(
                                geom.centroid.y,
                                6
                            )
                        )
                    )
                )

                hit = geom.intersects(
                    pune_box
                )

                log(
                    "Intersects Pune bbox: "
                    + str(hit)
                )

                if hit:
                    intersects += 1

            except Exception as e:
                log(
                    "DIAGNOSTIC ERROR: "
                    + repr(e)
                )

    log()
    log(
        f"Sampled: {total}"
    )
    log(
        f"Valid geometries: {valid}"
    )
    log(
        f"Intersects Pune: {intersects}"
    )

    if total == 0:
        raise RuntimeError(
            f"{name} GeoJSONSeq contains "
            "no readable features."
        )

    return intersects


# ============================================================
# PROCESS FEATURES
# ============================================================


def process_geojsonseq(
    path: Path,
    feature_type: str,
    writer: ChunkWriter
):
    log()
    log("=" * 70)
    log(
        f"PROCESSING {feature_type.upper()}"
    )
    log(
        f"FILE: {path}"
    )
    log("=" * 70)

    pune_box = box(
        MIN_LON,
        MIN_LAT,
        MAX_LON,
        MAX_LAT
    )

    total = 0
    accepted = 0
    chunk_features = 0

    invalid_json = 0
    no_geometry = 0
    invalid_geometry = 0
    empty_geometry = 0
    outside_bbox = 0
    intersection_errors = 0

    with open(
        path,
        "r",
        encoding="utf-8"
    ) as f:

        for raw_line in f:

            if not raw_line.strip():
                continue

            try:
                feature = (
                    read_geojsonseq_line(
                        raw_line
                    )
                )

            except Exception:
                invalid_json += 1
                continue

            if feature is None:
                continue

            total += 1

            geometry_json = (
                feature.get(
                    "geometry"
                )
            )

            if not geometry_json:
                no_geometry += 1
                continue

            try:
                geom = shape(
                    geometry_json
                )

            except Exception:
                invalid_geometry += 1
                continue

            if geom.is_empty:
                empty_geometry += 1
                continue

            try:
                if not geom.intersects(
                    pune_box
                ):
                    outside_bbox += 1
                    continue

            except Exception:
                intersection_errors += 1
                continue

            accepted += 1

            indices = (
                chunk_indices_for_geometry(
                    geom
                )
            )

            for x, y in indices:

                try:
                    clipped = (
                        clip_to_chunk(
                            geom,
                            x,
                            y
                        )
                    )

                except Exception:
                    intersection_errors += 1
                    continue

                if clipped is None:
                    continue

                out_feature = dict(
                    feature
                )

                out_feature[
                    "geometry"
                ] = mapping(
                    clipped
                )

                writer.write(
                    chunk_name(x, y),
                    feature_type,
                    out_feature
                )

                chunk_features += 1

            if total % 100_000 == 0:
                log(
                    f"[{feature_type}] "
                    f"total={total:,} "
                    f"accepted={accepted:,} "
                    f"chunk_features={chunk_features:,}"
                )

    result = {
        "total": total,
        "accepted": accepted,
        "chunk_features": chunk_features,
        "invalid_json": invalid_json,
        "no_geometry": no_geometry,
        "invalid_geometry": invalid_geometry,
        "empty_geometry": empty_geometry,
        "outside_bbox": outside_bbox,
        "intersection_errors": intersection_errors,
    }

    log()
    log(
        f"[{feature_type}] COMPLETE"
    )

    for key, value in result.items():
        log(
            f"  {key}: {value:,}"
            if isinstance(value, int)
            else f"  {key}: {value}"
        )

    return result


# ============================================================
# FINALIZE CHUNKS
# ============================================================


def finalize_chunks():
    log()
    log("=" * 70)
    log("FINALIZING CHUNKS")
    log("=" * 70)

    chunk_count = 0
    total_features = 0

    for chunk_dir in sorted(
        CHUNKS_DIR.iterdir()
    ):

        if not chunk_dir.is_dir():
            continue

        chunk_features = 0

        for feature_type in (
            "buildings",
            "roads",
            "waterways",
        ):

            seq = (
                chunk_dir /
                f"{feature_type}.geojsonseq"
            )

            if not seq.exists():
                continue

            features = []

            with open(
                seq,
                "r",
                encoding="utf-8"
            ) as f:

                for line in f:

                    if not line.strip():
                        continue

                    try:
                        feature = (
                            read_geojsonseq_line(
                                line
                            )
                        )

                        if feature is not None:
                            features.append(
                                feature
                            )

                    except Exception:
                        continue

            if not features:
                try:
                    seq.unlink()
                except Exception:
                    pass

                continue

            output = (
                chunk_dir /
                f"{feature_type}.geojson"
            )

            write_json(
                output,
                {
                    "type":
                        "FeatureCollection",
                    "features":
                        features,
                }
            )

            chunk_features += len(
                features
            )

            total_features += len(
                features
            )

            try:
                seq.unlink()
            except Exception:
                pass

        if chunk_features == 0:
            try:
                shutil.rmtree(
                    chunk_dir
                )
            except Exception:
                pass

            continue

        metadata = {
            "chunk":
                chunk_dir.name,
            "features":
                chunk_features,
            "files":
                sorted(
                    p.name
                    for p in chunk_dir.iterdir()
                    if p.is_file()
                )
        }

        write_json(
            chunk_dir /
            "metadata.json",
            metadata
        )

        chunk_count += 1

    log(
        f"Active chunks: "
        f"{chunk_count:,}"
    )

    log(
        f"Chunk features: "
        f"{total_features:,}"
    )

    return (
        chunk_count,
        total_features
    )


# ============================================================
# SUMMARY
# ============================================================


def create_summary(
    columns,
    rows,
    stats
):
    active_chunks = 0

    if CHUNKS_DIR.exists():
        active_chunks = sum(
            1
            for p in CHUNKS_DIR.iterdir()
            if p.is_dir()
        )

    summary = {
        "processor_version":
            VERSION,

        "bbox": {
            "min_lon":
                MIN_LON,
            "min_lat":
                MIN_LAT,
            "max_lon":
                MAX_LON,
            "max_lat":
                MAX_LAT,
        },

        "chunk_size_m":
            1000,

        "grid": {
            "columns":
                columns,
            "rows":
                rows,
            "total":
                columns * rows,
        },

        "features":
            stats,

        "active_chunks":
            active_chunks,
    }

    write_json(
        DATA_DIR / "summary.json",
        summary
    )

    return summary


# ============================================================
# ARCHIVE
# ============================================================


def create_archive():
    log()
    log("=" * 70)
    log("CREATING ARCHIVE")
    log("=" * 70)

    if ARCHIVE.exists():
        ARCHIVE.unlink()

    run([
        "tar",
        "-czf",
        str(ARCHIVE),
        "-C",
        str(DATA_DIR.parent),
        DATA_DIR.name,
    ])

    size = require_nonempty_file(
        ARCHIVE,
        "processor archive"
    )

    log(
        f"Archive size: "
        f"{size / 1024 / 1024:.2f} MB"
    )

    return size


# ============================================================
# VALIDATION
# ============================================================


def validate_output(
    chunk_count,
    stats
):
    log()
    log("=" * 70)
    log("VALIDATING OUTPUT")
    log("=" * 70)

    if chunk_count <= 0:
        raise RuntimeError(
            "ZERO ACTIVE CHUNKS. "
            "Refusing to create a successful result."
        )

    buildings_accepted = (
        stats["buildings"]["accepted"]
    )

    roads_accepted = (
        stats["roads"]["accepted"]
    )

    waterways_accepted = (
        stats["waterways"]["accepted"]
    )

    if buildings_accepted <= 0:
        raise RuntimeError(
            "ZERO BUILDINGS ACCEPTED."
        )

    if roads_accepted <= 0:
        raise RuntimeError(
            "ZERO ROADS ACCEPTED."
        )

    if waterways_accepted <= 0:
        log(
            "WARNING: zero waterways accepted."
        )

    building_files = list(
        CHUNKS_DIR.glob(
            "*/buildings.geojson"
        )
    )

    road_files = list(
        CHUNKS_DIR.glob(
            "*/roads.geojson"
        )
    )

    if not building_files:
        raise RuntimeError(
            "No buildings.geojson files found."
        )

    if not road_files:
        raise RuntimeError(
            "No roads.geojson files found."
        )

    log(
        f"Building files: "
        f"{len(building_files):,}"
    )

    log(
        f"Road files: "
        f"{len(road_files):,}"
    )

    log(
        f"Waterway files: "
        f"{len(list(CHUNKS_DIR.glob('*/waterways.geojson'))):,}"
    )

    log(
        "OUTPUT VALIDATION: PASS"
    )


# ============================================================
# MAIN
# ============================================================


def main():

    log()
    log("=" * 70)
    log(f"PUNE 1KM PROCESSOR {VERSION}")
    log("GEOGRAPHIC EXTRACT + DIRECT TAG FILTER")
    log("=" * 70)

    # --------------------------------------------------------
    # Basic checks
    # --------------------------------------------------------

    require_nonempty_file(
        RAW_PBF,
        "Raw central-zone PBF"
    )

    log(
        f"Raw PBF: "
        f"{RAW_PBF}"
    )

    log(
        f"Raw PBF size: "
        f"{file_mb(RAW_PBF):.2f} MB"
    )

    # --------------------------------------------------------
    # Clean previous output
    # --------------------------------------------------------

    if DATA_DIR.exists():
        shutil.rmtree(
            DATA_DIR
        )

    if ARCHIVE.exists():
        ARCHIVE.unlink()

    DATA_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    TMP_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    CHUNKS_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    # --------------------------------------------------------
    # Grid
    # --------------------------------------------------------

    columns, rows = (
        create_grid()
    )

    # --------------------------------------------------------
    # IMPORTANT:
    # First geographically isolate Pune.
    # --------------------------------------------------------

    pune_pbf = (
        create_pune_extract()
    )

    # --------------------------------------------------------
    # Filter each feature type
    # --------------------------------------------------------

    buildings_pbf = (
        create_filtered_pbf(
            pune_pbf,
            "buildings",
            BUILDING_FILTERS
        )
    )

    roads_pbf = (
        create_filtered_pbf(
            pune_pbf,
            "roads",
            ROAD_FILTERS
        )
    )

    waterways_pbf = (
        create_filtered_pbf(
            pune_pbf,
            "waterways",
            WATERWAY_FILTERS
        )
    )

    # --------------------------------------------------------
    # Export
    # --------------------------------------------------------

    buildings_geo = (
        export_geojsonseq(
            buildings_pbf,
            "buildings"
        )
    )

    roads_geo = (
        export_geojsonseq(
            roads_pbf,
            "roads"
        )
    )

    waterways_geo = (
        export_geojsonseq(
            waterways_pbf,
            "waterways"
        )
    )

    # --------------------------------------------------------
    # Diagnostic
    # --------------------------------------------------------

    diagnostic_sample(
        buildings_geo,
        "buildings"
    )

    diagnostic_sample(
        roads_geo,
        "roads"
    )

    diagnostic_sample(
        waterways_geo,
        "waterways"
    )

    # --------------------------------------------------------
    # Process
    # --------------------------------------------------------

    writer = ChunkWriter(
        CHUNKS_DIR,
        MAX_OPEN_FILES
    )

    try:

        building_stats = (
            process_geojsonseq(
                buildings_geo,
                "buildings",
                writer
            )
        )

        road_stats = (
            process_geojsonseq(
                roads_geo,
                "roads",
                writer
            )
        )

        waterway_stats = (
            process_geojsonseq(
                waterways_geo,
                "waterways",
                writer
            )
        )

    finally:
        writer.close()

    stats = {
        "buildings":
            building_stats,
        "roads":
            road_stats,
        "waterways":
            waterway_stats,
    }

    # --------------------------------------------------------
    # Finalize
    # --------------------------------------------------------

    chunk_count, total_features = (
        finalize_chunks()
    )

    # --------------------------------------------------------
    # Summary
    # --------------------------------------------------------

    summary = create_summary(
        columns,
        rows,
        stats
    )

    # --------------------------------------------------------
    # Hard validation
    # --------------------------------------------------------

    validate_output(
        chunk_count,
        stats
    )

    # --------------------------------------------------------
    # Archive
    # --------------------------------------------------------

    create_archive()

    # --------------------------------------------------------
    # Print final summary
    # --------------------------------------------------------

    log()
    log("=" * 70)
    log("PROCESSING COMPLETE")
    log("=" * 70)

    log(
        f"Active chunks: "
        f"{chunk_count:,}"
    )

    log(
        f"Total chunk features: "
        f"{total_features:,}"
    )

    log(
        f"buildings: "
        f"{building_stats['accepted']:,} / "
        f"{building_stats['total']:,}"
    )

    log(
        f"roads: "
        f"{road_stats['accepted']:,} / "
        f"{road_stats['total']:,}"
    )

    log(
        f"waterways: "
        f"{waterway_stats['accepted']:,} / "
        f"{waterway_stats['total']:,}"
    )

    log()
    log("Output:")
    log("  data/chunks")
    log("  data/grid.geojson")
    log("  data/summary.json")
    log("  pune-1km-chunks.tar.gz")


# ============================================================
# ENTRY POINT
# ============================================================


if __name__ == "__main__":

    try:
        main()

    except KeyboardInterrupt:
        log(
            "\nInterrupted."
        )
        sys.exit(130)

    except Exception as e:
        log()
        log("=" * 70)
        log("PROCESSING FAILED")
        log("=" * 70)
        log(
            f"{type(e).__name__}: {e}"
        )
        sys.exit(1)
