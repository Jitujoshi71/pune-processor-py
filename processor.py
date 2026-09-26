#!/usr/bin/env python3

import json
import math
import os
import shutil
import tarfile
from collections import defaultdict
from pathlib import Path

import osmium
from shapely.geometry import LineString, Polygon, box, mapping


# ============================================================
# CONFIGURATION
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

# Pune processing area.
#
# Override from GitHub Actions with:
#
# BBOX="min_lon,min_lat,max_lon,max_lat"
#
DEFAULT_BBOX = (
    73.70,  # min longitude
    18.40,  # min latitude
    74.05,  # max longitude
    18.70,  # max latitude
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
            "BBOX must contain exactly 4 values: "
            "min_lon,min_lat,max_lon,max_lat"
        )

    try:
        min_lon = float(parts[0])
        min_lat = float(parts[1])
        max_lon = float(parts[2])
        max_lat = float(parts[3])
    except ValueError as exc:
        raise ValueError(
            "BBOX contains invalid numbers"
        ) from exc

    if min_lon >= max_lon:
        raise ValueError(
            "BBOX min_lon must be smaller than max_lon"
        )

    if min_lat >= max_lat:
        raise ValueError(
            "BBOX min_lat must be smaller than max_lat"
        )

    return (
        min_lon,
        min_lat,
        max_lon,
        max_lat,
    )


# ============================================================
# APPROXIMATE LOCAL METRIC PROJECTION
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

    denominator = (
        111320.0
        * math.cos(
            math.radians(reference_lat)
        )
    )

    return x / denominator


def m_to_lat(y):

    return y / 110540.0


# ============================================================
# OSM COLLECTOR
# ============================================================

class OSMCollector(osmium.SimpleHandler):

    def __init__(self):

        # IMPORTANT:
        #
        # osmium.SimpleHandler does NOT accept
        # locations=True in its constructor.
        #
        # Locations are enabled when calling:
        #
        # apply_file(..., locations=True)
        #
        super().__init__()

        self.features = []

        self.building_count = 0
        self.road_count = 0
        self.waterway_count = 0

    @staticmethod
    def get_tags(obj):

        return {
            str(key): str(value)
            for key, value in obj.tags
        }

    # --------------------------------------------------------
    # WAY
    # --------------------------------------------------------

    def way(self, way):

        tags = self.get_tags(way)

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

        # ====================================================
        # BUILDINGS
        # ====================================================

        building = tags.get("building")

        if building:

            if (
                len(coordinates) >= 4
                and coordinates[0]
                == coordinates[-1]
            ):

                try:

                    geometry = Polygon(
                        coordinates
                    )

                    if not geometry.is_valid:
                        geometry = geometry.buffer(
                            0
                        )

                    if (
                        not geometry.is_empty
                        and geometry.area > 0
                    ):

                        self.features.append(
                            (
                                "building",
                                way.id,
                                geometry,
                                tags,
                            )
                        )

                        self.building_count += 1

                except Exception:
                    pass

            return

        # ====================================================
        # ROADS
        # ====================================================

        highway = tags.get("highway")

        if highway:

            try:

                geometry = LineString(
                    coordinates
                )

                if (
                    not geometry.is_empty
                    and geometry.length > 0
                ):

                    self.features.append(
                        (
                            "road",
                            way.id,
                            geometry,
                            tags,
                        )
                    )

                    self.road_count += 1

            except Exception:
                pass

        # ====================================================
        # WATERWAYS
        # ====================================================

        waterway = tags.get("waterway")

        if waterway:

            try:

                geometry = LineString(
                    coordinates
                )

                if (
                    not geometry.is_empty
                    and geometry.length > 0
                ):

                    self.features.append(
                        (
                            "waterway",
                            way.id,
                            geometry,
                            tags,
                        )
                    )

                    self.waterway_count += 1

            except Exception:
                pass


# ============================================================
# GEOJSON
# ============================================================

def make_feature(
    feature_type,
    osm_id,
    geometry,
    tags,
):

    properties = {
        "osm_id": int(osm_id),
        "feature_type": feature_type,
    }

    properties.update(tags)

    return {
        "type": "Feature",
        "properties": properties,
        "geometry": mapping(
            geometry
        ),
    }


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
# CHUNK ID
# ============================================================

def make_chunk_id(ix, iy):

    return (
        f"PUNE_{ix:04d}_{iy:04d}"
    )


# ============================================================
# MAIN
# ============================================================

