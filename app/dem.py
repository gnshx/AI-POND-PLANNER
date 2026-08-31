"""
dem.py
------
Turns a scattered (lon, lat, elevation) point cloud - extracted from
contour lines - into a regular-grid Digital Elevation Model (DEM) that
the hydrology module can run flow routing on.

Kept deliberately dependency-light (numpy + scipy only) and tuned by a
single "target_cells" knob so behaviour is predictable and stays inside
a small memory budget (see MAX_POINTS / target_cells below), which
matters when this runs on a ~2GB RAM server.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.interpolate import griddata
from scipy.ndimage import gaussian_filter

from .kml_parser import ContourPointCloud

EARTH_RADIUS_M = 6_371_000.0

# Interpolating a Delaunay triangulation gets expensive past ~O(10^5) points.
# Contour data is highly redundant (many near-collinear vertices along each
# line), so systematic thinning barely affects interpolation quality.
MAX_INTERP_POINTS = 60_000

# Caps grid size so peak memory / runtime stay small on constrained hosts.
DEFAULT_TARGET_CELLS = 250_000
MIN_TARGET_CELLS = 10_000
MAX_TARGET_CELLS = 600_000


@dataclass
class DEM:
    elevation: np.ndarray  # (rows, cols) float32, metres
    cell_size_m: float  # square cell edge length, metres
    lon0: float  # projection origin (grid x=0 maps here)
    lat0: float  # projection origin (grid y=0 maps here)
    valid_mask: np.ndarray  # (rows, cols) bool - True where data was interpolated (not extrapolated far outside hull)

    @property
    def shape(self):
        return self.elevation.shape

    def grid_to_lonlat(self, row: np.ndarray | float, col: np.ndarray | float):
        """Convert grid indices back to (lon, lat) using the equirectangular
        projection this DEM was built with."""
        x_m = col * self.cell_size_m
        y_m = row * self.cell_size_m
        lat = self.lat0 + (y_m / EARTH_RADIUS_M) * (180.0 / np.pi)
        lon = self.lon0 + (x_m / (EARTH_RADIUS_M * np.cos(np.radians(self.lat0)))) * (
            180.0 / np.pi
        )
        return lon, lat

    def cell_area_m2(self) -> float:
        return self.cell_size_m * self.cell_size_m


def _project_equirectangular(lons: np.ndarray, lats: np.ndarray):
    """Local equirectangular projection, accurate to well under 1% for the
    few-kilometre extents contour maps like this typically cover."""
    lat0 = float(lats.mean())
    lon0 = float(lons.min())
    lat_min = float(lats.min())
    x_m = (
        np.radians(lons - lon0)
        * EARTH_RADIUS_M
        * np.cos(np.radians(lat0))
    )
    y_m = np.radians(lats - lat_min) * EARTH_RADIUS_M
    return x_m, y_m, lon0, lat_min


def _thin(*arrays: np.ndarray, max_points: int):
    n = arrays[0].shape[0]
    if n <= max_points:
        return arrays
    stride = int(np.ceil(n / max_points))
    return tuple(a[::stride] for a in arrays)


def build_dem(
    points: ContourPointCloud,
    target_cells: int = DEFAULT_TARGET_CELLS,
    smoothing_sigma: float = 1.0,
) -> DEM:
    """
    Build a regular-grid DEM from a contour point cloud.

    target_cells bounds the total number of grid cells (rows*cols), which
    keeps both the scipy Delaunay interpolation and the downstream D8
    flow-routing pass fast and memory-bounded regardless of how large or
    detailed the input contour map is.
    """
    target_cells = int(np.clip(target_cells, MIN_TARGET_CELLS, MAX_TARGET_CELLS))

    x_m, y_m, lon0, lat0 = _project_equirectangular(points.lons, points.lats)
    elev = points.elevations

    x_m, y_m, elev = _thin(x_m, y_m, elev, max_points=MAX_INTERP_POINTS)

    width_m = float(x_m.max() - x_m.min())
    height_m = float(y_m.max() - y_m.min())
    area_m2 = max(width_m * height_m, 1.0)

    cell_size_m = float(np.sqrt(area_m2 / target_cells))
    cell_size_m = max(cell_size_m, 0.1)  # sanity floor

    n_cols = max(int(np.ceil(width_m / cell_size_m)) + 1, 2)
    n_rows = max(int(np.ceil(height_m / cell_size_m)) + 1, 2)

    grid_x = np.arange(n_cols) * cell_size_m
    grid_y = np.arange(n_rows) * cell_size_m
    gx, gy = np.meshgrid(grid_x, grid_y)  # shape (n_rows, n_cols)

    # x_m/y_m were built relative to lon.min()/lat.min(), matching grid origin.
    pts = np.column_stack([x_m, y_m])

    interp_linear = griddata(pts, elev, (gx, gy), method="linear")
    valid_mask = ~np.isnan(interp_linear)

    # Fill points outside the convex hull (edges/corners) with nearest-neighbour
    # extrapolation so the DEM has no holes for flow routing to fall through.
    interp_nearest = griddata(pts, elev, (gx, gy), method="nearest")
    filled = np.where(valid_mask, interp_linear, interp_nearest)

    if smoothing_sigma and smoothing_sigma > 0:
        # Contour-derived DEMs interpolated from a TIN have small facet-edge
        # artefacts (flat triangles) that create spurious 1-cell pits/flats.
        # A light Gaussian smooth removes these without erasing real terrain
        # features at the working resolution.
        filled = gaussian_filter(filled, sigma=smoothing_sigma, mode="nearest")

    return DEM(
        elevation=filled.astype(np.float32),
        cell_size_m=cell_size_m,
        lon0=lon0,
        lat0=lat0,
        valid_mask=valid_mask,
    )
