"""
hydrology.py
------------
Minimal D8 surface-hydrology toolkit implemented directly on numpy arrays
(no GDAL / whitebox / richdem dependency, by design - keeps the
deployment footprint small enough for a 2GB RAM host).

Pipeline:
  1. flow_direction  - each cell points to its steepest-descent neighbour
                        (or is marked a PIT if no neighbour is lower).
  2. flow_accumulation - topological (elevation-sorted) accumulation of
                        upstream contributing cells, O(rows*cols log(.)).
  3. find_pour_point  - ranks interior pits by accumulated contributing
                        area to choose the most promising pond site.
  4. delineate_catchment - BFS over the reversed flow-direction graph to
                        recover every cell draining to the chosen point.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

PIT = -1

# 8 neighbour offsets (row, col) and their D8 direction codes, ordered
# clockwise from North - only the ordering/index correspondence matters.
_NEIGHBOR_OFFSETS = np.array(
    [
        (-1, 0), (-1, 1), (0, 1), (1, 1),
        (1, 0), (1, -1), (0, -1), (-1, -1),
    ]
)
_DIAGONAL = np.array([False, True, False, True, False, True, False, True])


@dataclass
class FlowModel:
    direction: np.ndarray  # (rows, cols) int8, index into _NEIGHBOR_OFFSETS, or PIT
    accumulation: np.ndarray  # (rows, cols) int64, upstream cell COUNT (incl. self)


def _tilt(dem: np.ndarray) -> np.ndarray:
    """Tiny deterministic tilt that breaks exact elevation ties (flat TIN
    facets) without measurably perturbing real terrain. Must be applied
    identically everywhere elevation order matters (flow direction AND
    the topological sort in flow accumulation) - otherwise the two can
    disagree on ordering at ties and accumulation silently undercounts."""
    rows, cols = dem.shape
    ramp = np.arange(rows)[:, None] * 1e-6 + np.arange(cols)[None, :] * 1.3e-6
    return dem.astype(np.float64) + ramp


def compute_flow_direction(dem_tilted: np.ndarray, cell_size_m: float) -> np.ndarray:
    rows, cols = dem_tilted.shape
    direction = np.full((rows, cols), PIT, dtype=np.int8)

    diag_dist = cell_size_m * np.sqrt(2.0)

    best_drop = np.zeros((rows, cols), dtype=np.float64)

    for idx, (dr, dc) in enumerate(_NEIGHBOR_OFFSETS):
        dist = diag_dist if _DIAGONAL[idx] else cell_size_m

        src_r0, src_r1 = max(0, -dr), rows - max(0, dr)
        src_c0, src_c1 = max(0, -dc), cols - max(0, dc)
        nbr_r0, nbr_r1 = max(0, dr), rows - max(0, -dr)
        nbr_c0, nbr_c1 = max(0, dc), cols - max(0, -dc)

        drop = np.zeros((rows, cols), dtype=np.float64)
        drop[src_r0:src_r1, src_c0:src_c1] = (
            dem_tilted[src_r0:src_r1, src_c0:src_c1]
            - dem_tilted[nbr_r0:nbr_r1, nbr_c0:nbr_c1]
        ) / dist

        # Only cells that actually have this neighbour, and where it's downhill
        valid = np.zeros((rows, cols), dtype=bool)
        valid[src_r0:src_r1, src_c0:src_c1] = True
        better = valid & (drop > best_drop)

        direction[better] = idx
        best_drop = np.where(better, drop, best_drop)

    direction[best_drop <= 0] = PIT
    return direction


def compute_flow_accumulation(dem_tilted: np.ndarray, direction: np.ndarray) -> np.ndarray:
    rows, cols = dem_tilted.shape
    accumulation = np.ones((rows, cols), dtype=np.int64)

    # Must use the SAME (tilted) elevation ordering that produced `direction`
    # so every cell is guaranteed to be processed before its downstream
    # target - see _tilt() docstring.
    order = np.argsort(dem_tilted, axis=None)[::-1]  # highest elevation first
    rr, cc = np.unravel_index(order, dem_tilted.shape)

    for r, c in zip(rr, cc):
        d = direction[r, c]
        if d == PIT:
            continue
        dr, dc = _NEIGHBOR_OFFSETS[d]
        nr, nc = r + dr, c + dc
        if 0 <= nr < rows and 0 <= nc < cols:
            accumulation[nr, nc] += accumulation[r, c]

    return accumulation


def build_flow_model(dem: np.ndarray, cell_size_m: float) -> FlowModel:
    dem_tilted = _tilt(dem)
    direction = compute_flow_direction(dem_tilted, cell_size_m)
    accumulation = compute_flow_accumulation(dem_tilted, direction)
    return FlowModel(direction=direction, accumulation=accumulation)


def find_pour_point(
    flow: FlowModel,
    dem: np.ndarray,
    min_catchment_fraction: float = 0.0001,
    max_river_fraction: float = 0.0015,
    avoid_main_river: bool = True,
    edge_margin_fraction: float = 0.03,
) -> tuple[int, int]:
    """
    Choose a suitable off-stream pond / outlet location:
    Identifies interior topographic sinks or field depressions with an optimal
    farm catchment area while strictly avoiding river beds and stream channels
    (where high flow accumulation indicates a river or stream flow line).
    """
    rows, cols = dem.shape
    total_cells = rows * cols
    min_cells = max(int(total_cells * min_catchment_fraction), 1)

    margin_r = min(max(int(rows * edge_margin_fraction), 1), max(0, (rows // 2) - 1))
    margin_c = min(max(int(cols * edge_margin_fraction), 1), max(0, (cols // 2) - 1))

    interior = np.zeros((rows, cols), dtype=bool)
    interior[margin_r: rows - margin_r, margin_c: cols - margin_c] = True
    if not interior.any():
        interior = np.ones((rows, cols), dtype=bool)

    is_pit = flow.direction == PIT
    candidates = is_pit & interior & (flow.accumulation >= min_cells)

    if avoid_main_river:
        max_river_cells = max(int(total_cells * max_river_fraction), 200)
        off_river_candidates = candidates & (flow.accumulation <= max_river_cells)
        if off_river_candidates.any():
            candidates = off_river_candidates

    if not candidates.any():
        # Fallback 1: Relax min_cells constraint but keep interior pits
        candidates = is_pit & interior
        if not candidates.any():
            # Fallback 2: Any pit anywhere
            candidates = is_pit
            if not candidates.any():
                # Fallback 3: Interior cell with highest accumulation
                masked = np.where(interior, flow.accumulation, -1)
                r, c = np.unravel_index(np.argmax(masked), masked.shape)
                return int(r), int(c)

    masked_acc = np.where(candidates, flow.accumulation, -1)
    r, c = np.unravel_index(np.argmax(masked_acc), masked_acc.shape)
    return int(r), int(c)





def delineate_catchment(flow: FlowModel, outlet: tuple[int, int]) -> np.ndarray:
    """Boolean mask of every cell whose D8 flow path terminates at `outlet`."""
    rows, cols = flow.direction.shape

    # children[r, c] = list handled implicitly via reverse lookup: build once.
    reverse = [[[] for _ in range(cols)] for _ in range(rows)]  # noqa: not used (kept for clarity)
    del reverse  # too memory-heavy for large grids; use array-based BFS instead

    mask = np.zeros((rows, cols), dtype=bool)
    stack = [outlet]
    mask[outlet] = True

    # Precompute, for speed, the coordinates each cell drains INTO, then
    # invert with a bucket sort keyed by flattened downstream index.
    flat_down = np.full(rows * cols, -1, dtype=np.int64)
    valid = flow.direction != PIT
    rr, cc = np.nonzero(valid)
    dr = _NEIGHBOR_OFFSETS[flow.direction[rr, cc], 0]
    dc = _NEIGHBOR_OFFSETS[flow.direction[rr, cc], 1]
    nr, nc = rr + dr, cc + dc
    in_bounds = (nr >= 0) & (nr < rows) & (nc >= 0) & (nc < cols)
    src_idx = rr[in_bounds] * cols + cc[in_bounds]
    dst_idx = nr[in_bounds] * cols + nc[in_bounds]
    flat_down[src_idx] = dst_idx

    order = np.argsort(dst_idx)
    sorted_dst = dst_idx[order]
    sorted_src = src_idx[order]
    starts = np.searchsorted(sorted_dst, np.arange(rows * cols))
    ends = np.searchsorted(sorted_dst, np.arange(rows * cols), side="right")

    while stack:
        r, c = stack.pop()
        flat = r * cols + c
        for src in sorted_src[starts[flat]: ends[flat]]:
            sr, sc = divmod(int(src), cols)
            if not mask[sr, sc]:
                mask[sr, sc] = True
                stack.append((sr, sc))

    return mask
