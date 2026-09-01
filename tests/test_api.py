"""
API integration tests for FastAPI endpoints.
Verifies file upload under both parameter names ('contour_map' and 'file'),
endpoint aliases, health check, and error responses.
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


def test_root_and_health():
    res_root = client.get("/")
    assert res_root.status_code == 200
    assert res_root.json()["status"] == "ok"

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
