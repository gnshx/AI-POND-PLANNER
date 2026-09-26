"""
main.py
-------
FastAPI app exposing the pond catchment analysis backend.

Phase 3 additions:
  - Serves the interactive frontend (app/static/index.html) at GET /
  - Mounts app/static/ as /static/ for CSS/JS assets
  - Accepts optional bbox query param: min_lon,min_lat,max_lon,max_lat
  - Accepts optional max_depth_m for water volume estimation
  - Returns water_volume in the JSON response

POST /analyzeContour  (alias: /findCatchment)
    multipart/form-data upload of a .kml or .kmz contour map.
    Optional query params:
      bbox (str)                     - "min_lon,min_lat,max_lon,max_lat"
      target_cells (int)             - DEM grid resolution knob (default 250000)
      min_catchment_fraction (float) - minimum candidate-basin size as a
                                        fraction of DEM extent (default 0.0001)
      max_river_fraction (float)     - main river threshold (default 0.0015)
      avoid_main_river (bool)        - avoid rivers (default true)
      max_depth_m (float)            - cap for pond depth estimation (default 3.0)

Run locally:
    uvicorn app.main:app --host 0.0.0.0 --port 4000

Memory footprint is intentionally bounded (see app/dem.py) to run
comfortably on small (~2GB RAM) hosts; see README.md for deployment notes.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from .catchment import analyze, DEFAULT_MAX_POND_DEPTH_M
from .dem import DEFAULT_TARGET_CELLS, MAX_TARGET_CELLS, MIN_TARGET_CELLS

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("pond_catchment")

app = FastAPI(
    title="Pond Catchment Analysis API",
    description=(
        "Accepts a contour map (KML/KMZ), builds a DEM, runs D8 flow "
        "routing, and returns a suitable pond location with its "
        "estimated catchment area and water volume."
    ),
    version="0.3.0",
)

# CORS — allow the frontend served on the same host to call the API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

ALLOWED_EXTENSIONS = (".kml", ".kmz")
MAX_UPLOAD_BYTES = 100 * 1024 * 1024  # 100MB

# Serve the static frontend
STATIC_DIR = Path(__file__).parent / "static"
STATIC_DIR.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/", include_in_schema=False)
def root():
    """Serve the interactive frontend."""
    index_file = STATIC_DIR / "index.html"
    if index_file.exists():
        return FileResponse(str(index_file), media_type="text/html")
    # Fallback JSON if static not built yet
    return JSONResponse({
        "service": "pond-catchment-analysis",
        "status": "ok",
        "version": "0.3.0",
        "endpoints": {
            "POST /analyzeContour": "Upload a KML/KMZ contour map for analysis",
            "GET /health": "Liveness check",
            "GET /docs": "Interactive API documentation (Swagger UI)",
        },
    })


@app.get("/health")
def health():
    return {"status": "ok"}


def _parse_bbox(bbox_str: Optional[str]) -> Optional[tuple[float, float, float, float]]:
    """Parse a comma-separated bbox string into a (min_lon, min_lat, max_lon, max_lat) tuple."""
    if not bbox_str:
        return None
    try:
        parts = [float(v.strip()) for v in bbox_str.split(",")]
        if len(parts) != 4:
            raise ValueError
        min_lon, min_lat, max_lon, max_lat = parts
        if min_lon >= max_lon or min_lat >= max_lat:
            raise ValueError("bbox min values must be less than max values")
        return (min_lon, min_lat, max_lon, max_lat)
    except ValueError as e:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Invalid bbox parameter: '{bbox_str}'. "
                "Expected format: 'min_lon,min_lat,max_lon,max_lat' "
                f"(e.g., '81.28,21.24,81.31,21.27'). Error: {e}"
            ),
        )


@app.post("/analyzeContour")
@app.post("/findCatchment")
async def analyze_contour(
    contour_map: UploadFile | None = File(
        None, description="KML or KMZ contour map (form field: contour_map)"
    ),
    file: UploadFile | None = File(
        None, description="KML or KMZ contour map (form field: file)"
    ),
    target_cells: int = Query(
        DEFAULT_TARGET_CELLS,
        ge=MIN_TARGET_CELLS,
        le=MAX_TARGET_CELLS,
        description="DEM grid resolution (total cells); lower = faster/less RAM.",
    ),
    min_catchment_fraction: float = Query(
        0.0001,
        ge=0.0,
        le=0.5,
        description="Minimum candidate basin size, as a fraction of the DEM extent.",
    ),
    max_river_fraction: float = Query(
        0.0015,
        ge=0.0001,
        le=1.0,
        description="Maximum accumulation fraction before a channel is considered a river or stream to avoid.",
    ),
    avoid_main_river: bool = Query(
        True,
        description="Avoid placing pond sites directly on main river channels.",
    ),
    bbox: Optional[str] = Query(
        None,
        description=(
            "Geographic bounding box to restrict analysis to user-selected map area. "
            "Format: 'min_lon,min_lat,max_lon,max_lat' (e.g. '81.28,21.24,81.31,21.27'). "
            "Drawn from the map UI. If omitted, the full KML extent is used."
        ),
    ),
    max_depth_m: float = Query(
        DEFAULT_MAX_POND_DEPTH_M,
        ge=0.5,
        le=20.0,
        description="Maximum estimated pond depth (metres) used in water volume calculation.",
    ),
):
    upload_file = contour_map or file
    if upload_file is None:
        raise HTTPException(
            status_code=422,
            detail="Missing contour map file. Upload a KML/KMZ file using form field 'contour_map' or 'file'.",
        )

    filename = (upload_file.filename or "").lower()
    if not filename.endswith(ALLOWED_EXTENSIONS):
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type. Expected one of {ALLOWED_EXTENSIONS}.",
        )

    file_bytes = await upload_file.read()
    if not file_bytes:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")
    if len(file_bytes) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File too large (max {MAX_UPLOAD_BYTES // (1024*1024)}MB).",
        )

    bbox_tuple = _parse_bbox(bbox)

    try:
        result = analyze(
            file_bytes,
            target_cells=target_cells,
            min_catchment_fraction=min_catchment_fraction,
            max_river_fraction=max_river_fraction,
            avoid_main_river=avoid_main_river,
            bbox=bbox_tuple,
            max_depth_m=max_depth_m,
        )
    except ValueError as e:
        logger.warning("Bad contour input: %s", e)
        raise HTTPException(status_code=422, detail=str(e))
    except Exception:
        logger.exception("Unexpected failure analyzing contour map")
        raise HTTPException(
            status_code=500, detail="Internal error while analyzing contour map."
        )

    return JSONResponse(
        {
            "pond_location": result.pond_location,
            "catchment": result.catchment,
            "water_volume": result.water_volume,
            "dem_summary": result.dem_summary,
            "input_summary": result.input_summary,
            "warnings": result.warnings,
        }
    )
