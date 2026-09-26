"""
catchment.py
------------
High-level orchestration: KML/KMZ bytes in -> structured catchment
analysis result out. This is what the API route calls.

Phase 3 additions:
  - water_volume_m3 estimation using area × estimated average depth.
  - bbox parameter to clip analysis to a user-selected map region.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Tuple

import numpy as np
from skimage import measure

from . import hydrology
from .dem import DEM, build_dem
from .kml_parser import parse_contours, BBox

# Default max pond depth cap used for water volume estimation.
# Farm ponds are rarely deeper than 3-4 m; capping prevents unrealistic
# volumes when the contour map has a large relief range.
DEFAULT_MAX_POND_DEPTH_M = 3.0


@dataclass
class CatchmentResult:
    pond_location: dict
    catchment: dict
    water_volume: dict
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


def _estimate_water_volume(
    area_m2: float,
    relief_m: float,
    max_depth_m: float = DEFAULT_MAX_POND_DEPTH_M,
) -> dict:
    """
    Estimate the water volume a pond at this location could collect.

    Method: The average depth of a simple farm pond is roughly 1/4 of
    the local relief (the height difference that drives water into the
    basin), capped at `max_depth_m` to avoid unrealistic values for
    large, mountainous catchments. The pond is modelled as a bowl whose
    average cross-section is half the maximum area (triangular profile).

        avg_depth_m  = min(relief_m / 4, max_depth_m)
        volume_m3    = area_m2 × avg_depth_m × 0.5   (bowl factor)

    This is a planning-level estimate only — actual volume depends on
    site-specific soil excavation and pond design.
    """
    avg_depth_m = min(relief_m / 4.0, max_depth_m)
    avg_depth_m = max(avg_depth_m, 0.1)  # sanity floor

    # Bowl factor 0.5: average cross-section is half the surface area
    volume_m3 = area_m2 * avg_depth_m * 0.5
    volume_liters = volume_m3 * 1_000.0
    volume_million_liters = volume_liters / 1_000_000.0

    return {
        "estimated_volume_m3": round(volume_m3, 1),
        "estimated_volume_liters": round(volume_liters, 0),
        "estimated_volume_million_liters": round(volume_million_liters, 4),
        "estimated_avg_depth_m": round(avg_depth_m, 2),
        "method": (
            "Terrain-based estimate: avg_depth = min(relief/4, max_depth_cap) "
            f"= {round(avg_depth_m, 2)} m; "
            f"volume = area × avg_depth × 0.5 (bowl factor)."
        ),
        "note": "Planning-level estimate only. Actual volume depends on site design.",
    }


def analyze(
    file_bytes: bytes,
    target_cells: int = 250_000,
    min_catchment_fraction: float = 0.0001,
    max_river_fraction: float = 0.0015,
    avoid_main_river: bool = True,
    bbox: Optional[BBox] = None,
    max_depth_m: float = DEFAULT_MAX_POND_DEPTH_M,
) -> CatchmentResult:
    """
    Run the full pond catchment analysis pipeline.

    Parameters
    ----------
    file_bytes : bytes
        Raw KML/KMZ bytes.
    target_cells : int
        DEM grid resolution knob.
    min_catchment_fraction : float
        Minimum fraction of total DEM cells for a valid catchment.
    max_river_fraction : float
        Accumulation fraction above which a cell is treated as a main river.
    avoid_main_river : bool
        Skip cells on main river/stream channels.
    bbox : (min_lon, min_lat, max_lon, max_lat) or None
        Clip contour data to this geographic bounding box (Phase 3 map area).
    max_depth_m : float
        Cap for estimated pond depth used in water volume calculation.
    """
    warnings: list[str] = []

    points = parse_contours(file_bytes, bbox_filter=bbox)
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
    relief_m = float(catchment_elevs.max() - catchment_elevs.min())
    boundary_ring = _boundary_polygon(dem, mask)

    water_volume = _estimate_water_volume(area_m2, relief_m, max_depth_m)

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
            "relief_m": round(relief_m, 2),
            "boundary_polygon": {
                "type": "Polygon",
                "coordinates": [boundary_ring] if boundary_ring else [],
            },
        },
        water_volume=water_volume,
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
            "bbox_filter_applied": bbox is not None,
        },
        warnings=warnings,
    )
    return result
