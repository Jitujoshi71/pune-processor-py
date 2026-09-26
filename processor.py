#!/usr/bin/env python3

import json
import shutil
import subprocess
import sys
from collections import OrderedDict
from pathlib import Path

from shapely.geometry import box, shape, mapping


# ============================================================
# PUNE PROCESSOR v7
# ============================================================

VERSION = "v7"

RAW_PBF = Path("raw/central-zone.osm.pbf")

DATA_DIR = Path("data")
TMP_DIR = DATA_DIR / ".processor_tmp"
CHUNKS_DIR = DATA_DIR / "chunks"

ARCHIVE = Path("pune-1km-chunks.tar.gz")

# ============================================================
# PUNE BBOX
# ============================================================

MIN_LON = 73.70
MIN_LAT = 18.40
MAX_LON = 74.05
MAX_LAT = 18.70

CHUNK_SIZE_DEG = 0.01

MAX_OPEN_FILES = 48

PUNE_BOX = box(
    MIN_LON,
    MIN_LAT,
    MAX_LON,
    MAX_LAT
)


# ============================================================
# COMMANDS
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
# BASIC HELPERS
# ============================================================

def log(message=""):
    print(message, flush=True)


def run(cmd):
    cmd = [str(x) for x in cmd]

    log()
    log("=" * 70)
    log("[CMD] " + " ".join(cmd))
    log("=" * 70)

    result = subprocess.run(
        cmd,
        text=True
    )

    if result.returncode != 0:
        raise RuntimeError(
            f"Command failed ({result.returncode}): "
            + " ".join(cmd)
        )


def require_file(path, label):
    if not path.exists():
        raise RuntimeError(
            f"{label} does not exist: {path}"
        )

    size = path.stat().st_size

    if size == 0:
        raise RuntimeError(
            f"{label} is empty: {path}"
        )

    return size


def write_json(path, data):
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

    columns = int(
        round(
            (MAX_LON - MIN_LON)
            / CHUNK_SIZE_DEG
        )
    )

    rows = int(
        round(
            (MAX_LAT - MIN_LAT)
            / CHUNK_SIZE_DEG
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

            features.append({
                "type": "Feature",

                "properties": {
                    "chunk":
                        f"PUNE_{x:04d}_{y:04d}",
                    "x": x,
                    "y": y
                },

                "geometry": {
                    "type": "Polygon",

                    "coordinates": [[
                        [minx, miny],
                        [maxx, miny],
                        [maxx, maxy],
                        [minx, maxy],
                        [minx, miny]
                    ]]
                }
            })

    write_json(
        DATA_DIR / "grid.geojson",
        {
            "type":
                "FeatureCollection",
            "features":
                features
        }
    )

    log()
    log("=" * 70)
    log("GRID")
    log("=" * 70)
    log(
        f"Columns: {columns}"
    )
    log(
        f"Rows: {rows}"
    )
    log(
        f"Total: {len(features):,}"
    )

    return columns, rows


# ============================================================
# FILTER RAW PBF DIRECTLY
# ============================================================

def filter_pbf(
    name,
    expressions
):

    output = (
        TMP_DIR /
        f"{name}.osm.pbf"
    )

    if output.exists():
        output.unlink()

    cmd = [
        "osmium",
        "tags-filter",
        str(RAW_PBF),
    ]

    cmd.extend(expressions)

    cmd.extend([
        "-o",
        str(output),
        "--overwrite"
    ])

    run(cmd)

    size = require_file(
        output,
        f"{name} filtered PBF"
    )

    log(
        f"{name} size: "
        f"{size / 1024 / 1024:.2f} MB"
    )

    return output


# ============================================================
# EXPORT
# ============================================================

def export_pbf(
    pbf,
    name
):

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
        "-o",
        str(output),
        "--overwrite"
    ])

    size = require_file(
        output,
        f"{name} GeoJSONSeq"
    )

    log(
        f"{name} GeoJSONSeq size: "
        f"{size / 1024 / 1024:.2f} MB"
    )

    return output


# ============================================================
# GEOJSONSEQ PARSER
# ============================================================

def parse_line(line):

    line = line.strip()

    if not line:
        return None

    # GeoJSON Sequence record separator
    if line.startswith("\x1e"):
        line = line[1:].lstrip()

    if not line:
        return None

    return json.loads(line)


# ============================================================
# DIAGNOSTIC
# ============================================================

