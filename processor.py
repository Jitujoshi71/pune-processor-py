#!/usr/bin/env python3

import json
import math
import os
import shutil
import subprocess
import sys
from pathlib import Path

from shapely.geometry import box, shape
from shapely.ops import transform


# ============================================================
# CONFIG
# ============================================================

RAW_PBF = Path("raw/central-zone.osm.pbf")

DATA_DIR = Path("data")
TMP_DIR = DATA_DIR / ".processor_tmp"
CHUNKS_DIR = DATA_DIR / "chunks"

GRID_GEOJSON = DATA_DIR / "grid.geojson"
SUMMARY_JSON = DATA_DIR / "summary.json"

# Pune processing bounding box
#
# xmin, ymin, xmax, ymax
PUNE_BOX = (73.70, 18.40, 74.05, 18.70)

# 1 km-ish geographic chunks
# 0.01 degree ~= 1 km around Pune
CHUNK_SIZE = 0.01

# ============================================================
# HELPERS
# ============================================================


def log(message: str):
    print(message, flush=True)


def fail(message: str):
    log("")
    log("=" * 70)
    log("ERROR")
    log("=" * 70)
    log(message)
    sys.exit(1)


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
            f"Command failed with exit code {result.returncode}:\n"
            + " ".join(str(x) for x in command)
        )

    return result


def ensure_command(command):
    if shutil.which(command) is None:
        fail(f"Required command not found: {command}")


def clean_temp():
    if TMP_DIR.exists():
        shutil.rmtree(TMP_DIR)

    TMP_DIR.mkdir(parents=True, exist_ok=True)


def clean_chunks():
    if CHUNKS_DIR.exists():
        shutil.rmtree(CHUNKS_DIR)

    CHUNKS_DIR.mkdir(parents=True, exist_ok=True)


def safe_float(value):
    try:
        return float(value)
    except Exception:
        return None


# ============================================================
# CHUNK HELPERS
# ============================================================


def chunk_index(value: float, minimum: float) -> int:
    return int(math.floor((value - minimum) / CHUNK_SIZE))


def chunk_name(cx: int, cy: int) -> str:
    return f"PUNE_{cx:04d}_{cy:04d}"


def chunk_bounds(cx: int, cy: int):
    xmin, ymin, _, _ = PUNE_BOX

    x0 = xmin + cx * CHUNK_SIZE
    y0 = ymin + cy * CHUNK_SIZE

    x1 = x0 + CHUNK_SIZE
    y1 = y0 + CHUNK_SIZE

    return x0, y0, x1, y1


def feature_chunk_range(feature_bounds):
    xmin, ymin, xmax, ymax = feature_bounds

    pxmin, pymin, pxmax, pymax = PUNE_BOX

    xmin = max(xmin, pxmin)
    ymin = max(ymin, pymin)
    xmax = min(xmax, pxmax)
    ymax = min(ymax, pymax)

    if xmin > xmax or ymin > ymax:
        return None

    cx0 = chunk_index(xmin, pxmin)
    cy0 = chunk_index(ymin, pymin)

    # Subtract tiny epsilon so an object exactly on a chunk
    # boundary does not unnecessarily create the next chunk.
    cx1 = chunk_index(max(xmin, xmax - 1e-12), pxmin)
    cy1 = chunk_index(max(ymin, ymax - 1e-12), pymin)

    return cx0, cy0, cx1, cy1


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

    nx = int(math.ceil(width / CHUNK_SIZE))
    ny = int(math.ceil(height / CHUNK_SIZE))

    features = []

    for cy in range(ny):
        for cx in range(nx):
            x0, y0, x1, y1 = chunk_bounds(cx, cy)

            # Clip final edge cells to Pune bbox.
            x1 = min(x1, xmax)
            y1 = min(y1, ymax)

            geom = box(x0, y0, x1, y1)

            features.append(
                {
                    "type": "Feature",
                    "properties": {
                        "chunk": chunk_name(cx, cy),
                        "cx": cx,
                        "cy": cy,
                        "xmin": x0,
                        "ymin": y0,
                        "xmax": x1,
                        "ymax": y1,
                    },
                    "geometry": geom.__geo_interface__,
                }
            )

    GRID_GEOJSON.parent.mkdir(parents=True, exist_ok=True)

    with GRID_GEOJSON.open("w", encoding="utf-8") as f:
        json.dump(
            {
                "type": "FeatureCollection",
                "features": features,
            },
            f,
            separators=(",", ":"),
        )

    log(f"Grid: {nx} x {ny} = {len(features)} chunks")
    log(f"Saved: {GRID_GEOJSON}")

    return nx, ny