def main():

    print()
    print("=" * 70)
    print("PUNE 1KM OSM PROCESSOR")
    print("=" * 70)

    # --------------------------------------------------------
    # Check OSM
    # --------------------------------------------------------

    if not OSM_FILE.exists():

        raise FileNotFoundError(
            f"OSM file not found: {OSM_FILE}"
        )

    osm_size_mb = (
        OSM_FILE.stat().st_size
        / 1024
        / 1024
    )

    print(
        f"OSM file: {OSM_FILE}"
    )

    print(
        f"OSM size: {osm_size_mb:.2f} MB"
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
    print("BBOX")
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
    # Reference latitude
    # --------------------------------------------------------

    reference_lat = (
        min_lat + max_lat
    ) / 2.0

    # --------------------------------------------------------
    # Convert BBOX to approximate meters
    # --------------------------------------------------------

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
    print("GRID")
    print(
        f"  width: {width_m:.2f} m"
    )
    print(
        f"  height: {height_m:.2f} m"
    )
    print(
        f"  chunk size: {CHUNK_SIZE_M:.2f} m"
    )
    print(
        f"  grid: {nx} x {ny}"
    )
    print(
        f"  total chunks: {total_chunks}"
    )

    # --------------------------------------------------------
    # Output cleanup
    # --------------------------------------------------------

    if OUTPUT_DIR.exists():

        print()
        print(
            f"Removing previous output: "
            f"{OUTPUT_DIR}"
        )

        shutil.rmtree(
            OUTPUT_DIR
        )

    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    # --------------------------------------------------------
    # Read OSM
    # --------------------------------------------------------

    print()
    print("=" * 70)
    print("READING OSM PBF")
    print("=" * 70)

    collector = OSMCollector()

    print(
        "Parsing ways..."
    )

    # IMPORTANT:
    #
    # locations=True belongs here,
    # NOT inside SimpleHandler.__init__().
    #

    collector.apply_file(
        str(OSM_FILE),
        locations=True,
    )

    print()
    print(
        "OSM parsing complete."
    )

    print(
        f"Buildings: "
        f"{collector.building_count:,}"
    )

    print(
        f"Roads: "
        f"{collector.road_count:,}"
    )

    print(
        f"Waterways: "
        f"{collector.waterway_count:,}"
    )

    print(
        f"Total collected features: "
        f"{len(collector.features):,}"
    )

    # --------------------------------------------------------
    # City bounding box
    # --------------------------------------------------------

    city_bbox = box(
        min_lon,
        min_lat,
        max_lon,
        max_lat,
    )

    # --------------------------------------------------------
    # Group features by chunk
    # --------------------------------------------------------

    grouped = defaultdict(list)

    print()
    print("=" * 70)
    print("ASSIGNING FEATURES TO CHUNKS")
    print("=" * 70)

    assigned_features = 0

    for index, item in enumerate(
        collector.features,
        start=1,
    ):

        (
            feature_type,
            osm_id,
            geometry,
            tags,
        ) = item

        # ----------------------------------------------------
        # Skip geometry outside city bbox
        # ----------------------------------------------------

        try:

            if not geometry.intersects(
                city_bbox
            ):
                continue

        except Exception:
            continue

        try:

            (
                geom_min_lon,
                geom_min_lat,
                geom_max_lon,
                geom_max_lat,
            ) = geometry.bounds

        except Exception:
            continue

        # ----------------------------------------------------
        # Calculate candidate chunk range
        # ----------------------------------------------------

        raw_ix0 = math.floor(
            (
                lon_to_m(
                    geom_min_lon,
                    reference_lat,
                )
                - x0
            )
            / CHUNK_SIZE_M
        )

        raw_ix1 = math.floor(
            (
                lon_to_m(
                    geom_max_lon,
                    reference_lat,
                )
                - x0
            )
            / CHUNK_SIZE_M
        )

        raw_iy0 = math.floor(
            (
                lat_to_m(
                    geom_min_lat
                )
                - y0
            )
            / CHUNK_SIZE_M
        )

        raw_iy1 = math.floor(
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
                raw_ix0,
            ),
        )

        ix1 = max(
            0,
            min(
                nx - 1,
                raw_ix1,
            ),
        )

        iy0 = max(
            0,
            min(
                ny - 1,
                raw_iy0,
            ),
        )

        iy1 = max(
            0,
            min(
                ny - 1,
                raw_iy1,
            ),
        )

        if (
            ix1 < ix0
            or iy1 < iy0
        ):
            continue

        # ----------------------------------------------------
        # Assign to intersecting chunks
        # ----------------------------------------------------

        for iy in range(
            iy0,
            iy1 + 1,
        ):

            for ix in range(
                ix0,
                ix1 + 1,
            ):

                grouped[
                    (ix, iy)
                ].append(
                    item
                )

                assigned_features += 1

        # ----------------------------------------------------
        # Progress
        # ----------------------------------------------------

        if (
            index % 50000 == 0
            or index == len(
                collector.features
            )
        ):

            print(
                f"Processed "
                f"{index:,}/"
                f"{len(collector.features):,}"
            )

    print()
    print(
        f"Feature assignments: "
        f"{assigned_features:,}"
    )

    # --------------------------------------------------------
    # Generate chunks
    # --------------------------------------------------------

    print()
    print("=" * 70)
    print("GENERATING 1KM CHUNKS")
    print("=" * 70)

    grid_features = []

    chunk_summaries = []

    nonempty_chunks = 0

    total_buildings = 0
    total_roads = 0
    total_waterways = 0

    # --------------------------------------------------------
    # Iterate grid
    # --------------------------------------------------------

    for iy in range(ny):

        for ix in range(nx):

            chunk_id = make_chunk_id(
                ix,
                iy,
            )

            # ------------------------------------------------
            # Chunk metric bounds
            # ------------------------------------------------

            chunk_min_x = (
                x0
                + ix * CHUNK_SIZE_M
            )

            chunk_max_x = min(
                x0
                + (ix + 1)
                * CHUNK_SIZE_M,
                x1,
            )

            chunk_min_y = (
                y0
                + iy * CHUNK_SIZE_M
            )

            chunk_max_y = min(
                y0
                + (iy + 1)
                * CHUNK_SIZE_M,
                y1,
            )

            # ------------------------------------------------
            # Convert back to WGS84
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

            chunk_bbox = box(
                chunk_min_lon,
                chunk_min_lat,
                chunk_max_lon,
                chunk_max_lat,
            )

            # ------------------------------------------------
            # Features
            # ------------------------------------------------

            buildings = []
            roads = []
            waterways = []

            seen = set()

            for item in grouped.get(
                (ix, iy),
                [],
            ):

                (
                    feature_type,
                    osm_id,
                    geometry,
                    tags,
                ) = item

                key = (
                    feature_type,
                    int(osm_id),
                )

                if key in seen:
                    continue

                seen.add(key)

                # --------------------------------------------
                # Clip geometry to chunk
                # --------------------------------------------

                try:

                    clipped = geometry.intersection(
                        chunk_bbox
                    )

                except Exception:
                    continue

                if clipped.is_empty:
                    continue

                # --------------------------------------------
                # Repair invalid polygon
                # --------------------------------------------

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

                    buildings.append(
                        make_feature(
                            feature_type,
                            osm_id,
                            clipped,
                            tags,
                        )
                    )

                elif feature_type == "road":

                    roads.append(
                        make_feature(
                            feature_type,
                            osm_id,
                            clipped,
                            tags,
                        )
                    )

                elif feature_type == "waterway":

                    waterways.append(
                        make_feature(
                            feature_type,
                            osm_id,
                            clipped,
                            tags,
                        )
                    )

            # ------------------------------------------------
            # Create directory
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
            # Metadata
            # ------------------------------------------------

            metadata = {
                "version": 2,
                "chunk_id": chunk_id,
                "grid_x": ix,
                "grid_y": iy,
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

            metadata_file = (
                chunk_dir
                / "metadata.json"
            )

            metadata_file.write_text(
                json.dumps(
                    metadata,
                    indent=2,
                ),
                encoding="utf-8",
            )

            # ------------------------------------------------
            # Buildings
            # ------------------------------------------------

            write_geojson(
                chunk_dir
                / "buildings.geojson",
                buildings,
            )

            # ------------------------------------------------
            # Roads
            # ------------------------------------------------

            write_geojson(
                chunk_dir
                / "roads.geojson",
                roads,
            )

            # ------------------------------------------------
            # Waterways
            # ------------------------------------------------

            write_geojson(
                chunk_dir
                / "waterways.geojson",
                waterways,
            )

            # ------------------------------------------------
            # Statistics
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
            # Grid feature
            # ------------------------------------------------

            grid_features.append(
                {
                    "type": "Feature",
                    "properties": {
                        "chunk_id": chunk_id,
                        "grid_x": ix,
                        "grid_y": iy,
                        "buildings": building_count,
                        "roads": road_count,
                        "waterways": waterway_count,
                    },
                    "geometry": mapping(
                        chunk_bbox
                    ),
                }
            )

            chunk_summaries.append(
                metadata
            )

            # ------------------------------------------------
            # Progress
            # ------------------------------------------------

            completed = (
                iy * nx
                + ix
                + 1
            )

            if (
                completed % 25 == 0
                or completed == total_chunks
            ):

                percent = (
                    completed
                    / total_chunks
                    * 100
                )

                print(
                    f"Chunks: "
                    f"{completed:,}/"
                    f"{total_chunks:,} "
                    f"({percent:.1f}%)"
                )

    # --------------------------------------------------------
    # grid.geojson
    # --------------------------------------------------------

    print()
    print(
        "Writing grid.geojson..."
    )

    write_geojson(
        OUTPUT_DIR
        / "grid.geojson",
        grid_features,
    )

    # --------------------------------------------------------
    # summary.json
    # --------------------------------------------------------

    print(
        "Writing summary.json..."
    )

    summary = {
        "version": 2,
        "processor": "pune-processor-py",
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
    # Create tar.gz
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
    # Final report
    # --------------------------------------------------------

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
        f"Total chunks       : "
        f"{total_chunks:,}"
    )

    print(
        f"Non-empty chunks   : "
        f"{nonempty_chunks:,}"
    )

    print(
        f"Buildings          : "
        f"{total_buildings:,}"
    )

    print(
        f"Roads              : "
        f"{total_roads:,}"
    )

    print(
        f"Waterways          : "
        f"{total_waterways:,}"
    )

    print(
        f"Output directory   : "
        f"{OUTPUT_DIR}"
    )

    print(
        f"Archive            : "
        f"{archive_path}"
    )

    print(
        f"Archive size       : "
        f"{archive_size_mb:.2f} MB"
    )

    print("=" * 70)
    print()


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    main()
