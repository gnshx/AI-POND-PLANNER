"""
API integration tests for FastAPI endpoints.
Verifies file upload under both parameter names ('contour_map' and 'file'),
endpoint aliases, health check, error responses, and Phase 3 features
(water_volume, bbox filtering).
"""

import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.main import app  # noqa: E402

client = TestClient(app)
LOCAL_SAMPLE = os.path.join(os.path.dirname(__file__), "sample_contours_1m.kml")


def _sample_bytes():
    if os.path.exists(LOCAL_SAMPLE):
        with open(LOCAL_SAMPLE, "rb") as f:
            return f.read()
    pytest.skip("Sample contour KML not found")


def test_root_serves_html_or_json():
    """Root should serve the frontend HTML or a JSON fallback."""
    res_root = client.get("/")
    assert res_root.status_code == 200
    # Accepts either HTML (frontend) or JSON (fallback if static not built)
    ct = res_root.headers.get("content-type", "")
    assert "text/html" in ct or "application/json" in ct


def test_health():
    res_health = client.get("/health")
    assert res_health.status_code == 200
    assert res_health.json() == {"status": "ok"}


def test_post_contour_map_field_name():
    """Test POST /analyzeContour using TA specified parameter name 'contour_map'."""
    data = _sample_bytes()
    files = {"contour_map": ("contours_1m.kml", data, "application/vnd.google-earth.kml+xml")}
    params = {"target_cells": 40000}
    response = client.post("/analyzeContour", files=files, params=params)
    assert response.status_code == 200
    json_data = response.json()
    assert "pond_location" in json_data
    assert "catchment" in json_data
    assert json_data["catchment"]["area_m2"] > 0


def test_response_contains_water_volume():
    """Phase 3: response must include water_volume key with volume estimate."""
    data = _sample_bytes()
    files = {"contour_map": ("contours_1m.kml", data, "application/vnd.google-earth.kml+xml")}
    params = {"target_cells": 40000}
    response = client.post("/analyzeContour", files=files, params=params)
    assert response.status_code == 200
    json_data = response.json()
    assert "water_volume" in json_data
    wv = json_data["water_volume"]
    assert "estimated_volume_m3" in wv
    assert "estimated_volume_liters" in wv
    assert "estimated_volume_million_liters" in wv
    assert "estimated_avg_depth_m" in wv
    assert wv["estimated_volume_m3"] > 0


def test_response_catchment_has_boundary_polygon():
    """Catchment should include a boundary_polygon for map rendering."""
    data = _sample_bytes()
    files = {"contour_map": ("contours_1m.kml", data, "application/vnd.google-earth.kml+xml")}
    params = {"target_cells": 40000}
    response = client.post("/analyzeContour", files=files, params=params)
    assert response.status_code == 200
    ca = response.json()["catchment"]
    assert "boundary_polygon" in ca
    assert ca["boundary_polygon"]["type"] == "Polygon"


def test_bbox_filter_clips_analysis():
    """Phase 3: bbox param should restrict analysis to the drawn map area."""
    data = _sample_bytes()
    files = {"contour_map": ("contours_1m.kml", data, "application/vnd.google-earth.kml+xml")}
    # Use a valid sub-region of the sample KML (known bounds ~81.28-81.31, 21.24-21.26)
    params = {"target_cells": 30000, "bbox": "81.283,21.242,81.300,21.258"}
    response = client.post("/analyzeContour", files=files, params=params)
    assert response.status_code == 200
    json_data = response.json()
    assert "pond_location" in json_data
    assert json_data["input_summary"]["bbox_filter_applied"] is True
    # Pond must be within or near the selected bbox
    pl = json_data["pond_location"]
    assert 81.280 <= pl["longitude"] <= 81.305
    assert 21.239 <= pl["latitude"] <= 21.261


def test_bbox_filter_invalid_format():
    """Invalid bbox string should return 400."""
    data = _sample_bytes()
    files = {"contour_map": ("contours_1m.kml", data, "application/vnd.google-earth.kml+xml")}
    params = {"bbox": "not,a,valid"}  # only 3 values
    response = client.post("/analyzeContour", files=files, params=params)
    assert response.status_code == 400
    assert "bbox" in response.json()["detail"].lower()


def test_post_file_field_name_fallback():
    """Test POST /analyzeContour using alternative parameter name 'file'."""
    data = _sample_bytes()
    files = {"file": ("contours_1m.kml", data, "application/vnd.google-earth.kml+xml")}
    params = {"target_cells": 40000}
    response = client.post("/analyzeContour", files=files, params=params)
    assert response.status_code == 200
    json_data = response.json()
    assert "pond_location" in json_data


def test_post_find_catchment_alias():
    """Test POST /findCatchment route alias."""
    data = _sample_bytes()
    files = {"contour_map": ("contours_1m.kml", data, "application/vnd.google-earth.kml+xml")}
    params = {"target_cells": 40000}
    response = client.post("/findCatchment", files=files, params=params)
    assert response.status_code == 200
    assert "pond_location" in response.json()


def test_post_missing_file():
    response = client.post("/analyzeContour")
    assert response.status_code == 422


def test_post_invalid_extension():
    files = {"contour_map": ("test.txt", b"dummy content", "text/plain")}
    response = client.post("/analyzeContour", files=files)
    assert response.status_code == 400
    assert "Unsupported file type" in response.json()["detail"]
