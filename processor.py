#!/usr/bin/env python3

import json
import math
import os
import shutil
import subprocess
import sys
from pathlib import Path

from shapely.geometry import box, shape


# ============================================================
# PUNE PROCESSOR v9
# ============================================================

VERSION = "v9"

RAW_PBF = Path("raw/central-zone.osm.pbf")

DATA_DIR = Path("data")
TMP_DIR = DATA_DIR / ".processor_tmp"
CHUNKS_DIR = DATA_DIR / "chunks"

GRID_GEOJSON = DATA_DIR / "grid.geojson"
SUMMARY_JSON = DATA_DIR / "summary.json"

# Pune processing bounding box
# xmin, ymin, xmax, ymax
PUNE_BOX = (
    73.70,
    18.40,
    74.05,
    18.70,
)

# Approximately 1 km around Pune.
CHUNK_SIZE = 0.01


# ============================================================
# LOGGING
# ============================================================

def log(message=""):
    print(message, flush=True)


def fail(message):
    log("")
    log("=" * 70)
    log("ERROR")
    log("=" * 70)
    log(message)
    sys.exit(1)


# ============================================================
# COMMAND HELPERS
# ============================================================

def run_command(command, description=None):
    if description:
        log("")
        log("=" * 70)
        log(description)
        log("=" * 70)

    log("$ " + " ".join(str(x) for x in command))

    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    if result.stdout:
        print(result.stdout, end="", flush=True)

    if result.returncode != 0:
        fail(
            "Command failed with exit code "
            f"{result.returncode}:\n"
            + " ".join(str(x) for x in command)
        )

    return result


def ensure_command(command):
    if shutil.which(command) is None:
        fail(f"Required command not found: {command}")


# ============================================================
# CLEANUP
# ============================================================

def clean_temp():
    if TMP_DIR.exists():
        shutil.rmtree(TMP_DIR)

    TMP_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )


def clean_chunks():
    if CHUNKS_DIR.exists():
        shutil.rmtree(CHUNKS_DIR)

    CHUNKS_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )


# ============================================================
# CHUNK FUNCTIONS
# ============================================================

def chunk_index(value, minimum):
    return int(
        math.floor(
            (value - minimum) / CHUNK_SIZE
        )
    )


def chunk_name(cx, cy):
    return f"PUNE_{cx:04d}_{cy:04d}"


def chunk_bounds(cx, cy):
    xmin, ymin, xmax, ymax = PUNE_BOX

    x0 = xmin + cx * CHUNK_SIZE
    y0 = ymin + cy * CHUNK_SIZE

    x1 = min(
        x0 + CHUNK_SIZE,
        xmax,
    )

    y1 = min(
        y0 + CHUNK_SIZE,
        ymax,
    )

    return (
        x0,
        y0,
        x1,
        y1,
    )


def feature_chunk_range(bounds):
    xmin, ymin, xmax, ymax = bounds

    pxmin, pymin, pxmax, pymax = PUNE_BOX

    # Clamp feature to Pune bbox.
    xmin = max(xmin, pxmin)
    ymin = max(ymin, pymin)
    xmax = min(xmax, pxmax)
    ymax = min(ymax, pymax)

    if xmin > xmax or ymin > ymax:
        return None

    cx0 = chunk_index(
        xmin,
        pxmin,
    )

    cy0 = chunk_index(
        ymin,
        pymin,
    )

    cx1 = chunk_index(
        max(
            xmin,
            xmax - 1e-12,
        ),
        pxmin,
    )

    cy1 = chunk_index(
        max(
            ymin,
            ymax - 1e-12,
        ),
        pymin,
    )

    return (
        cx0,
        cy0,
        cx1,
        cy1,
    )


# ============================================================
# GRID
# ============================================================

