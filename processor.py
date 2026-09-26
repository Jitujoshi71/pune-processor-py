#!/usr/bin/env python3

import json
import math
import os
import shutil
import sqlite3
import tarfile
import time
from pathlib import Path

import osmium
from shapely.geometry import (
    LineString,
    Polygon,
    box,
    mapping,
)


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

CHUNK_SIZE_M = float(
    os.getenv(
        "CHUNK_SIZE_M",
        "1000",
    )
)

TEMP_DIR = Path(
    os.getenv(
        "TEMP_DIR",
        "data/.processor_tmp",
    )
)

DATABASE_FILE = (
    TEMP_DIR / "features.sqlite"
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
# DATABASE
# ============================================================

def create_database():

    TEMP_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    if DATABASE_FILE.exists():
        DATABASE_FILE.unlink()

    connection = sqlite3.connect(
        DATABASE_FILE
    )

    connection.execute(
        """
        PRAGMA journal_mode=WAL
        """
    )

    connection.execute(
        """
        PRAGMA synchronous=NORMAL
        """
    )

    connection.execute(
        """
        PRAGMA temp_store=FILE
        """
    )

    connection.execute(
        """
        CREATE TABLE features (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chunk_x INTEGER NOT NULL,
            chunk_y INTEGER NOT NULL,
            feature_type TEXT NOT NULL,
            osm_id INTEGER NOT NULL,
            geometry TEXT NOT NULL,
            properties TEXT NOT NULL
        )
        """
    )

    connection.execute(
        """
        CREATE INDEX idx_features_chunk
        ON features(chunk_x, chunk_y)
        """
    )

    connection.execute(
        """
        CREATE INDEX idx_features_type
        ON features(feature_type)
        """
    )

    connection.commit()

    return connection


# ============================================================
# OSM STREAM HANDLER
# ============================================================

class OSMStreamingHandler(
    osmium.SimpleHandler
):

    def __init__(
        self,
        connection,
        min_lon,
        min_lat,
        max_lon,
        max_lat,
        x0,
        y0,
        nx,
        ny,
        chunk_size,
        reference_lat,
    ):

        super().__init__()

        self.connection = connection

        self.city_bbox = box(
            min_lon,
            min_lat,
            max_lon,
            max_lat,
        )

        self.min_lon = min_lon
        self.min_lat = min_lat
        self.max_lon = max_lon
        self.max_lat = max_lat

        self.x0 = x0
        self.y0 = y0

        self.nx = nx
        self.ny = ny

        self.chunk_size = chunk_size
        self.reference_lat = reference_lat

        self.way_count = 0
        self.building_count = 0
        self.road_count = 0
        self.waterway_count = 0

        self.assigned_count = 0

        self.last_commit = time.time()

        self.pending = []

        self.commit_every = 2000

    # --------------------------------------------------------
    # TAGS
    # --------------------------------------------------------

    @staticmethod
    def tags_to_dict(obj):

        return {
            str(key): str(value)
            for key, value in obj.tags
        }

    # --------------------------------------------------------
    # CHUNK RANGE
    # --------------------------------------------------------

    def longitude_to_chunk(
        self,
        lon,
    ):

        value = (
            lon_to_m(
                lon,
                self.reference_lat,
            )
            - self.x0
        ) / self.chunk_size

        return math.floor(value)

    def latitude_to_chunk(
        self,
        lat,
    ):

        value = (
            lat_to_m(lat)
            - self.y0
        ) / self.chunk_size

        return math.floor(value)

    # --------------------------------------------------------
    # ADD TO DATABASE
    # --------------------------------------------------------

    def add_record(
        self,
        chunk_x,
        chunk_y,
        feature_type,
        osm_id,
        geometry,
        properties,
    ):

        if (
            chunk_x < 0
            or chunk_x >= self.nx
            or chunk_y < 0
            or chunk_y >= self.ny
        ):
            return

        record = (
            chunk_x,
            chunk_y,
            feature_type,
            int(osm_id),
            json.dumps(
                mapping(geometry),
                separators=(",", ":"),
            ),
            json.dumps(
                properties,
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        )

        self.pending.append(record)

        if len(self.pending) >= self.commit_every:
            self.flush()

    # --------------------------------------------------------
    # FLUSH
    # --------------------------------------------------------

    def flush(self):

        if not self.pending:
            return

        self.connection.executemany(
            """
            INSERT INTO features
            (
                chunk_x,
                chunk_y,
                feature_type,
                osm_id,
                geometry,
                properties
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            self.pending,
        )

        self.connection.commit()

        self.pending.clear()

    # --------------------------------------------------------
    # WAY
    # --------------------------------------------------------

    def way(self, way):

        self.way_count += 1

        # ----------------------------------------------------
        # Progress
        # ----------------------------------------------------

        if (
            self.way_count % PROGRESS_EVERY
            == 0
        ):

            self.flush()

            elapsed = (
                time.time()
                - self.last_commit
            )

            print(
                f"[OSM] ways="
                f"{self.way_count:,} "
                f"buildings="
                f"{self.building_count:,} "
                f"roads="
                f"{self.road_count:,} "
                f"waterways="
                f"{self.waterway_count:,}",
                flush=True,
            )

            self.last_commit = time.time()

        # ----------------------------------------------------
        # Tags
        # ----------------------------------------------------

        tags = self.tags_to_dict(
            way
        )

        feature_type = None

        if tags.get("building"):
            feature_type = "building"

        elif tags.get("highway"):
            feature_type = "road"

        elif tags.get("waterway"):
            feature_type = "waterway"

        else:
            return

        # ----------------------------------------------------
        # Coordinates
        # ----------------------------------------------------

        coordinates = []

        try:

            for node in way.nodes:

                if not node.location.valid():
                    continue

                coordinates.append(
                    (
                        float(node.lon),
                        float(node.lat),
                    )
                )

        except Exception:
            return

        if len(coordinates) < 2:
            return

        # ----------------------------------------------------
        # Geometry
        # ----------------------------------------------------

        try:

            if feature_type == "building":

                if (
                    len(coordinates) < 4
                    or coordinates[0]
                    != coordinates[-1]
                ):
                    return

                geometry = Polygon(
                    coordinates
                )

                if not geometry.is_valid:
                    geometry = geometry.buffer(
                        0
                    )

                if geometry.is_empty:
                    return

                if geometry.area <= 0:
                    return

            else:

                geometry = LineString(
                    coordinates
                )

                if geometry.is_empty:
                    return

                if geometry.length <= 0:
                    return

        except Exception:
            return

        # ----------------------------------------------------
        # BBOX filter
        # ----------------------------------------------------

        try:

            if not geometry.intersects(
                self.city_bbox
            ):
                return

            geometry = geometry.intersection(
                self.city_bbox
            )

        except Exception:
            return

        if geometry.is_empty:
            return

        # ----------------------------------------------------
        # Geometry bounds
        # ----------------------------------------------------

        try:

            (
                geom_min_lon,
                geom_min_lat,
                geom_max_lon,
                geom_max_lat,
            ) = geometry.bounds

        except Exception:
            return

        # ----------------------------------------------------
        # Candidate chunks
        # ----------------------------------------------------

        ix0 = self.longitude_to_chunk(
            geom_min_lon
        )

        ix1 = self.longitude_to_chunk(
            geom_max_lon
        )

        iy0 = self.latitude_to_chunk(
            geom_min_lat
        )

        iy1 = self.latitude_to_chunk(
            geom_max_lat
        )

        ix0 = max(
            0,
            min(
                self.nx - 1,
                ix0,
            ),
        )

        ix1 = max(
            0,
            min(
                self.nx - 1,
                ix1,
            ),
        )

        iy0 = max(
            0,
            min(
                self.ny - 1,
                iy0,
            ),
        )

        iy1 = max(
            0,
            min(
                self.ny - 1,
                iy1,
            ),
        )

        # ----------------------------------------------------
        # Store in candidate chunks
        # ----------------------------------------------------

        for chunk_y in range(
            iy0,
            iy1 + 1,
        ):

            for chunk_x in range(
                ix0,
                ix1 + 1,
            ):

                chunk_min_x = (
                    self.x0
                    + chunk_x
                    * self.chunk_size
                )

                chunk_max_x = (
                    self.x0
                    + (chunk_x + 1)
                    * self.chunk_size
                )

                chunk_min_y = (
                    self.y0
                    + chunk_y
                    * self.chunk_size
                )

                chunk_max_y = (
                    self.y0
                    + (chunk_y + 1)
                    * self.chunk_size
                )

                chunk_min_lon = m_to_lon(
                    chunk_min_x,
                    self.reference_lat,
                )

                chunk_max_lon = m_to_lon(
                    chunk_max_x,
                    self.reference_lat,
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

                    clipped = geometry.intersection(
                        chunk_bbox
                    )

                except Exception:
                    continue

                if clipped.is_empty:
                    continue

                # ------------------------------------------------
                # Building repair
                # ------------------------------------------------

                if feature_type == "building":

                    try:

                        if not clipped.is_valid:
                            clipped = clipped.buffer(
                                0
                            )

                    except Exception:
                        continue

                    if clipped.is_empty:
                        continue

                # ------------------------------------------------
                # Properties
                # ------------------------------------------------

                properties = {
                    "osm_id": int(way.id),
                    "feature_type": feature_type,
                }

                properties.update(
                    tags
                )

                self.add_record(
                    chunk_x,
                    chunk_y,
                    feature_type,
                    way.id,
                    clipped,
                    properties,
                )

                self.assigned_count += 1

        # ----------------------------------------------------
        # Counters
        # ----------------------------------------------------

        if feature_type == "building":
            self.building_count += 1

        elif feature_type == "road":
            self.road_count += 1

        elif feature_type == "waterway":
            self.waterway_count += 1


# ============================================================
# GEOJSON
# ============================================================

def write_geojson_file(
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
# EMPTY GEOJSON
# ============================================================

def write_empty_geojson(
    path,
):

    write_geojson_file(
        path,
        [],
    )


# ============================================================
# MAIN
# ============================================================

def main():

    start_time = time.time()

    print()
    print("=" * 70)
    print("PUNE STREAMING OSM PROCESSOR")
    print("=" * 70)

    # --------------------------------------------------------
    # Check OSM
    # --------------------------------------------------------

    if not OSM_FILE.exists():

        raise FileNotFoundError(
            f"OSM file not found: "
            f"{OSM_FILE}"
        )

    osm_size_mb = (
        OSM_FILE.stat().st_size
        / 1024
        / 1024
    )

    print(
        f"OSM: {OSM_FILE}"
    )

    print(
        f"OSM size: "
        f"{osm_size_mb:.2f} MB"
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
        f"  {min_lon}, "
        f"{min_lat}, "
        f"{max_lon}, "
        f"{max_lat}"
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

    total_chunks = (
        nx * ny
    )

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
    # Cleanup temporary data
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
    # Cleanup output
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
    # SQLite
    # --------------------------------------------------------

    print()
    print("=" * 70)
    print("CREATING DISK-BACKED DATABASE")
    print("=" * 70)

    connection = create_database()

    # --------------------------------------------------------
    # OSM streaming
    # --------------------------------------------------------

    handler = OSMStreamingHandler(
        connection=connection,
        min_lon=min_lon,
        min_lat=min_lat,
        max_lon=max_lon,
        max_lat=max_lat,
        x0=x0,
        y0=y0,
        nx=nx,
        ny=ny,
        chunk_size=CHUNK_SIZE_M,
        reference_lat=reference_lat,
    )

    print()
    print("=" * 70)
    print("STARTING OSM STREAM")
    print("=" * 70)

    print(
        "The PBF is processed incrementally."
    )

    print(
        "RAM will NOT contain the complete OSM dataset."
    )

    print()

    try:

        handler.apply_file(
            str(OSM_FILE),
            locations=True,
        )

        handler.flush()

    except Exception:

        connection.close()

        raise

    # --------------------------------------------------------
    # Database statistics
    # --------------------------------------------------------

    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT COUNT(*)
        FROM features
        """
    )

    database_features = (
        cursor.fetchone()[0]
    )

    print()
    print("=" * 70)
    print("OSM STREAM COMPLETE")
    print("=" * 70)

    print(
        f"Ways processed     : "
        f"{handler.way_count:,}"
    )

    print(
        f"Buildings          : "
        f"{handler.building_count:,}"
    )

    print(
        f"Roads              : "
        f"{handler.road_count:,}"
    )

    print(
        f"Waterways          : "
        f"{handler.waterway_count:,}"
    )

    print(
        f"Chunk assignments   : "
        f"{handler.assigned_count:,}"
    )

    print(
        f"Database features  : "
        f"{database_features:,}"
    )

    # --------------------------------------------------------
    # Generate grid
    # --------------------------------------------------------

    print()
    print("=" * 70)
    print("GENERATING CHUNKS")
    print("=" * 70)

    grid_features = []

    chunk_summaries = []

    nonempty_chunks = 0

    total_buildings = 0
    total_roads = 0
    total_waterways = 0

    # --------------------------------------------------------
    # Query each chunk
    # --------------------------------------------------------

    for chunk_y in range(ny):

        for chunk_x in range(nx):

            chunk_number = (
                chunk_y * nx
                + chunk_x
                + 1
            )

            chunk_id = (
                f"PUNE_"
                f"{chunk_x:04d}_"
                f"{chunk_y:04d}"
            )

            # ------------------------------------------------
            # Metric bounds
            # ------------------------------------------------

            chunk_min_x = (
                x0
                + chunk_x
                * CHUNK_SIZE_M
            )

            chunk_max_x = min(
                x0
                + (chunk_x + 1)
                * CHUNK_SIZE_M,
                x1,
            )

            chunk_min_y = (
                y0
                + chunk_y
                * CHUNK_SIZE_M
            )

            chunk_max_y = min(
                y0
                + (chunk_y + 1)
                * CHUNK_SIZE_M,
                y1,
            )

            # ------------------------------------------------
            # WGS84
            # ------------------------------------------------

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

            # ------------------------------------------------
            # Directory
            # ------------------------------------------------

            chunk_dir = (
                OUTPUT_DIR
                / chunk_id
            )

            chunk_dir.mkdir(
                parents=True,
                exist_ok=True,
            )

            # ------------------------------------------------
            # Feature containers
            # ------------------------------------------------

            buildings = []
            roads = []
            waterways = []

            # ------------------------------------------------
            # Query database
            # ------------------------------------------------

            rows = connection.execute(
                """
                SELECT
                    feature_type,
                    osm_id,
                    geometry,
                    properties
                FROM features
                WHERE
                    chunk_x = ?
                    AND chunk_y = ?
                ORDER BY id
                """,
                (
                    chunk_x,
                    chunk_y,
                ),
            )

            for row in rows:

                (
                    feature_type,
                    osm_id,
                    geometry_json,
                    properties_json,
                ) = row

                try:

                    geometry = json.loads(
                        geometry_json
                    )

                    properties = json.loads(
                        properties_json
                    )

                except Exception:
                    continue

                feature = {
                    "type": "Feature",
                    "properties": properties,
                    "geometry": geometry,
                }

                if feature_type == "building":
                    buildings.append(
                        feature
                    )

                elif feature_type == "road":
                    roads.append(
                        feature
                    )

                elif feature_type == "waterway":
                    waterways.append(
                        feature
                    )

            # ------------------------------------------------
            # Write files
            # ------------------------------------------------

            write_geojson_file(
                chunk_dir
                / "buildings.geojson",
                buildings,
            )

            write_geojson_file(
                chunk_dir
                / "roads.geojson",
                roads,
            )

            write_geojson_file(
                chunk_dir
                / "waterways.geojson",
                waterways,
            )

            # ------------------------------------------------
            # Counts
            # ------------------------------------------------

            building_count = len(
                buildings
            )

            road_count = len(
                roads
            )

            waterway_count = len(
                waterways
            )

            total_buildings += (
                building_count
            )

            total_roads += (
                road_count
            )

            total_waterways += (
                waterway_count
            )

            if (
                building_count
                or road_count
                or waterway_count
            ):
                nonempty_chunks += 1

            # ------------------------------------------------
            # Metadata
            # ------------------------------------------------

            metadata = {
                "version": 2,
                "chunk_id": chunk_id,
                "grid_x": chunk_x,
                "grid_y": chunk_y,
                "chunk_size_m": CHUNK_SIZE_M,
                "crs": "EPSG:4326",
                "bounds_wgs84": [
                    chunk_min_lon,
                    chunk_min_lat,
                    chunk_max_lon,
                    chunk_max_lat,
                ],
                "source": {
                    "file": OSM_FILE.name,
                    "type": "OpenStreetMap PBF",
                },
                "features": {
                    "buildings": building_count,
                    "roads": road_count,
                    "waterways": waterway_count,
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
            # Grid feature
            # ------------------------------------------------

            chunk_polygon = box(
                chunk_min_lon,
                chunk_min_lat,
                chunk_max_lon,
                chunk_max_lat,
            )

            grid_features.append(
                {
                    "type": "Feature",
                    "properties": {
                        "chunk_id": chunk_id,
                        "grid_x": chunk_x,
                        "grid_y": chunk_y,
                        "buildings": building_count,
                        "roads": road_count,
                        "waterways": waterway_count,
                    },
                    "geometry": mapping(
                        chunk_polygon
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
                chunk_number % 25 == 0
                or chunk_number
                == total_chunks
            ):

                percent = (
                    chunk_number
                    / total_chunks
                    * 100
                )

                print(
                    f"[CHUNKS] "
                    f"{chunk_number:,}/"
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
    # Grid GeoJSON
    # --------------------------------------------------------

    print()
    print(
        "Writing grid.geojson..."
    )

    write_geojson_file(
        OUTPUT_DIR
        / "grid.geojson",
        grid_features,
    )

    # --------------------------------------------------------
    # Summary
    # --------------------------------------------------------

    summary = {
        "version": 2,
        "processor": "pune-processor-py-streaming",
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

    print(
        "Writing summary.json..."
    )

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
    # Close DB
    # --------------------------------------------------------

    connection.close()

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
        "Creating final archive..."
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
    # Database cleanup
    # --------------------------------------------------------

    if TEMP_DIR.exists():

        shutil.rmtree(
            TEMP_DIR
        )

    # --------------------------------------------------------
    # Final stats
    # --------------------------------------------------------

    elapsed = (
        time.time()
        - start_time
    )

    archive_size_mb = (
        archive_path.stat().st_size
        / 1024
        / 1024
    )

    print()
    print("=" * 70)
    print("PROCESSING COMPLETE")
    print("=" * 70)

    print(
        f"Total chunks      : "
        f"{total_chunks:,}"
    )

    print(
        f"Non-empty chunks  : "
        f"{nonempty_chunks:,}"
    )

    print(
        f"Buildings         : "
        f"{total_buildings:,}"
    )

    print(
        f"Roads             : "
        f"{total_roads:,}"
    )

    print(
        f"Waterways         : "
        f"{total_waterways:,}"
    )

    print(
        f"Archive size      : "
        f"{archive_size_mb:.2f} MB"
    )

    print(
        f"Time              : "
        f"{elapsed / 60:.2f} minutes"
    )

    print(
        f"Output            : "
        f"{OUTPUT_DIR}"
    )

    print(
        f"Archive           : "
        f"{archive_path}"
    )

    print("=" * 70)
    print()


if __name__ == "__main__":
    main()
