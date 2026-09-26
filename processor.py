import gzip
import json
import math
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import requests
from pyproj import Transformer
from shapely.geometry import box, mapping, shape
from shapely.ops import transform
import geopandas as gpd


# ============================================================
# CONFIG
# ============================================================

RAW_DIR = Path("data/raw")
OUTPUT_DIR = Path("data/chunks")

OSM_FILE = RAW_DIR / "osm" / "central-zone.osm.pbf"

DEM_DIR = RAW_DIR / "dem"

BOUNDARY_FILE = Path("data/pune_boundary.geojson")

GRID_SIZE_METERS = 1000

PUNE_QUERY = (
    "Pune Municipal Corporation, Maharashtra, India"
)

NOMINATIM_URL = (
    "https://nominatim.openstreetmap.org/search"
)

USER_AGENT = (
    "Pune3DChunkProcessor/1.0 "
    "(open-data processing project)"
)

# OSM layers we want to export.
OSM_LAYERS = {
    "buildings": "buildings",
    "roads": "roads",
    "waterways": "waterways",
    "parks": "parks",
    "railways": "railways",
}


# ============================================================
# HTTP
# ============================================================

session = requests.Session()

session.headers.update(
    {
        "User-Agent": USER_AGENT
    }
)


# ============================================================
# HELPERS
# ============================================================

def log(message=""):
    print(message, flush=True)


def clean_name(value):
    value = str(value)

    result = ""

    for char in value:
        if char.isalnum() or char in "_-":
            result += char
        elif char in " .":
            result += "_"

    return result[:100]