def create_grid():
    log("")
    log("=" * 70)
    log("CREATING 1KM GRID")
    log("=" * 70)

    xmin, ymin, xmax, ymax = PUNE_BOX

    width = xmax - xmin
    height = ymax - ymin

    nx = int(
        math.ceil(
            width / CHUNK_SIZE
        )
    )

    ny = int(
        math.ceil(
            height / CHUNK_SIZE
        )
    )

    features = []

    for cy in range(ny):
        for cx in range(nx):

            x0, y0, x1, y1 = chunk_bounds(
                cx,
                cy,
            )

            geometry = box(
                x0,
                y0,
                x1,
                y1,
            )

            features.append(
                {
                    "type": "Feature",
                    "properties": {
                        "chunk": chunk_name(
                            cx,
                            cy,
                        ),
                        "cx": cx,
                        "cy": cy,
                        "xmin": x0,
                        "ymin": y0,
                        "xmax": x1,
                        "ymax": y1,
                    },
                    "geometry": geometry.__geo_interface__,
                }
            )

    DATA_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    with GRID_GEOJSON.open(
        "w",
        encoding="utf-8",
    ) as f:
        json.dump(
            {
                "type": "FeatureCollection",
                "features": features,
            },
            f,
            separators=(",", ":"),
        )

    log(
        f"Grid: {nx} x {ny} = "
        f"{len(features)} chunks"
    )

    log(
        f"Saved: {GRID_GEOJSON}"
    )

    return nx, ny


# ============================================================
# OSM TAG FILTER
# ============================================================

def create_filtered_pbf():
    log("")
    log("=" * 70)
    log("FILTERING OSM DATA")
    log("=" * 70)

    buildings_pbf = (
        TMP_DIR /
        "buildings.osm.pbf"
    )

    roads_pbf = (
        TMP_DIR /
        "roads.osm.pbf"
    )

    waterways_pbf = (
        TMP_DIR /
        "waterways.osm.pbf"
    )

    # Buildings
    run_command(
        [
            "osmium",
            "tags-filter",
            str(RAW_PBF),
            "w/building",
            "r/building",
            "-o",
            str(buildings_pbf),
            "--overwrite",
        ],
        "FILTER BUILDINGS",
    )

    # Roads
    run_command(
        [
            "osmium",
            "tags-filter",
            str(RAW_PBF),
            "w/highway",
            "r/highway",
            "-o",
            str(roads_pbf),
            "--overwrite",
        ],
        "FILTER ROADS",
    )

    # Waterways
    run_command(
        [
            "osmium",
            "tags-filter",
            str(RAW_PBF),
            "w/waterway",
            "r/waterway",
            "-o",
            str(waterways_pbf),
            "--overwrite",
        ],
        "FILTER WATERWAYS",
    )

    for path in (
        buildings_pbf,
        roads_pbf,
        waterways_pbf,
    ):
        if not path.exists():
            fail(
                f"Missing filtered PBF: {path}"
            )

        if path.stat().st_size == 0:
            fail(
                f"Empty filtered PBF: {path}"
            )

        log(
            f"{path.name}: "
            f"{path.stat().st_size / 1024 / 1024:.2f} MB"
        )

    return (
        buildings_pbf,
        roads_pbf,
        waterways_pbf,
    )


# ============================================================
# GEOJSON EXPORT
# ============================================================

def export_geojsonseq(
    input_pbf,
    output_path,
    label,
    geometry_types,
):
    log("")
    log("=" * 70)
    log(
        f"EXPORTING {label}"
    )
    log("=" * 70)

    command = [
        "osmium",
        "export",
        str(input_pbf),
        "-o",
        str(output_path),
        "--overwrite",
        "--output-format=geojsonseq",
        "--geometry-types",
        geometry_types,
        "--format-option",
        "print_record_separator=false",
    ]

    run_command(
        command,
        f"OSMIUM EXPORT: {label}",
    )

    if not output_path.exists():
        fail(
            f"Missing GeoJSONSeq output: "
            f"{output_path}"
        )

    if output_path.stat().st_size == 0:
        fail(
            f"Empty GeoJSONSeq output: "
            f"{output_path}"
        )

    log(
        f"{output_path.name}: "
        f"{output_path.stat().st_size / 1024 / 1024:.2f} MB"
    )


# ============================================================
# DIAGNOSTIC
# ============================================================

