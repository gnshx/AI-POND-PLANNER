"""
Basic sanity tests for the catchment analysis pipeline.
Run with:  pytest -v
"""
import io
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import hydrology  # noqa: E402
from app.catchment import analyze  # noqa: E402
from app.kml_parser import parse_contours  # noqa: E402

LOCAL_SAMPLE = os.path.join(os.path.dirname(__file__), "sample_contours_1m.kml")


def _sample_bytes():
    for path in (LOCAL_SAMPLE, "/mnt/user-data/uploads/contours_1m.kml"):
        if os.path.exists(path):
            with open(path, "rb") as f:
                return f.read()
    pytest.skip("Sample contour KML not found (expected tests/sample_contours_1m.kml)")


def test_parse_contours_sample():
    pc = parse_contours(_sample_bytes())
    assert pc.lons.shape[0] > 0
    assert pc.n_lines > 0
    assert len(pc.elevation_levels) > 1
    assert pc.bounds[0] < pc.bounds[2]
    assert pc.bounds[1] < pc.bounds[3]


def test_parse_contours_rejects_empty():
    with pytest.raises(ValueError):
        parse_contours(b"")


def test_parse_contours_rejects_no_data():
    kml = b'<kml xmlns="http://www.opengis.net/kml/2.2"><Document></Document></kml>'
    with pytest.raises(ValueError):
        parse_contours(kml)


def test_flow_accumulation_matches_catchment_size():
    """Regression test for the tie-breaking bug: accumulation at the outlet
    must exactly equal the number of cells the BFS delineation finds."""
    dem = np.array(
        [
            [10, 9, 8, 9, 10],
            [9, 7, 6, 7, 9],
            [8, 6, 4, 6, 8],
            [9, 7, 6, 7, 9],
            [10, 9, 8, 9, 10],
        ],
        dtype=np.float64,
    )
    flow = hydrology.build_flow_model(dem, cell_size_m=1.0)
    outlet = tuple(np.argwhere(dem == dem.min())[0])
    mask = hydrology.delineate_catchment(flow, outlet)
    assert flow.accumulation[outlet] == mask.sum()
    # A symmetric bowl should drain (almost) everywhere into its centre.
    assert mask.sum() == dem.size


def test_full_analyze_pipeline():
    result = analyze(_sample_bytes(), target_cells=40_000)
    assert "longitude" in result.pond_location
    assert "latitude" in result.pond_location
    assert result.catchment["area_m2"] > 0
    assert result.catchment["cell_count"] > 0
    ring = result.catchment["boundary_polygon"]["coordinates"]
    if ring:
        assert ring[0][0] == ring[0][-1]  # closed ring


def test_analyze_is_deterministic():
    data = _sample_bytes()
    r1 = analyze(data, target_cells=40_000)
    r2 = analyze(data, target_cells=40_000)
    assert r1.pond_location == r2.pond_location
    assert r1.catchment["cell_count"] == r2.catchment["cell_count"]


def test_avoid_main_river_option():
    data = _sample_bytes()
    # When avoid_main_river=False, pour point is on the main river channel (max accumulation)
    res_river = analyze(data, target_cells=40_000, avoid_main_river=False)
    # When avoid_main_river=True (default), pour point is on a suitable tributary/off-river basin
    res_off_river = analyze(data, target_cells=40_000, avoid_main_river=True)

    assert res_river.pond_location != res_off_river.pond_location
    # The main river outlet will have a larger accumulation than an off-river tributary
    assert res_river.catchment["cell_count"] > res_off_river.catchment["cell_count"]

