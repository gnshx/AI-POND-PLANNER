"""
main.py
-------
FastAPI app exposing the pond catchment analysis backend.

POST /analyzeContour
    multipart/form-data upload of a .kml or .kmz contour map.
    Optional query params:
      target_cells (int)             - DEM grid resolution knob (default 250000)
      min_catchment_fraction (float) - minimum candidate-basin size as a
                                        fraction of DEM extent (default 0.01)

Run locally:
    uvicorn app.main:app --host 0.0.0.0 --port 4000

Memory footprint is intentionally bounded (see app/dem.py) to run
comfortably on small (~2GB RAM) hosts; see README.md for deployment notes.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import JSONResponse

from .catchment import analyze
from .dem import DEFAULT_TARGET_CELLS, MAX_TARGET_CELLS, MIN_TARGET_CELLS

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("pond_catchment")

app = FastAPI(
    title="Pond Catchment Analysis API",
    description=(
        "Accepts a contour map (KML/KMZ), builds a DEM, runs D8 flow "
        "routing, and returns a suitable pond location with its "
        "estimated catchment area."
    ),
    version="0.2.0",
)

ALLOWED_EXTENSIONS = (".kml", ".kmz")
MAX_UPLOAD_BYTES = 100 * 1024 * 1024  # 100MB - generous for contour KML/KMZ


@app.get("/")
def root():
    return {
        "service": "pond-catchment-analysis",
        "status": "ok",
        "endpoints": {
            "POST /analyzeContour": "Upload a KML/KMZ contour map for analysis",
            "GET /health": "Liveness check",
            "GET /docs": "Interactive API documentation (Swagger UI)",
        },
    }


@app.get("/health")
def health():
    return {"status": "ok"}


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

    try:
        result = analyze(
            file_bytes,
            target_cells=target_cells,
            min_catchment_fraction=min_catchment_fraction,
            max_river_fraction=max_river_fraction,
            avoid_main_river=avoid_main_river,
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
            "dem_summary": result.dem_summary,
            "input_summary": result.input_summary,
            "warnings": result.warnings,
        }
    )