# ============================================================
# OSM FILTER
# ============================================================


def create_filtered_pbf():
    log("")
    log("=" * 70)
    log("FILTERING OSM DATA")
    log("=" * 70)

    building_pbf = TMP_DIR / "buildings.osm.pbf"
    road_pbf = TMP_DIR / "roads.osm.pbf"
    waterway_pbf = TMP_DIR / "waterways.osm.pbf"

    # Direct filtering is used on the raw PBF.
    run_command(
        [
            "osmium",
            "tags-filter",
            str(RAW_PBF),
            "w/building",
            "r/building",
            "-o",
            str(building_pbf),
            "--overwrite",
        ],
        "FILTER BUILDINGS",
    )

    run_command(
        [
            "osmium",
            "tags-filter",
            str(RAW_PBF),
            "w/highway",
            "r/highway",
            "-o",
            str(road_pbf),
            "--overwrite",
        ],
        "FILTER ROADS",
    )

    run_command(
        [
            "osmium",
            "tags-filter",
            str(RAW_PBF),
            "w/waterway",
            "r/waterway",
            "-o",
            str(waterway_pbf),
            "--overwrite",
        ],
        "FILTER WATERWAYS",
    )

    for path in (building_pbf, road_pbf, waterway_pbf):
        if not path.exists() or path.stat().st_size == 0:
            fail(f"Filtered PBF missing or empty: {path}")

        log(f"{path.name}: {path.stat().st_size / 1024 / 1024:.2f} MB")

    return building_pbf, road_pbf, waterway_pbf


# ============================================================
# GEOJSON EXPORT
# ============================================================


def export_geojsonseq(input_pbf: Path, output_path: Path, label: str):
    log("")
    log("=" * 70)
    log(f"EXPORTING {label}")
    log("=" * 70)

    run_command(
        [
            "osmium",
            "export",
            str(input_pbf),
            "-o",
            str(output_path),
            "--overwrite",
            "--output-format=geojsonseq",
        ],
        f"OSMIUM EXPORT: {label}",
    )

    if not output_path.exists() or output_path.stat().st_size == 0:
        fail(f"GeoJSONSeq output is empty: {output_path}")

    log(
        f"{output_path.name}: "
        f"{output_path.stat().st_size / 1024 / 1024:.2f} MB"
    )


# ============================================================
# DIAGNOSTICS
# ============================================================