def diagnostic_sample(
    path,
    label,
    limit=10,
):
    log("")
    log("=" * 70)
    log(
        f"DIAGNOSTIC SAMPLE: {label}"
    )
    log("=" * 70)

    pune_geometry = box(
        *PUNE_BOX
    )

    count = 0

    with path.open(
        "r",
        encoding="utf-8",
    ) as f:

        for raw_line in f:

            line = raw_line.strip()

            if not line:
                continue

            line = line.lstrip(
                "\x1e"
            )

            try:
                feature = json.loads(
                    line
                )
            except json.JSONDecodeError:
                continue

            geometry_json = feature.get(
                "geometry"
            )

            if not geometry_json:
                continue

            try:
                geometry = shape(
                    geometry_json
                )
            except Exception:
                continue

            if geometry.is_empty:
                continue

            count += 1

            properties = (
                feature.get(
                    "properties"
                )
                or {}
            )

            log("")
            log(
                f"Sample #{count}"
            )

            log(
                "  geometry_type = "
                f"{geometry.geom_type}"
            )

            log(
                "  bounds        = "
                f"{geometry.bounds}"
            )

            log(
                "  centroid      = "
                f"({geometry.centroid.x}, "
                f"{geometry.centroid.y})"
            )

            log(
                "  intersects    = "
                f"{geometry.intersects(pune_geometry)}"
            )

            log(
                "  within        = "
                f"{geometry.within(pune_geometry)}"
            )

            log(
                "  valid         = "
                f"{geometry.is_valid}"
            )

            log(
                "  empty         = "
                f"{geometry.is_empty}"
            )

            interesting_tags = {}

            for key in (
                "building",
                "highway",
                "waterway",
                "building:levels",
                "height",
                "name",
            ):
                if key in properties:
                    interesting_tags[key] = (
                        properties[key]
                    )

            log(
                "  tags          = "
                f"{interesting_tags}"
            )

            if count >= limit:
                break

    if count == 0:
        log(
            "WARNING: "
            "No valid geometries found."
        )


# ============================================================
# GEOMETRY NORMALIZATION
# ============================================================

def normalize_geometry(
    geometry_json
):
    if not geometry_json:
        return None

    try:
        geometry = shape(
            geometry_json
        )
    except Exception:
        return None

    if geometry.is_empty:
        return None

    if not geometry.is_valid:
        try:
            geometry = geometry.buffer(
                0
            )
        except Exception:
            return None

    if geometry.is_empty:
        return None

    minx, miny, maxx, maxy = (
        geometry.bounds
    )

    values = (
        minx,
        miny,
        maxx,
        maxy,
    )

    if not all(
        math.isfinite(v)
        for v in values
    ):
        return None

    # WGS84 sanity check.
    if (
        abs(minx) > 180
        or abs(maxx) > 180
        or abs(miny) > 90
        or abs(maxy) > 90
    ):
        return None

    return geometry


# ============================================================
# FEATURE PROCESSING
# ============================================================