def diagnostic(
    path,
    name
):

    log()
    log("=" * 70)
    log(
        f"DIAGNOSTIC: {name.upper()}"
    )
    log("=" * 70)

    total = 0
    valid = 0
    intersects = 0

    min_seen_lon = None
    max_seen_lon = None
    min_seen_lat = None
    max_seen_lat = None

    with open(
        path,
        "r",
        encoding="utf-8"
    ) as f:

        for line in f:

            if total >= 10:
                break

            try:
                feature = parse_line(
                    line
                )
            except Exception as e:
                log(
                    "JSON error:",
                    e
                )
                continue

            if feature is None:
                continue

            total += 1

            geometry = feature.get(
                "geometry"
            )

            properties = feature.get(
                "properties",
                {}
            )

            log()
            log(
                f"FEATURE {total}"
            )

            log(
                "Geometry:",
                geometry.get("type")
                if geometry
                else None
            )

            if geometry:

                try:

                    geom = shape(
                        geometry
                    )

                    valid += 1

                    bounds = geom.bounds

                    log(
                        "Bounds:",
                        tuple(
                            round(
                                float(v),
                                6
                            )
                            for v in bounds
                        )
                    )

                    log(
                        "Centroid:",
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

                    if geom.intersects(
                        PUNE_BOX
                    ):
                        intersects += 1

                    min_lon, min_lat, max_lon, max_lat = bounds

                    if min_seen_lon is None:
                        min_seen_lon = min_lon
                        max_seen_lon = max_lon
                        min_seen_lat = min_lat
                        max_seen_lat = max_lat
                    else:
                        min_seen_lon = min(
                            min_seen_lon,
                            min_lon
                        )
                        max_seen_lon = max(
                            max_seen_lon,
                            max_lon
                        )
                        min_seen_lat = min(
                            min_seen_lat,
                            min_lat
                        )
                        max_seen_lat = max(
                            max_seen_lat,
                            max_lat
                        )

                except Exception as e:

                    log(
                        "Geometry error:",
                        repr(e)
                    )

            log(
                "building:",
                properties.get(
                    "building"
                )
            )

            log(
                "highway:",
                properties.get(
                    "highway"
                )
            )

            log(
                "waterway:",
                properties.get(
                    "waterway"
                )
            )

    log()
    log(
        f"Sampled: {total}"
    )

    log(
        f"Valid: {valid}"
    )

    log(
        f"Intersects Pune: {intersects}"
    )

    if min_seen_lon is not None:

        log()
        log(
            "Sample coordinate range:"
        )

        log(
            f"Longitude: "
            f"{min_seen_lon:.6f} -> "
            f"{max_seen_lon:.6f}"
        )

        log(
            f"Latitude: "
            f"{min_seen_lat:.6f} -> "
            f"{max_seen_lat:.6f}"
        )

    return {
        "sampled":
            total,
        "valid":
            valid,
        "intersects":
            intersects
    }


# ============================================================
# LRU CHUNK WRITER
# ============================================================

class ChunkWriter:

    def __init__(
        self,
        root,
        max_open=48
    ):

        self.root = root
        self.max_open = max_open

        self.handles = OrderedDict()

    def get_handle(
        self,
        chunk,
        feature_type
    ):

        key = (
            chunk,
            feature_type
        )

        if key in self.handles:

            fh = self.handles.pop(
                key
            )

            self.handles[key] = fh

            return fh

        if (
            len(self.handles)
            >= self.max_open
        ):

            old_key, old_fh = (
                self.handles.popitem(
                    last=False
                )
            )

            old_fh.close()

        chunk_dir = (
            self.root /
            chunk
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

        return fh

    def write(
        self,
        chunk,
        feature_type,
        feature
    ):

        fh = self.get_handle(
            chunk,
            feature_type
        )

        fh.write(
            json.dumps(
                feature,
                ensure_ascii=False,
                separators=(
                    ",",
                    ":"
                )
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
# CHUNK HELPERS
# ============================================================

def chunk_name(
    x,
    y
):
    return (
        f"PUNE_{x:04d}_{y:04d}"
    )


def geometry_chunks(
    geom
):

    if geom.is_empty:
        return []

    minx, miny, maxx, maxy = (
        geom.bounds
    )

    if (
        maxx < MIN_LON or
        minx > MAX_LON or
        maxy < MIN_LAT or
        miny > MAX_LAT
    ):
        return []

    minx = max(
        minx,
        MIN_LON
    )

    maxx = min(
        maxx,
        MAX_LON
    )

    miny = max(
        miny,
        MIN_LAT
    )

    maxy = min(
        maxy,
        MAX_LAT
    )

    columns = int(
        round(
            (MAX_LON - MIN_LON)
            / CHUNK_SIZE_DEG
        )
    )

    rows = int(
        round(
            (MAX_LAT - MIN_LAT)
            / CHUNK_SIZE_DEG
        )
    )

    x0 = int(
        (minx - MIN_LON)
        / CHUNK_SIZE_DEG
    )

    x1 = int(
        (maxx - MIN_LON)
        / CHUNK_SIZE_DEG
    )

    y0 = int(
        (miny - MIN_LAT)
        / CHUNK_SIZE_DEG
    )

    y1 = int(
        (maxy - MIN_LAT)
        / CHUNK_SIZE_DEG
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
                (
                    x,
                    y
                )
            )

    return result


def clip_geometry(
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

    if not geom.intersects(
        tile
    ):
        return None

    clipped = geom.intersection(
        tile
    )

    if clipped.is_empty:
        return None

    return clipped


# ============================================================
# PROCESS
# ============================================================

def process_file(
    path,
    feature_type,
    writer
):

    log()
    log("=" * 70)
    log(
        f"PROCESSING {feature_type.upper()}"
    )
    log("=" * 70)

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

        for line in f:

            if not line.strip():
                continue

            try:

                feature = parse_line(
                    line
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
                    PUNE_BOX
                ):

                    outside_bbox += 1
                    continue

            except Exception:

                intersection_errors += 1
                continue

            accepted += 1

            for x, y in (
                geometry_chunks(
                    geom
                )
            ):

                try:

                    clipped = (
                        clip_geometry(
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
                    chunk_name(
                        x,
                        y
                    ),
                    feature_type,
                    out_feature
                )

                chunk_features += 1

            if total % 100000 == 0:

                log(
                    f"{feature_type}: "
                    f"{total:,} total | "
                    f"{accepted:,} accepted | "
                    f"{chunk_features:,} chunk features"
                )

    result = {
        "total":
            total,
        "accepted":
            accepted,
        "chunk_features":
            chunk_features,
        "invalid_json":
            invalid_json,
        "no_geometry":
            no_geometry,
        "invalid_geometry":
            invalid_geometry,
        "empty_geometry":
            empty_geometry,
        "outside_bbox":
            outside_bbox,
        "intersection_errors":
            intersection_errors
    }

    log()
    log(
        f"[{feature_type}] COMPLETE"
    )

    for key, value in result.items():

        log(
            f"  {key}: {value:,}"
        )

    return result


# ============================================================
# FINALIZE
# ============================================================

def finalize_chunks():

    log()
    log("=" * 70)
    log("FINALIZING CHUNKS")
    log("=" * 70)

    chunk_count = 0
    total_features = 0

    if not CHUNKS_DIR.exists():
        return 0, 0

    for chunk_dir in sorted(
        CHUNKS_DIR.iterdir()
    ):

        if not chunk_dir.is_dir():
            continue

        chunk_features = 0

        for feature_type in (
            "buildings",
            "roads",
            "waterways"
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
                            parse_line(
                                line
                            )
                        )

                        if feature:
                            features.append(
                                feature
                            )

                    except Exception:
                        pass

            if not features:

                seq.unlink(
                    missing_ok=True
                )

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
                        features
                }
            )

            chunk_features += (
                len(features)
            )

            total_features += (
                len(features)
            )

            seq.unlink(
                missing_ok=True
            )

        if chunk_features == 0:

            shutil.rmtree(
                chunk_dir,
                ignore_errors=True
            )

            continue

        write_json(
            chunk_dir /
            "metadata.json",
            {
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
        )

        chunk_count += 1

    log(
        f"Active chunks: "
        f"{chunk_count:,}"
    )

    log(
        f"Total chunk features: "
        f"{total_features:,}"
    )

    return (
        chunk_count,
        total_features
    )


# ============================================================
# SUMMARY
# ============================================================

def write_summary(
    columns,
    rows,
    stats,
    chunk_count,
    total_features
):

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
                MAX_LAT
        },

        "chunk_size_deg":
            CHUNK_SIZE_DEG,

        "chunk_size_m":
            1000,

        "grid": {
            "columns":
                columns,
            "rows":
                rows,
            "total":
                columns * rows
        },

        "features":
            stats,

        "active_chunks":
            chunk_count,

        "total_chunk_features":
            total_features
    }

    write_json(
        DATA_DIR /
        "summary.json",
        summary
    )


# ============================================================
# ARCHIVE
# ============================================================

def create_archive():

    if ARCHIVE.exists():
        ARCHIVE.unlink()

    log()
    log("=" * 70)
    log("CREATING ARCHIVE")
    log("=" * 70)

    run([
        "tar",
        "-czf",
        str(ARCHIVE),
        "-C",
        str(DATA_DIR.parent),
        DATA_DIR.name
    ])

    size = require_file(
        ARCHIVE,
        "archive"
    )

    log(
        f"Archive size: "
        f"{size / 1024 / 1024:.2f} MB"
    )


# ============================================================
# MAIN
# ============================================================

def main():

    log("=" * 70)
    log("PUNE 1KM PROCESSOR v7")
    log("DIRECT FILTER -> GEOMETRY -> PUNE BBOX -> CHUNKS")
    log("=" * 70)

    # --------------------------------------------------------
    # RAW
    # --------------------------------------------------------

    raw_size = require_file(
        RAW_PBF,
        "Raw PBF"
    )

    log(
        f"Raw PBF: {RAW_PBF}"
    )

    log(
        f"Raw PBF size: "
        f"{raw_size / 1024 / 1024:.2f} MB"
    )

    # --------------------------------------------------------
    # CLEAN
    # --------------------------------------------------------

    if DATA_DIR.exists():
        shutil.rmtree(
            DATA_DIR
        )

    if ARCHIVE.exists():
        ARCHIVE.unlink()

    DATA_DIR.mkdir(
        parents=True
    )

    TMP_DIR.mkdir(
        parents=True
    )

    CHUNKS_DIR.mkdir(
        parents=True
    )

    # --------------------------------------------------------
    # GRID
    # --------------------------------------------------------

    columns, rows = (
        create_grid()
    )

    # --------------------------------------------------------
    # DIRECT FILTER
    #
    # IMPORTANT:
    # NO osmium extract here.
    # --------------------------------------------------------

    buildings_pbf = filter_pbf(
        "buildings",
        BUILDING_FILTERS
    )

    roads_pbf = filter_pbf(
        "roads",
        ROAD_FILTERS
    )

    waterways_pbf = filter_pbf(
        "waterways",
        WATERWAY_FILTERS
    )

    # --------------------------------------------------------
    # EXPORT
    # --------------------------------------------------------

    buildings_geo = export_pbf(
        buildings_pbf,
        "buildings"
    )

    roads_geo = export_pbf(
        roads_pbf,
        "roads"
    )

    waterways_geo = export_pbf(
        waterways_pbf,
        "waterways"
    )

    # --------------------------------------------------------
    # DIAGNOSTICS
    # --------------------------------------------------------

    building_diag = diagnostic(
        buildings_geo,
        "buildings"
    )

    road_diag = diagnostic(
        roads_geo,
        "roads"
    )

    waterway_diag = diagnostic(
        waterways_geo,
        "waterways"
    )

    # --------------------------------------------------------
    # IMPORTANT:
    # We expect at least some features in Pune.
    # --------------------------------------------------------

    if (
        building_diag["intersects"] == 0
        and road_diag["intersects"] == 0
        and waterway_diag["intersects"] == 0
    ):

        raise RuntimeError(
            "NONE of the sampled exported "
            "features intersect the Pune bbox. "
            "The diagnostic above contains the "
            "actual coordinates. Processing stopped."
        )

    # --------------------------------------------------------
    # PROCESS
    # --------------------------------------------------------

    writer = ChunkWriter(
        CHUNKS_DIR,
        MAX_OPEN_FILES
    )

    try:

        building_stats = process_file(
            buildings_geo,
            "buildings",
            writer
        )

        road_stats = process_file(
            roads_geo,
            "roads",
            writer
        )

        waterway_stats = process_file(
            waterways_geo,
            "waterways",
            writer
        )

    finally:

        writer.close()

    stats = {
        "buildings":
            building_stats,

        "roads":
            road_stats,

        "waterways":
            waterway_stats
    }

    # --------------------------------------------------------
    # FINALIZE
    # --------------------------------------------------------

    chunk_count, total_features = (
        finalize_chunks()
    )

    # --------------------------------------------------------
    # SUMMARY
    # --------------------------------------------------------

    write_summary(
        columns,
        rows,
        stats,
        chunk_count,
        total_features
    )

    # --------------------------------------------------------
    # HARD VALIDATION
    # --------------------------------------------------------

    if chunk_count == 0:

        raise RuntimeError(
            "ZERO ACTIVE CHUNKS. "
            "Refusing successful output."
        )

    if building_stats["accepted"] == 0:

        raise RuntimeError(
            "ZERO BUILDINGS ACCEPTED."
        )

    if road_stats["accepted"] == 0:

        raise RuntimeError(
            "ZERO ROADS ACCEPTED."
        )

    # --------------------------------------------------------
    # ARCHIVE
    # --------------------------------------------------------

    create_archive()

    # --------------------------------------------------------
    # FINAL
    # --------------------------------------------------------

    log()
    log("=" * 70)
    log("PROCESSING COMPLETE")
    log("=" * 70)

    log(
        f"Active chunks: {chunk_count:,}"
    )

    log(
        f"Chunk features: {total_features:,}"
    )

    log(
        f"Buildings: "
        f"{building_stats['accepted']:,} / "
        f"{building_stats['total']:,}"
    )

    log(
        f"Roads: "
        f"{road_stats['accepted']:,} / "
        f"{road_stats['total']:,}"
    )

    log(
        f"Waterways: "
        f"{waterway_stats['accepted']:,} / "
        f"{waterway_stats['total']:,}"
    )


if __name__ == "__main__":

    try:
        main()

    except KeyboardInterrupt:

        log(
            "Interrupted."
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