def diagnostic_sample(path: Path, label: str, limit: int = 10):
    log("")
    log("=" * 70)
    log(f"DIAGNOSTIC SAMPLE: {label}")
    log("=" * 70)

    pune_geom = box(*PUNE_BOX)

    count = 0

    with path.open("r", encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()

            if not line:
                continue

            # GeoJSONSeq can contain RS before JSON.
            line = line.lstrip("\x1e")

            try:
                feature = json.loads(line)
            except json.JSONDecodeError:
                continue

            count += 1

            geometry = feature.get("geometry")
            properties = feature.get("properties") or {}

            log("")
            log(f"Sample #{count}")
            log(f"  geometry_type = {geometry.get('type') if geometry else None}")

            if geometry:
                try:
                    geom = shape(geometry)

                    log(f"  bounds        = {geom.bounds}")
                    log(f"  centroid      = ({geom.centroid.x}, {geom.centroid.y})")
                    log(f"  intersects    = {geom.intersects(pune_geom)}")
                    log(f"  within        = {geom.within(pune_geom)}")
                    log(f"  valid         = {geom.is_valid}")
                    log(f"  empty         = {geom.is_empty}")
                except Exception as exc:
                    log(f"  geometry_error = {exc}")

            tags = {}

            for key in (
                "building",
                "highway",
                "waterway",
                "building:levels",
                "height",
                "name",
            ):
                if key in properties:
                    tags[key] = properties[key]

            log(f"  tags          = {tags}")

            if count >= limit:
                break

    if count == 0:
        log("WARNING: No readable GeoJSONSeq features found in diagnostic sample.")


# ============================================================
# FEATURE PROCESSING
# ============================================================


def normalize_feature(feature):
    geometry = feature.get("geometry")

    if not geometry:
        return None

    try:
        geom = shape(geometry)
    except Exception:
        return None

    if geom.is_empty:
        return None

    if not geom.is_valid:
        try:
            geom = geom.buffer(0)
        except Exception:
            return None

    if geom.is_empty:
        return None

    # Geometry must have geographic coordinates.
    minx, miny, maxx, maxy = geom.bounds

    if not all(
        math.isfinite(v)
        for v in (minx, miny, maxx, maxy)
    ):
        return None

    # Reject obviously invalid coordinate systems.
    if (
        abs(minx) > 180
        or abs(maxx) > 180
        or abs(miny) > 90
        or abs(maxy) > 90
    ):
        return None

    return geom


def process_geojsonseq(
    input_path: Path,
    feature_type: str,
    counters: dict,
):
    log("")
    log("=" * 70)
    log(f"PROCESSING {feature_type.upper()}")
    log("=" * 70)

    file_handles = {}

    try:
        with input_path.open("r", encoding="utf-8") as source:
            for line_number, raw_line in enumerate(source, start=1):
                line = raw_line.strip()

                if not line:
                    continue

                line = line.lstrip("\x1e")

                try:
                    feature = json.loads(line)
                except json.JSONDecodeError:
                    counters["invalid_json"] += 1
                    continue

                counters["total_features"] += 1

                properties = feature.get("properties") or {}

                geometry = normalize_feature(feature)

                if geometry is None:
                    counters["no_geometry"] += 1
                    continue

                counters["geometry_features"] += 1

                if not geometry.intersects(box(*PUNE_BOX)):
                    counters["outside_bbox"] += 1
                    continue

                counters["inside_bbox"] += 1

                bounds = geometry.bounds

                ranges = feature_chunk_range(bounds)

                if ranges is None:
                    counters["outside_bbox"] += 1
                    continue

                cx0, cy0, cx1, cy1 = ranges

                # Prevent accidental pathological values.
                if (
                    cx0 < 0
                    or cy0 < 0
                    or cx1 < 0
                    or cy1 < 0
                ):
                    counters["invalid_chunk"] += 1
                    continue

                # Clip geometry to each touched chunk.
                for cy in range(cy0, cy1 + 1):
                    for cx in range(cx0, cx1 + 1):

                        cb = box(*chunk_bounds(cx, cy))

                        if not geometry.intersects(cb):
                            continue

                        try:
                            clipped = geometry.intersection(cb)
                        except Exception:
                            counters["clip_error"] += 1
                            continue

                        if clipped.is_empty:
                            continue

                        if not clipped.is_valid:
                            try:
                                clipped = clipped.buffer(0)
                            except Exception:
                                continue

                        if clipped.is_empty:
                            continue

                        out_feature = {
                            "type": "Feature",
                            "properties": properties,
                            "geometry": clipped.__geo_interface__,
                        }

                        name = chunk_name(cx, cy)

                        chunk_dir = CHUNKS_DIR / name
                        chunk_dir.mkdir(parents=True, exist_ok=True)

                        output_path = chunk_dir / f"{feature_type}.geojson"

                        if name not in file_handles:
                            file_handles[name] = output_path.open(
                                "w",
                                encoding="utf-8",
                            )

                        file_handles[name].write(
                            json.dumps(
                                out_feature,
                                separators=(",", ":"),
                            )
                            + "\n"
                        )

                        counters["written_features"] += 1

                        if name not in counters["chunks"]:
                            counters["chunks"].add(name)

                if line_number % 100000 == 0:
                    log(
                        f"{feature_type}: "
                        f"read={counters['total_features']:,} "
                        f"inside={counters['inside_bbox']:,} "
                        f"written={counters['written_features']:,} "
                        f"chunks={len(counters['chunks']):,}"
                    )

    finally:
        for handle in file_handles.values():
            try:
                handle.close()
            except Exception:
                pass


# ============================================================
# CHUNK METADATA
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
        and p.name.startswith("PUNE_")
    )

    for chunk_dir in chunk_dirs:
        try:
            parts = chunk_dir.name.split("_")

            cx = int(parts[1])
            cy = int(parts[2])
        except Exception:
            continue

        x0, y0, x1, y1 = chunk_bounds(cx, cy)

        metadata = {
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
                "lon": (x0 + x1) / 2,
                "lat": (y0 + y1) / 2,
            },
            "files": {},
        }

        for feature_type in (
            "buildings",
            "roads",
            "waterways",
        ):
            path = chunk_dir / f"{feature_type}.geojson"

            if path.exists():
                metadata["files"][feature_type] = {
                    "path": path.name,
                    "size_bytes": path.stat().st_size,
                }

        with (chunk_dir / "metadata.json").open(
            "w",
            encoding="utf-8",
        ) as f:
            json.dump(
                metadata,
                f,
                indent=2,
            )

    log(f"Chunk directories: {len(chunk_dirs)}")


