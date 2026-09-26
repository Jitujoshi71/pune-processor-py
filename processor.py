#!/usr/bin/env python3

import json
import math
import os
import shutil
import tarfile
from pathlib import Path
from collections import defaultdict

import osmium
from shapely.geometry import LineString, Polygon, box, mapping


RAW_DIR = Path(os.getenv("RAW_DIR", "raw"))
OSM_FILE = Path(
    os.getenv(
        "OSM_FILE",
        str(RAW_DIR / "central-zone.osm.pbf")
    )
)

OUTPUT_DIR = Path(
    os.getenv("OUTPUT_DIR", "data/chunks")
)

CHUNK_SIZE_M = float(
    os.getenv("CHUNK_SIZE_M", "1000")
)

# Pune-wide default bounding box.
# Override using:
# BBOX=min_lon,min_lat,max_lon,max_lat
DEFAULT_BBOX = (
    73.70,
    18.40,
    74.05,
    18.70,
)


def parse_bbox():
    value = os.getenv("BBOX")

    if not value:
        return DEFAULT_BBOX

    values = [
        float(x.strip())
        for x in value.split(",")
    ]

    if len(values) != 4:
        raise ValueError(
            "BBOX must be: "
            "min_lon,min_lat,max_lon,max_lat"
        )

    min_lon, min_lat, max_lon, max_lat = values

    if min_lon >= max_lon or min_lat >= max_lat:
        raise ValueError(
            "Invalid BBOX coordinates"
        )

    return (
        min_lon,
        min_lat,
        max_lon,
        max_lat,
    )


# ---------------------------------------------------------
# Simple local meter projection
# ---------------------------------------------------------

def lon_to_m(lon, lat0):
    return (
        lon
        * 111320.0
        * math.cos(math.radians(lat0))
    )


def lat_to_m(lat):
    return lat * 110540.0


def m_to_lon(x, lat0):
    return x / (
        111320.0
        * math.cos(math.radians(lat0))
    )


def m_to_lat(y):
    return y / 110540.0


# ---------------------------------------------------------
# OSM collector
# ---------------------------------------------------------

class OSMCollector(osmium.SimpleHandler):

    def __init__(self):
        super().__init__(locations=True)

        self.features = []

    @staticmethod
    def get_tags(obj):
        return {
            key: value
            for key, value in obj.tags
        }

    def way(self, way):

        tags = self.get_tags(way)

        try:
            coords = [
                (node.lon, node.lat)
                for node in way.nodes
                if node.location.valid()
            ]
        except Exception:
            return

        if len(coords) < 2:
            return

        # ---------------------------------------------
        # Buildings
        # ---------------------------------------------

        if (
            tags.get("building")
            and len(coords) >= 4
            and coords[0] == coords[-1]
        ):
            try:
                geometry = Polygon(coords)

                if not geometry.is_valid:
                    geometry = geometry.buffer(0)

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

            except Exception:
                pass

            return

        # ---------------------------------------------
        # Roads
        # ---------------------------------------------

        highway = tags.get("highway")

        if highway:

            try:
                geometry = LineString(coords)

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

            except Exception:
                pass

        # ---------------------------------------------
        # Waterways
        # ---------------------------------------------

        if tags.get("waterway"):

            try:
                geometry = LineString(coords)

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

            except Exception:
                pass


# ---------------------------------------------------------
# GeoJSON
# ---------------------------------------------------------

def feature_json(
    feature_type,
    osm_id,
    geometry,
    tags,
):

    properties = {
        "osm_id": int(osm_id),
        **tags,
    }

    return {
        "type": "Feature",
        "properties": properties,
        "geometry": mapping(geometry),
    }