def write_json(path, data):
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    path.write_text(
        json.dumps(
            data,
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


# ============================================================
# DOWNLOAD / RESTORE OSM
# ============================================================

def verify_osm():

    if not OSM_FILE.exists():
        raise FileNotFoundError(
            f"OSM file not found: {OSM_FILE}"
        )

    size = OSM_FILE.stat().st_size

    log(
        f"OSM size: "
        f"{size / 1024 / 1024:.2f} MiB"
    )

    if size < 100_000_000:
        raise RuntimeError(
            "OSM file is suspiciously small."
        )

    log("OSM validation passed.")


# ============================================================
# GET PUNE BOUNDARY
# ============================================================

def get_pune_boundary():

    if BOUNDARY_FILE.exists():

        log()
        log(
            f"Using existing boundary: "
            f"{BOUNDARY_FILE}"
        )

        return gpd.read_file(
            BOUNDARY_FILE
        )

    log()
    log("=" * 70)
    log("FETCHING PUNE BOUNDARY")
    log("=" * 70)

    params = {
        "q": PUNE_QUERY,
        "format": "json",
        "polygon_geojson": 1,
        "limit": 10,
    }

    response = session.get(
        NOMINATIM_URL,
        params=params,
        timeout=120,
    )

    response.raise_for_status()

    results = response.json()

    if not results:
        raise RuntimeError(
            "Could not find Pune boundary."
        )

    selected = None

    for item in results:

        display_name = item.get(
            "display_name",
            "",
        )

        geojson = item.get(
            "geojson"
        )

        if not geojson:
            continue

        if (
            "Pune" in display_name
            and (
                "Municipal"
                in display_name
                or "Corporation"
                in display_name
            )
        ):
            selected = item
            break

    if selected is None:

        for item in results:

            if item.get("geojson"):
                selected = item
                break

    if selected is None:
        raise RuntimeError(
            "Pune result did not contain geometry."
        )

    geometry = shape(
        selected["geojson"]
    )

    if geometry.is_empty:
        raise RuntimeError(
            "Pune boundary geometry is empty."
        )

    boundary = gpd.GeoDataFrame(
        {
            "name": [
                selected.get(
                    "display_name",
                    "Pune",
                )
            ]
        },
        geometry=[geometry],
        crs="EPSG:4326",
    )

    BOUNDARY_FILE.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    boundary.to_file(
        BOUNDARY_FILE,
        driver="GeoJSON",
    )

    log()
    log(
        "Boundary saved:"
    )
    log(str(BOUNDARY_FILE))

    log()
    log(
        "Boundary:"
    )
    log(
        selected.get(
            "display_name",
            "Pune",
        )
    )

    return boundary


# ============================================================
# CREATE 1 KM GRID
# ============================================================

def create_grid(boundary):

    log()
    log("=" * 70)
    log("CREATING 1 KM GRID")
    log("=" * 70)

    # Work in UTM zone 43N for Pune.
    metric_crs = "EPSG:32643"

    boundary_metric = boundary.to_crs(
        metric_crs
    )

    geometry = boundary_metric.geometry.unary_union

    minx, miny, maxx, maxy = (
        geometry.bounds
    )

    start_x = (
        math.floor(minx / GRID_SIZE_METERS)
        * GRID_SIZE_METERS
    )

    start_y = (
        math.floor(miny / GRID_SIZE_METERS)
        * GRID_SIZE_METERS
    )

    end_x = (
        math.ceil(maxx / GRID_SIZE_METERS)
        * GRID_SIZE_METERS
    )

    end_y = (
        math.ceil(maxy / GRID_SIZE_METERS)
        * GRID_SIZE_METERS
    )

    chunks = []

    row = 0

    y = start_y

    while y < end_y:

        col = 0

        x = start_x

        while x < end_x:

            tile = box(
                x,
                y,
                x + GRID_SIZE_METERS,
                y + GRID_SIZE_METERS,
            )

            intersection = tile.intersection(
                geometry
            )

            if not intersection.is_empty:

                chunk_id = (
                    f"PUNE_"
                    f"{row:04d}_"
                    f"{col:04d}"
                )

                chunks.append(
                    {
                        "id": chunk_id,
                        "row": row,
                        "column": col,
                        "geometry": tile,
                        "coverage": intersection,
                    }
                )

            x += GRID_SIZE_METERS
            col += 1

        y += GRID_SIZE_METERS
        row += 1

    log()
    log(
        f"Grid chunks: {len(chunks)}"
    )

    return chunks, metric_crs


# ============================================================
# SAVE GRID
# ============================================================

def save_grid(chunks, metric_crs):

    records = []

    for chunk in chunks:

        records.append(
            {
                "chunk_id": chunk["id"],
                "row": chunk["row"],
                "column": chunk["column"],
                "geometry": chunk["geometry"],
            }
        )

    grid = gpd.GeoDataFrame(
        records,
        geometry="geometry",
        crs=metric_crs,
    )

    grid_path = (
        OUTPUT_DIR / "grid.geojson"
    )

    grid_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    grid.to_crs(
        "EPSG:4326"
    ).to_file(
        grid_path,
        driver="GeoJSON",
    )

    log(
        f"Grid saved: {grid_path}"
    )


# ============================================================
# LOAD OSM
# ============================================================

def load_osm():

    log()
    log("=" * 70)
    log("READING OSM")
    log("=" * 70)

    try:
        from pyrosm import OSM

    except ImportError:
        raise RuntimeError(
            "pyrosm is not installed."
        )

    log(
        f"Opening: {OSM_FILE}"
    )

    osm = OSM(
        str(OSM_FILE)
    )

    return osm


# ============================================================
# OSM EXTRACTION
# ============================================================

def extract_osm_layers(
    osm,
    chunks,
    metric_crs,
):

    log()
    log("=" * 70)
    log("PROCESSING OSM CHUNKS")
    log("=" * 70)

    for index, chunk in enumerate(
        chunks,
        start=1,
    ):

        chunk_id = chunk["id"]

        log()
        log(
            f"[{index}/{len(chunks)}] "
            f"{chunk_id}"
        )

        chunk_dir = (
            OUTPUT_DIR
            / chunk_id
        )

        chunk_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        tile = chunk["geometry"]

        tile_wgs84 = gpd.GeoSeries(
            [tile],
            crs=metric_crs,
        ).to_crs(
            "EPSG:4326"
        ).iloc[0]

        minx, miny, maxx, maxy = (
            tile_wgs84.bounds
        )

        bbox = (
            minx,
            miny,
            maxx,
            maxy,
        )

        # ----------------------------------------------------
        # Buildings
        # ----------------------------------------------------

        try:

            buildings = osm.get_buildings(
                custom_filter={
                    "building": True
                },
                bounding_box=bbox,
            )

            if buildings is not None and not buildings.empty:

                buildings.to_crs(
                    "EPSG:4326"
                ).to_file(
                    chunk_dir
                    / "buildings.geojson",
                    driver="GeoJSON",
                )

                log(
                    f"Buildings: "
                    f"{len(buildings)}"
                )

        except Exception as exc:

            log(
                "Buildings extraction failed:"
            )
            log(repr(exc))

        # ----------------------------------------------------
        # Roads
        # ----------------------------------------------------

        try:

            roads = osm.get_network(
                network_type="all",
                bounding_box=bbox,
            )

            if roads is not None and not roads.empty:

                roads.to_crs(
                    "EPSG:4326"
                ).to_file(
                    chunk_dir
                    / "roads.geojson",
                    driver="GeoJSON",
                )

                log(
                    f"Roads: "
                    f"{len(roads)}"
                )

        except Exception as exc:

            log(
                "Road extraction failed:"
            )
            log(repr(exc))

        # ----------------------------------------------------
        # Waterways
        # ----------------------------------------------------

        try:

            waterways = osm.get_data_by_custom_criteria(
                custom_filter={
                    "waterway": True
                },
                bounding_box=bbox,
                filter_type="keep",
            )

            if (
                waterways is not None
                and not waterways.empty
            ):

                waterways.to_crs(
                    "EPSG:4326"
                ).to_file(
                    chunk_dir
                    / "waterways.geojson",
                    driver="GeoJSON",
                )

                log(
                    f"Waterways: "
                    f"{len(waterways)}"
                )

        except Exception as exc:

            log(
                "Waterway extraction failed:"
            )
            log(repr(exc))

        # ----------------------------------------------------
        # POIs
        # ----------------------------------------------------

        try:

            pois = osm.get_pois(
                bounding_box=bbox
            )

            if pois is not None and not pois.empty:

                pois.to_crs(
                    "EPSG:4326"
                ).to_file(
                    chunk_dir
                    / "pois.geojson",
                    driver="GeoJSON",
                )

                log(
                    f"POIs: "
                    f"{len(pois)}"
                )

        except Exception as exc:

            log(
                "POI extraction failed:"
            )
            log(repr(exc))


# ============================================================
# DEM
# ============================================================

def read_hgt(path):

    with gzip.open(
        path,
        "rb",
    ) as file:

        data = file.read()

    values = np.frombuffer(
        data,
        dtype=">i2",
    )

    total = values.size

    side = int(
        math.sqrt(total)
    )

    if side * side != total:
        raise RuntimeError(
            f"Invalid HGT file: {path}"
        )

    return values.reshape(
        (side, side)
    )


def dem_tile_bounds(tile):

    lat_sign = 1 if tile[0] == "N" else -1
    lon_sign = 1 if tile[4] == "E" else -1

    latitude = (
        int(tile[1:3])
        * lat_sign
    )

    longitude = (
        int(tile[4:7])
        * lon_sign
    )

    return (
        latitude,
        longitude,
    )


def process_dem_chunk(
    chunk,
    metric_crs,
):

    chunk_id = chunk["id"]

    chunk_dir = (
        OUTPUT_DIR
        / chunk_id
        / "terrain"
    )

    chunk_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    tile = chunk["geometry"]

    wgs84 = gpd.GeoSeries(
        [tile],
        crs=metric_crs,
    ).to_crs(
        "EPSG:4326"
    ).iloc[0]

    min_lon, min_lat, max_lon, max_lat = (
        wgs84.bounds
    )

    terrain_info = {
        "chunk_id": chunk_id,
        "bounds_wgs84": [
            min_lon,
            min_lat,
            max_lon,
            max_lat,
        ],
        "source": "AWS Terrain Tiles",
        "tiles": [],
    }

    # DEM files are 1° x 1°.
    lat_start = math.floor(min_lat)
    lat_end = math.floor(max_lat)

    lon_start = math.floor(min_lon)
    lon_end = math.floor(max_lon)

    for latitude in range(
        lat_start,
        lat_end + 1,
    ):

        for longitude in range(
            lon_start,
            lon_end + 1,
        ):

            ns = (
                "N"
                if latitude >= 0
                else "S"
            )

            ew = (
                "E"
                if longitude >= 0
                else "W"
            )

            tile_name = (
                f"{ns}"
                f"{abs(latitude):02d}"
                f"{ew}"
                f"{abs(longitude):03d}"
            )

            dem_file = (
                DEM_DIR
                / f"{tile_name}.hgt.gz"
            )

            if not dem_file.exists():

                continue

            try:

                elevation = read_hgt(
                    dem_file
                )

                # Store summary information first.
                terrain_info["tiles"].append(
                    {
                        "tile": tile_name,
                        "rows": int(
                            elevation.shape[0]
                        ),
                        "columns": int(
                            elevation.shape[1]
                        ),
                        "min_m": float(
                            elevation.min()
                        ),
                        "max_m": float(
                            elevation.max()
                        ),
                        "mean_m": float(
                            elevation.mean()
                        ),
                    }
                )

            except Exception as exc:

                log(
                    f"DEM error "
                    f"{tile_name}: "
                    f"{exc}"
                )

    write_json(
        chunk_dir
        / "terrain.json",
        terrain_info,
    )


# ============================================================
# CHUNK METADATA
# ============================================================

def create_chunk_metadata(
    chunk,
    metric_crs,
):

    chunk_id = chunk["id"]

    chunk_dir = (
        OUTPUT_DIR
        / chunk_id
    )

    geometry_wgs84 = gpd.GeoSeries(
        [chunk["geometry"]],
        crs=metric_crs,
    ).to_crs(
        "EPSG:4326"
    ).iloc[0]

    minx, miny, maxx, maxy = (
        geometry_wgs84.bounds
    )

    metadata = {
        "chunk_id": chunk_id,
        "grid": {
            "width_m": GRID_SIZE_METERS,
            "height_m": GRID_SIZE_METERS,
            "row": chunk["row"],
            "column": chunk["column"],
        },
        "crs": {
            "source": "EPSG:32643",
            "geographic": "EPSG:4326",
        },
        "bounds_wgs84": [
            minx,
            miny,
            maxx,
            maxy,
        ],
        "geometry": mapping(
            geometry_wgs84
        ),
        "layers": [
            "buildings",
            "roads",
            "waterways",
            "pois",
            "terrain",
        ],
    }

    write_json(
        chunk_dir
        / "metadata.json",
        metadata,
    )


# ============================================================
# PROCESS ONE CHUNK TERRAIN
# ============================================================

def process_terrain(
    chunks,
    metric_crs,
):

    log()
    log("=" * 70)
    log("PROCESSING DEM")
    log("=" * 70)

    for index, chunk in enumerate(
        chunks,
        start=1,
    ):

        log(
            f"Terrain "
            f"[{index}/{len(chunks)}] "
            f"{chunk['id']}"
        )

        process_dem_chunk(
            chunk,
            metric_crs,
        )


# ============================================================
# SUMMARY
# ============================================================

def create_summary(
    chunks,
    boundary,
):

    summary = {
        "project": "Pune 3D City",
        "processor": "Pune 1km Chunk Processor",
        "grid_size_m": GRID_SIZE_METERS,
        "chunk_count": len(chunks),
        "coordinate_system": {
            "metric": "EPSG:32643",
            "geographic": "EPSG:4326",
        },
        "boundary": PUNE_QUERY,
        "generated_at": time.strftime(
            "%Y-%m-%dT%H:%M:%SZ",
            time.gmtime(),
        ),
    }

    write_json(
        OUTPUT_DIR
        / "summary.json",
        summary,
    )


# ============================================================
# MAIN
# ============================================================

def main():

    start = time.time()

    log()
    log("=" * 70)
    log("PUNE 1 KM CHUNK PROCESSOR")
    log("=" * 70)

    # --------------------------------------------------------
    # Validate
    # --------------------------------------------------------

    verify_osm()

    # --------------------------------------------------------
    # Boundary
    # --------------------------------------------------------

    boundary = get_pune_boundary()

    # --------------------------------------------------------
    # Grid
    # --------------------------------------------------------

    chunks, metric_crs = create_grid(
        boundary
    )

    save_grid(
        chunks,
        metric_crs,
    )

    # --------------------------------------------------------
    # OSM
    # --------------------------------------------------------

    osm = load_osm()

    extract_osm_layers(
        osm,
        chunks,
        metric_crs,
    )

    # --------------------------------------------------------
    # DEM
    # --------------------------------------------------------

    process_terrain(
        chunks,
        metric_crs,
    )

    # --------------------------------------------------------
    # Metadata
    # --------------------------------------------------------

    log()
    log(
        "Creating chunk metadata..."
    )

    for chunk in chunks:

        create_chunk_metadata(
            chunk,
            metric_crs,
        )

    # --------------------------------------------------------
    # Summary
    # --------------------------------------------------------

    create_summary(
        chunks,
        boundary,
    )

    elapsed = (
        time.time() - start
    )

    log()
    log("=" * 70)
    log("PROCESSING COMPLETE")
    log("=" * 70)

    log()
    log(
        f"Chunks: {len(chunks)}"
    )

    log(
        f"Output: {OUTPUT_DIR}"
    )

    log(
        f"Time: {elapsed / 60:.2f} minutes"
    )


if __name__ == "__main__":
    main()