def process_geojsonseq(
    input_path,
    feature_type,
    counters,
):
    log("")
    log("=" * 70)
    log(
        f"PROCESSING "
        f"{feature_type.upper()}"
    )
    log("=" * 70)

    pune_geometry = box(
        *PUNE_BOX
    )

    file_handles = {}

    try:
        with input_path.open(
            "r",
            encoding="utf-8",
        ) as source:

            for line_number, raw_line in enumerate(
                source,
                start=1,
            ):

                line = raw_line.strip()

                if not line:
                    continue

                line = line.lstrip(
                    "\x1e"
                )

                try:
                    feature = json.loads(
                        line
                    )
                except json.JSONDecodeError:
                    counters[
                        "invalid_json"
                    ] += 1
                    continue

                counters[
                    "total_features"
                ] += 1

                geometry = normalize_geometry(
                    feature.get(
                        "geometry"
                    )
                )

                if geometry is None:
                    counters[
                        "no_geometry"
                    ] += 1
                    continue

                counters[
                    "geometry_features"
                ] += 1

                if not geometry.intersects(
                    pune_geometry
                ):
                    counters[
                        "outside_bbox"
                    ] += 1
                    continue

                counters[
                    "inside_bbox"
                ] += 1

                bounds = geometry.bounds

                ranges = feature_chunk_range(
                    bounds
                )

                if ranges is None:
                    counters[
                        "outside_bbox"
                    ] += 1
                    continue

                cx0, cy0, cx1, cy1 = (
                    ranges
                )

                for cy in range(
                    cy0,
                    cy1 + 1,
                ):

                    for cx in range(
                        cx0,
                        cx1 + 1,
                    ):

                        chunk_box = box(
                            *chunk_bounds(
                                cx,
                                cy,
                            )
                        )

                        if not geometry.intersects(
                            chunk_box
                        ):
                            continue

                        try:
                            clipped = (
                                geometry.intersection(
                                    chunk_box
                                )
                            )
                        except Exception:
                            counters[
                                "clip_error"
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

                        chunk = chunk_name(
                            cx,
                            cy,
                        )

                        chunk_dir = (
                            CHUNKS_DIR /
                            chunk
                        )

                        chunk_dir.mkdir(
                            parents=True,
                            exist_ok=True,
                        )

                        output_path = (
                            chunk_dir /
                            f"{feature_type}.geojson"
                        )

                        if chunk not in file_handles:
                            file_handles[
                                chunk
                            ] = output_path.open(
                                "a",
                                encoding="utf-8",
                            )

                        properties = (
                            feature.get(
                                "properties"
                            )
                            or {}
                        )

                        output_feature = {
                            "type": "Feature",
                            "properties": properties,
                            "geometry": (
                                clipped.__geo_interface__
                            ),
                        }

                        file_handles[
                            chunk
                        ].write(
                            json.dumps(
                                output_feature,
                                separators=(
                                    ",",
                                    ":",
                                ),
                            )
                            + "\n"
                        )

                        counters[
                            "written_features"
                        ] += 1

                        counters[
                            "chunks"
                        ].add(chunk)

                if (
                    line_number % 100000
                    == 0
                ):
                    log(
                        f"{feature_type}: "
                        f"read="
                        f"{counters['total_features']:,} "
                        f"geometry="
                        f"{counters['geometry_features']:,} "
                        f"inside="
                        f"{counters['inside_bbox']:,} "
                        f"written="
                        f"{counters['written_features']:,} "
                        f"chunks="
                        f"{len(counters['chunks']):,}"
                    )

    finally:
        for handle in file_handles.values():
            try:
                handle.close()
            except Exception:
                pass


# ============================================================
# METADATA
# ============================================================

def create_chunk_metadata():
    log("")
    log("=" * 70)
    log("CREATING CHUNK METADATA")
    log("=" * 70)

    chunk_dirs = sorted(
        p
        for p in CHUNKS_DIR.iterdir()
        if p.is_dir()
        and p.name.startswith(
            "PUNE_"
        )
    )

    for chunk_dir in chunk_dirs:

        try:
            parts = chunk_dir.name.split(
                "_"
            )

            cx = int(parts[1])
            cy = int(parts[2])

        except Exception:
            continue

        x0, y0, x1, y1 = (
            chunk_bounds(
                cx,
                cy,
            )
        )

        metadata = {
            "processor_version": VERSION,
            "chunk": chunk_dir.name,
            "cx": cx,
            "cy": cy,
            "bbox": {
                "xmin": x0,
                "ymin": y0,
                "xmax": x1,
                "ymax": y1,
            },
            "center": {
                "lon": (
                    x0 + x1
                ) / 2,
                "lat": (
                    y0 + y1
                ) / 2,
            },
            "files": {},
        }

        for feature_type in (
            "buildings",
            "roads",
            "waterways",
        ):

            path = (
                chunk_dir /
                f"{feature_type}.geojson"
            )

            if path.exists():
                metadata[
                    "files"
                ][feature_type] = {
                    "path": path.name,
                    "size_bytes": (
                        path.stat().st_size
                    ),
                }

        with (
            chunk_dir /
            "metadata.json"
        ).open(
            "w",
            encoding="utf-8",
        ) as f:

            json.dump(
                metadata,
                f,
                indent=2,
            )

    log(
        f"Chunk directories: "
        f"{len(chunk_dirs)}"
    )


# ============================================================
# COUNTER CREATION
# ============================================================

def create_counters():
    return {
        "total_features": 0,
        "geometry_features": 0,
        "inside_bbox": 0,
        "outside_bbox": 0,
        "no_geometry": 0,
        "invalid_json": 0,
        "invalid_chunk": 0,
        "clip_error": 0,
        "written_features": 0,
        "chunks": set(),
    }


# ============================================================
# SUMMARY
# ============================================================

def counters_for_json(
    counters
):
    result = dict(
        counters
    )

    result["chunks"] = len(
        counters["chunks"]
    )

    return result


def create_summary(
    building_stats,
    road_stats,
    water_stats,
    grid_count,
):
    chunk_dirs = [
        p
        for p in CHUNKS_DIR.iterdir()
        if p.is_dir()
        and p.name.startswith(
            "PUNE_"
        )
    ]

    summary = {
        "version": VERSION,
        "processor": "python",
        "bbox": {
            "xmin": PUNE_BOX[0],
            "ymin": PUNE_BOX[1],
            "xmax": PUNE_BOX[2],
            "ymax": PUNE_BOX[3],
        },
        "chunk_size_degrees": CHUNK_SIZE,
        "grid_chunks": grid_count,
        "generated_chunks": len(
            chunk_dirs
        ),
        "features": {
            "buildings": counters_for_json(
                building_stats
            ),
            "roads": counters_for_json(
                road_stats
            ),
            "waterways": counters_for_json(
                water_stats
            ),
        },
    }

    with SUMMARY_JSON.open(
        "w",
        encoding="utf-8",
    ) as f:
        json.dump(
            summary,
            f,
            indent=2,
        )

    log("")
    log("=" * 70)
    log("SUMMARY")
    log("=" * 70)

    log(
        json.dumps(
            summary,
            indent=2,
        )
    )

    return summary


# ============================================================
# MAIN
# ============================================================

def main():

    log("=" * 70)
    log(
        f"PUNE 1KM PROCESSOR {VERSION}"
    )
    log("=" * 70)

    log(
        f"Python: {sys.version}"
    )

    log(
        f"Raw PBF: {RAW_PBF}"
    )

    log(
        f"Pune bbox: {PUNE_BOX}"
    )

    log(
        f"Chunk size: {CHUNK_SIZE}"
    )

    ensure_command(
        "osmium"
    )

    if not RAW_PBF.exists():
        fail(
            f"Raw PBF not found: "
            f"{RAW_PBF}"
        )

    if RAW_PBF.stat().st_size == 0:
        fail(
            f"Raw PBF is empty: "
            f"{RAW_PBF}"
        )

    log(
        f"Raw PBF size: "
        f"{RAW_PBF.stat().st_size / 1024 / 1024:.2f} MB"
    )

    DATA_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    clean_temp()
    clean_chunks()

    # --------------------------------------------------------
    # GRID
    # --------------------------------------------------------

    nx, ny = create_grid()

    grid_count = nx * ny

    # --------------------------------------------------------
    # OSM FILTER
    # --------------------------------------------------------

    (
        buildings_pbf,
        roads_pbf,
        waterways_pbf,
    ) = create_filtered_pbf()

    # --------------------------------------------------------
    # GEOJSONSEQ
    # --------------------------------------------------------

    buildings_geojson = (
        TMP_DIR /
        "buildings.geojsonseq"
    )

    roads_geojson = (
        TMP_DIR /
        "roads.geojsonseq"
    )

    waterways_geojson = (
        TMP_DIR /
        "waterways.geojsonseq"
    )

    # IMPORTANT:
    #
    # Buildings:
    #   polygon + linestring
    #
    # Roads:
    #   linestring
    #
    # Waterways:
    #   linestring
    #
    # Point geometries are intentionally excluded.
    #
    # This prevents referenced OSM nodes from becoming
    # millions of irrelevant point features.

    export_geojsonseq(
        buildings_pbf,
        buildings_geojson,
        "BUILDINGS",
        "linestring,polygon",
    )

    export_geojsonseq(
        roads_pbf,
        roads_geojson,
        "ROADS",
        "linestring",
    )

    export_geojsonseq(
        waterways_pbf,
        waterways_geojson,
        "WATERWAYS",
        "linestring",
    )

    # --------------------------------------------------------
    # DIAGNOSTICS
    # --------------------------------------------------------

    diagnostic_sample(
        buildings_geojson,
        "BUILDINGS",
    )

    diagnostic_sample(
        roads_geojson,
        "ROADS",
    )

    diagnostic_sample(
        waterways_geojson,
        "WATERWAYS",
    )

    # --------------------------------------------------------
    # PROCESS
    # --------------------------------------------------------

    building_stats = (
        create_counters()
    )

    road_stats = (
        create_counters()
    )

    water_stats = (
        create_counters()
    )

    process_geojsonseq(
        buildings_geojson,
        "buildings",
        building_stats,
    )

    process_geojsonseq(
        roads_geojson,
        "roads",
        road_stats,
    )

    process_geojsonseq(
        waterways_geojson,
        "waterways",
        water_stats,
    )

    # --------------------------------------------------------
    # CHUNK VALIDATION
    # --------------------------------------------------------

    generated_chunks = [
        p
        for p in CHUNKS_DIR.iterdir()
        if p.is_dir()
        and p.name.startswith(
            "PUNE_"
        )
    ]

    generated_chunk_count = len(
        generated_chunks
    )

    log("")
    log("=" * 70)
    log("PROCESSING RESULT")
    log("=" * 70)

    log(
        "Buildings:"
    )

    log(
        f"  total     = "
        f"{building_stats['total_features']:,}"
    )

    log(
        f"  geometry  = "
        f"{building_stats['geometry_features']:,}"
    )

    log(
        f"  inside    = "
        f"{building_stats['inside_bbox']:,}"
    )

    log(
        f"  written   = "
        f"{building_stats['written_features']:,}"
    )

    log(
        f"  chunks    = "
        f"{len(building_stats['chunks']):,}"
    )

    log("")

    log(
        "Roads:"
    )

    log(
        f"  total     = "
        f"{road_stats['total_features']:,}"
    )

    log(
        f"  geometry  = "
        f"{road_stats['geometry_features']:,}"
    )

    log(
        f"  inside    = "
        f"{road_stats['inside_bbox']:,}"
    )

    log(
        f"  written   = "
        f"{road_stats['written_features']:,}"
    )

    log(
        f"  chunks    = "
        f"{len(road_stats['chunks']):,}"
    )

    log("")

    log(
        "Waterways:"
    )

    log(
        f"  total     = "
        f"{water_stats['total_features']:,}"
    )

    log(
        f"  geometry  = "
        f"{water_stats['geometry_features']:,}"
    )

    log(
        f"  inside    = "
        f"{water_stats['inside_bbox']:,}"
    )

    log(
        f"  written   = "
        f"{water_stats['written_features']:,}"
    )

    log(
        f"  chunks    = "
        f"{len(water_stats['chunks']):,}"
    )

    log("")

    log(
        f"Generated chunks: "
        f"{generated_chunk_count}"
    )

    if generated_chunk_count == 0:
        fail(
            "No Pune chunks were generated."
        )

    # --------------------------------------------------------
    # METADATA
    # --------------------------------------------------------

    create_chunk_metadata()

    # --------------------------------------------------------
    # SUMMARY
    # --------------------------------------------------------

    summary = create_summary(
        building_stats,
        road_stats,
        water_stats,
        grid_count,
    )

    # --------------------------------------------------------
    # FINAL VALIDATION
    # --------------------------------------------------------

    if summary[
        "generated_chunks"
    ] <= 0:
        fail(
            "Final validation failed: "
            "generated_chunks == 0"
        )

    if (
        building_stats[
            "written_features"
        ] == 0
    ):
        log(
            "WARNING: "
            "No buildings were written."
        )

    if (
        road_stats[
            "written_features"
        ] == 0
    ):
        log(
            "WARNING: "
            "No roads were written."
        )

    log("")
    log("=" * 70)
    log(
        "FINAL VALIDATION PASSED"
    )
    log("=" * 70)

    log(
        f"Grid chunks       : "
        f"{grid_count}"
    )

    log(
        f"Generated chunks  : "
        f"{generated_chunk_count}"
    )

    log(
        f"Buildings written : "
        f"{building_stats['written_features']:,}"
    )

    log(
        f"Roads written     : "
        f"{road_stats['written_features']:,}"
    )

    log(
        f"Waterways written : "
        f"{water_stats['written_features']:,}"
    )

    log("")
    log(
        "PROCESSOR COMPLETED SUCCESSFULLY"
    )


if __name__ == "__main__":
    main()