def write_geojson(path, features):

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    data = {
        "type": "FeatureCollection",
        "features": features,
    }

    path.write_text(
        json.dumps(
            data,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )


# ---------------------------------------------------------
# Main
# ---------------------------------------------------------

def main():

    if not OSM_FILE.exists():
        raise FileNotFoundError(
            f"OSM file not found: {OSM_FILE}"
        )

    (
        min_lon,
        min_lat,
        max_lon,
        max_lat,
    ) = parse_bbox()

    lat0 = (
        min_lat + max_lat
    ) / 2.0

    # ---------------------------------------------
    # Geographic coordinates -> local meters
    # ---------------------------------------------

    x0 = lon_to_m(
        min_lon,
        lat0,
    )

    y0 = lat_to_m(
        min_lat
    )

    x1 = lon_to_m(
        max_lon,
        lat0,
    )

    y1 = lat_to_m(
        max_lat
    )

    nx = math.ceil(
        (x1 - x0)
        / CHUNK_SIZE_M
    )

    ny = math.ceil(
        (y1 - y0)
        / CHUNK_SIZE_M
    )

    print()
    print("=" * 60)
    print("PUNE PROCESSOR")
    print("=" * 60)

    print(
        f"OSM file: {OSM_FILE}"
    )

    print(
        "BBOX:",
        min_lon,
        min_lat,
        max_lon,
        max_lat,
    )

    print(
        f"Grid: {nx} x {ny}"
    )

    print(
        f"Total chunks: {nx * ny}"
    )

    print(
        f"Chunk size: {CHUNK_SIZE_M} meters"
    )

    print("=" * 60)

    # ---------------------------------------------
    # Parse OSM PBF
    # ---------------------------------------------

    collector = OSMCollector()

    print(
        "Reading OSM PBF..."
    )

    collector.apply_file(
        str(OSM_FILE),
        locations=True,
    )

    print(
        f"Collected OSM ways: "
        f"{len(collector.features):,}"
    )

    # ---------------------------------------------
    # Clean old output
    # ---------------------------------------------

    if OUTPUT_DIR.exists():
        print(
            f"Removing old output: "
            f"{OUTPUT_DIR}"
        )

        shutil.rmtree(
            OUTPUT_DIR
        )

    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    # ---------------------------------------------
    # Group geometry into chunks
    # ---------------------------------------------

    grouped = defaultdict(list)

    type_counts = defaultdict(int)

    city_bbox = box(
        min_lon,
        min_lat,
        max_lon,
        max_lat,
    )

    for (
        feature_type,
        osm_id,
        geometry,
        tags,
    ) in collector.features:

        if not geometry.intersects(
            city_bbox
        ):
            continue

        (
            geom_min_lon,
            geom_min_lat,
            geom_max_lon,
            geom_max_lat,
        ) = geometry.bounds

        ix0 = max(
            0,
            int(
                math.floor(
                    (
                        lon_to_m(
                            geom_min_lon,
                            lat0,
                        )
                        - x0
                    )
                    / CHUNK_SIZE_M
                )
            ),
        )

        iy0 = max(
            0,
            int(
                math.floor(
                    (
                        lat_to_m(
                            geom_min_lat
                        )
                        - y0
                    )
                    / CHUNK_SIZE_M
                )
            ),
        )

        ix1 = min(
            nx - 1,
            int(
                math.floor(
                    (
                        lon_to_m(
                            geom_max_lon,
                            lat0,
                        )
                        - x0
                    )
                    / CHUNK_SIZE_M
                )
            ),
        )

        iy1 = min(
            ny - 1,
            int(
                math.floor(
                    (
                        lat_to_m(
                            geom_max_lat
                        )
                        - y0
                    )
                    / CHUNK_SIZE_M
                )
            ),
        )

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
                    (
                        feature_type,
                        osm_id,
                        geometry,
                        tags,
                    )
                )

                type_counts[
                    feature_type
                ] += 1

    # ---------------------------------------------
    # Create chunks
    # ---------------------------------------------

    grid_features = []

    chunk_summaries = []

    nonempty_chunks = 0

    for iy in range(ny):

        for ix in range(nx):

            chunk_id = (
                f"PUNE_{ix:04d}_{iy:04d}"
            )

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

            lon_a = m_to_lon(
                chunk_min_x,
                lat0,
            )

            lon_b = m_to_lon(
                chunk_max_x,
                lat0,
            )

            lat_a = m_to_lat(
                chunk_min_y
            )

            lat_b = m_to_lat(
                chunk_max_y
            )

            chunk_bbox = box(
                lon_a,
                lat_a,
                lon_b,
                lat_b,
            )

            items = grouped.get(
                (ix, iy),
                [],
            )

            buildings = []
            roads = []
            waterways = []

            seen = set()

            for (
                feature_type,
                osm_id,
                geometry,
                tags,
            ) in items:

                key = (
                    feature_type,
                    osm_id,
                )

                if key in seen:
                    continue

                seen.add(key)

                try:
                    clipped = geometry.intersection(
                        chunk_bbox
                    )
                except Exception:
                    continue

                if clipped.is_empty:
                    continue

                if feature_type == "building":

                    buildings.append(
                        feature_json(
                            feature_type,
                            osm_id,
                            clipped,
                            tags,
                        )
                    )

                elif feature_type == "road":

                    roads.append(
                        feature_json(
                            feature_type,
                            osm_id,
                            clipped,
                            tags,
                        )
                    )

                elif feature_type == "waterway":

                    waterways.append(
                        feature_json(
                            feature_type,
                            osm_id,
                            clipped,
                            tags,
                        )
                    )

            if (
                buildings
                or roads
                or waterways
            ):
                nonempty_chunks += 1

            # -------------------------------------
            # Chunk directory
            # -------------------------------------

            chunk_dir = (
                OUTPUT_DIR
                / chunk_id
            )

            chunk_dir.mkdir(
                parents=True,
                exist_ok=True,
            )

            metadata = {
                "chunk_id": chunk_id,
                "grid_x": ix,
                "grid_y": iy,
                "size_m": CHUNK_SIZE_M,
                "bounds_wgs84": [
                    lon_a,
                    lat_a,
                    lon_b,
                    lat_b,
                ],
                "crs": "EPSG:4326",
                "source": OSM_FILE.name,
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

            (
                chunk_dir
                / "metadata.json"
            ).write_text(
                json.dumps(
                    metadata,
                    indent=2,
                ),
                encoding="utf-8",
            )

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

            # -------------------------------------
            # Grid
            # -------------------------------------

            grid_features.append(
                {
                    "type": "Feature",
                    "properties": {
                        "chunk_id": chunk_id,
                        "grid_x": ix,
                        "grid_y": iy,
                    },
                    "geometry": mapping(
                        chunk_bbox
                    ),
                }
            )

            chunk_summaries.append(
                metadata
            )

    # ---------------------------------------------
    # grid.geojson
    # ---------------------------------------------

    write_geojson(
        OUTPUT_DIR / "grid.geojson",
        grid_features,
    )

    # ---------------------------------------------
    # summary.json
    # ---------------------------------------------

    summary = {
        "version": 2,
        "chunk_size_m": CHUNK_SIZE_M,
        "bbox_wgs84": [
            min_lon,
            min_lat,
            max_lon,
            max_lat,
        ],
        "grid": {
            "nx": nx,
            "ny": ny,
            "total_chunks": nx * ny,
            "nonempty_chunks": nonempty_chunks,
        },
        "feature_counts": dict(
            type_counts
        ),
        "chunks": chunk_summaries,
    }

    (
        OUTPUT_DIR / "summary.json"
    ).write_text(
        json.dumps(
            summary,
            indent=2,
        ),
        encoding="utf-8",
    )

    # ---------------------------------------------
    # Create artifact archive
    # ---------------------------------------------

    archive = (
        OUTPUT_DIR.parent.parent
        / "pune-1km-chunks.tar.gz"
    )

    if archive.exists():
        archive.unlink()

    with tarfile.open(
        archive,
        "w:gz",
    ) as tar:

        tar.add(
            OUTPUT_DIR,
            arcname="data/chunks",
        )

    # ---------------------------------------------
    # Final report
    # ---------------------------------------------

    print()
    print("=" * 60)
    print("PROCESSING COMPLETE")
    print("=" * 60)

    print(
        f"Total chunks: "
        f"{nx * ny:,}"
    )

    print(
        f"Non-empty chunks: "
        f"{nonempty_chunks:,}"
    )

    print(
        f"Buildings: "
        f"{type_counts.get('building', 0):,}"
    )

    print(
        f"Roads: "
        f"{type_counts.get('road', 0):,}"
    )

    print(
        f"Waterways: "
        f"{type_counts.get('waterway', 0):,}"
    )

    print(
        f"Output: {OUTPUT_DIR}"
    )

    print(
        f"Archive: {archive}"
    )

    print("=" * 60)


if __name__ == "__main__":
    main()
