"""
catchment.py
------------
High-level orchestration: KML/KMZ bytes in -> structured catchment
analysis result out. This is what the API route calls.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from skimage import measure

from . import hydrology
from .dem import DEM, build_dem
from .kml_parser import parse_contours


@dataclass
class CatchmentResult:
    pond_location: dict
    catchment: dict
    dem_summary: dict
    input_summary: dict
    warnings: list[str] = field(default_factory=list)


def _boundary_polygon(dem: DEM, mask: np.ndarray) -> list[list[float]]:
    """Trace the outer boundary of the catchment mask and return it as a
    list of [lon, lat] pairs (closed ring)."""
    padded = np.pad(mask.astype(np.uint8), 1, mode="constant")
    contours = measure.find_contours(padded, level=0.5)
    if not contours:
        return []
    # Keep the longest contour (outer boundary; small internal artefacts,
    # if any, are discarded).
    contour = max(contours, key=len)
    rows = contour[:, 0] - 1  # undo padding
    cols = contour[:, 1] - 1
    lons, lats = dem.grid_to_lonlat(rows, cols)
    ring = [[float(lo), float(la)] for lo, la in zip(lons, lats)]
    if ring and ring[0] != ring[-1]:
        ring.append(ring[0])
    return ring


def analyze(
    file_bytes: bytes,
    target_cells: int = 250_000,
    min_catchment_fraction: float = 0.005,
    max_river_fraction: float = 0.35,
    avoid_main_river: bool = True,
) -> CatchmentResult:
    warnings: list[str] = []

    points = parse_contours(file_bytes)
    dem = build_dem(points, target_cells=target_cells)

    flow = hydrology.build_flow_model(dem.elevation, dem.cell_size_m)
    outlet_rc = hydrology.find_pour_point(
        flow,
        dem.elevation,
        min_catchment_fraction=min_catchment_fraction,
        max_river_fraction=max_river_fraction,
        avoid_main_river=avoid_main_river,
    )
    mask = hydrology.delineate_catchment(flow, outlet_rc)

    n_cells = int(mask.sum())
    area_m2 = n_cells * dem.cell_area_m2()
    area_ha = area_m2 / 10_000.0

    rows, cols = dem.shape
    if n_cells >= (rows * cols) * 0.9:
        warnings.append(
            "Delineated catchment covers nearly the entire input extent - "
            "the contour map may not include the full watershed, or terrain "
            "in this tile drains almost entirely toward one edge."
        )

    outlet_r, outlet_c = outlet_rc
    outlet_lon, outlet_lat = dem.grid_to_lonlat(outlet_r, outlet_c)
    outlet_elev = float(dem.elevation[outlet_r, outlet_c])

    catchment_elevs = dem.elevation[mask]
    boundary_ring = _boundary_polygon(dem, mask)

    result = CatchmentResult(
        pond_location={
            "longitude": round(float(outlet_lon), 7),
            "latitude": round(float(outlet_lat), 7),
            "elevation_m": round(outlet_elev, 2),
            "selection_method": (
                "Off-stream interior topographic sink / sub-catchment pour point "
                "with optimal contributing area, avoiding main river channels."
            ),
        },
        catchment={
            "area_m2": round(area_m2, 1),
            "area_hectares": round(area_ha, 3),
            "cell_count": n_cells,
            "cell_size_m": round(dem.cell_size_m, 3),
            "elevation_range_m": [
                round(float(catchment_elevs.min()), 2),
                round(float(catchment_elevs.max()), 2),
            ],
            "relief_m": round(
                float(catchment_elevs.max() - catchment_elevs.min()), 2
            ),
            "boundary_polygon": {
                "type": "Polygon",
                "coordinates": [boundary_ring] if boundary_ring else [],
            },
        },
        dem_summary={
            "grid_rows": rows,
            "grid_cols": cols,
            "cell_size_m": round(dem.cell_size_m, 3),
            "elevation_min_m": round(float(dem.elevation.min()), 2),
            "elevation_max_m": round(float(dem.elevation.max()), 2),
        },
        input_summary={
            "contour_lines": points.n_lines,
            "contour_vertices": int(points.lons.shape[0]),
            "elevation_levels": points.elevation_levels,
            "bounds": {
                "min_lon": round(points.bounds[0], 7),
                "min_lat": round(points.bounds[1], 7),
                "max_lon": round(points.bounds[2], 7),
                "max_lat": round(points.bounds[3], 7),
            },
        },
        warnings=warnings,
    )
    return result