# ============================================================
# SUMMARY
# ============================================================


def summarize(
    building_stats,
    road_stats,
    water_stats,
    grid_count,
):
    chunk_dirs = sorted(
        p
        for p in CHUNKS_DIR.iterdir()
        if p.is_dir()
        and p.name.startswith("PUNE_")
    )

    summary = {
        "version": "v7",
        "processor": "python",
        "bbox": {
            "xmin": PUNE_BOX[0],
            "ymin": PUNE_BOX[1],
            "xmax": PUNE_BOX[2],
            "ymax": PUNE_BOX[3],
        },
        "chunk_size_degrees": CHUNK_SIZE,
        "grid_chunks": grid_count,
        "generated_chunks": len(chunk_dirs),
        "features": {
            "buildings": building_stats,
            "roads": road_stats,
            "waterways": water_stats,
        },
    }

    SUMMARY_JSON.parent.mkdir(parents=True, exist_ok=True)

    with SUMMARY_JSON.open("w", encoding="utf-8") as f:
        json.dump(
            summary,
            f,
            indent=2,
        )

    log("")
    log("=" * 70)
    log("SUMMARY")
    log("=" * 70)

    log(json.dumps(summary, indent=2))

    return summary


# ============================================================
# MAIN
# ============================================================


def main():
    log("=" * 70)
    log("PUNE 1KM PROCESSOR v7")
    log("=" * 70)

    log(f"Python: {sys.version}")
    log(f"Raw PBF: {RAW_PBF}")
    log(f"Pune bbox: {PUNE_BOX}")
    log(f"Chunk size: {CHUNK_SIZE}")

    ensure_command("osmium")

    if not RAW_PBF.exists():
        fail(f"Raw PBF not found: {RAW_PBF}")

    if RAW_PBF.stat().st_size == 0:
        fail(f"Raw PBF is empty: {RAW_PBF}")

    log(
        f"Raw PBF size: "
        f"{RAW_PBF.stat().st_size / 1024 / 1024:.2f} MB"
    )

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    clean_temp()
    clean_chunks()

    nx, ny = create_grid()
    grid_count = nx * ny

    building_pbf, road_pbf, waterway_pbf = create_filtered_pbf()

    buildings_geojson = TMP_DIR / "buildings.geojsonseq"
    roads_geojson = TMP_DIR / "roads.geojsonseq"
    waterways_geojson = TMP_DIR / "waterways.geojsonseq"

    export_geojsonseq(
        building_pbf,
        buildings_geojson,
        "BUILDINGS",
    )

    export_geojsonseq(
        road_pbf,
        roads_geojson,
        "ROADS",
    )

    export_geojsonseq(
        waterway_pbf,
        waterways_geojson,
        "WATERWAYS",
    )

    # Important diagnostic before processing millions of objects.
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

    building_stats = {
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

    road_stats = {
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

    water_stats = {
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

    # Sets are not JSON serializable.
    for stats in (
        building_stats,
        road_stats,
        water_stats,
    ):
        stats["chunks"] = len(stats["chunks"])

    total_generated_chunks = len(
        [
            p
            for p in CHUNKS_DIR.iterdir()
            if p.is_dir()
            and p.name.startswith("PUNE_")
        ]
    )

    if total_generated_chunks == 0:
        fail(
            "No Pune chunks were generated.\n"
            "Check the diagnostic sample above for geometry "
            "coordinates/bounds."
        )

    if building_stats["written_features"] == 0:
        log("")
        log("WARNING: No building features were written.")

    if road_stats["written_features"] == 0:
        log("")
        log("WARNING: No road features were written.")

    create_chunk_metadata()

    summary = summarize(
        building_stats,
        road_stats,
        water_stats,
        grid_count,
    )

    # Final safety checks.
    if summary["generated_chunks"] == 0:
        fail("Final validation failed: generated_chunks == 0")

    log("")
    log("=" * 70)
    log("FINAL VALIDATION")
    log("=" * 70)

    log(f"Grid chunks       : {summary['grid_chunks']}")
    log(f"Generated chunks  : {summary['generated_chunks']}")
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
    log("PROCESSOR COMPLETED SUCCESSFULLY")


if __name__ == "__main__":
    main()
