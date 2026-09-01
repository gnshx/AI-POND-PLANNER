# 📑 Assignment 1 - Phase 2: Pond Catchment Analysis Report

**Course:** Computer Systems Design (CS559) — Phase 2 Assignment  

---

## 1. Project Information & Links

- **GitHub Repository Link:** [https://github.com/gnshx/AI-POND-PLANNER](https://github.com/gnshx/AI-POND-PLANNER)
- **Working API Route URL:** `http://10.1.75.51:4310/analyzeContour`  
  *(Alternative Alias Route: `http://10.1.75.51:4310/findCatchment`)*
- **Swagger Documentation:** `http://10.1.75.51:4310/docs`

---

## 2. Catchment Estimation Approach

The backend implements a pure Python hydrological pipeline built on `numpy` and `scipy` without hardcoding any spatial boundaries or coordinates.

### Step-by-Step Methodology:

```
┌─────────────────┐
│ KML/KMZ Upload  │  Extract contour vertices (lon, lat, elevation) from <name>,
└────────┬────────┘  ExtendedData, or 3D coordinate strings.
         ▼
┌─────────────────┐  Project coordinates to local metres (equirectangular), interpolate
│  DEM Build      │  onto a regular grid via Delaunay Triangulation + linear interpolation,
└────────┬────────┘  and apply light Gaussian smoothing.
         ▼
┌─────────────────┐  Calculate D8 steepest-descent flow direction vectors for all cells,
│ Flow Routing    │  followed by topological flow accumulation computation.
└────────┬────────┘
         ▼
┌─────────────────┐  Identify candidate interior sinks (depressions) while filtering out
│  Pond Siting    │  main river channels using accumulation thresholds (off-stream
└────────┬────────┘  tributary / sub-catchment selection).
         ▼
┌─────────────────┐  Reverse flow-direction graph and perform Breadth-First Search (BFS)
│ Catchment Boundary│ from the chosen pour point to delineate all draining cells and trace
└─────────────────┘  the GeoJSON boundary polygon.
```

### Key Technical Highlights & Extensibility:
1. **No Hard-coded Parameters:** Extent, grid resolution, elevation levels, and cell sizes are calculated dynamically from the uploaded KML.
2. **Main River Avoidance:** Prevents placing ponds directly on high-velocity main river trunks by using an accumulation threshold (`max_river_fraction`), selecting optimal off-stream farm basins instead.
3. **Phase 3 Extensibility:** The memory profile is bounded (default 250,000 target cells) to run within **~150MB RAM**, allowing it to process much larger contour maps or alternative GIS data formats in future phases.

---

## 3. API Documentation

### Endpoint Definition
- **HTTP Method:** `POST`
- **Route URLs:** `/analyzeContour` or `/findCatchment`
- **Content-Type:** `multipart/form-data`

### Request Parameters

| Parameter | Location | Type | Required | Description |
| :--- | :--- | :--- | :---: | :--- |
| `contour_map` | Form-Data | File | **Yes** | `.kml` or `.kmz` contour map file (accepts `file` as fallback) |
| `target_cells` | Query | Integer | No | DEM grid cell target (default: `250000`, range: `10000–600000`) |
| `min_catchment_fraction` | Query | Float | No | Minimum candidate basin size threshold (default: `0.0001`) |
| `max_river_fraction` | Query | Float | No | Max accumulation threshold before a channel is considered a main river (default: `0.0015`) |
| `avoid_main_river` | Query | Boolean | No | Avoid main river channels (default: `true`) |

### Example cURL Request
```bash
curl -X POST "http://10.1.75.51:4310/analyzeContour" \
  -F "contour_map=@contours_1m.kml"
```

### Sample API Response (`200 OK`)
```json
{
  "pond_location": {
    "longitude": 81.2886285,
    "latitude": 21.2526051,
    "elevation_m": 269.01,
    "selection_method": "Off-stream interior topographic sink / sub-catchment pour point with optimal contributing area, avoiding main river channels."
  },
  "catchment": {
    "area_m2": 49610.0,
    "area_hectares": 4.961,
    "cell_count": 1450,
    "cell_size_m": 5.849,
    "elevation_range_m": [269.01, 287.99],
    "relief_m": 18.98,
    "boundary_polygon": {
      "type": "Polygon",
      "coordinates": [
        [ [81.2886285, 21.2526051], "... closed ring ..." ]
      ]
    }
  },
  "dem_summary": {
    "grid_rows": 453,
    "grid_cols": 555,
    "cell_size_m": 5.849,
    "elevation_min_m": 267.0,
    "elevation_max_m": 297.89
  },
  "input_summary": {
    "contour_lines": 1355,
    "contour_vertices": 159113,
    "elevation_levels": [267.0, 268.0, 269.0, "..."],
    "bounds": {
      "min_lon": 81.2814045,
      "min_lat": 21.2398224,
      "max_lon": 81.3126469,
      "max_lat": 21.2635806
    }
  },
  "warnings": []
}
```

---

## 4. Demonstration using `contours_1m.kml`

When evaluated against the provided `contours_1m.kml` sample map:

1. **Input Summary:**
   - Total Contour Lines: **1,355**
   - Total Vertices Processed: **159,113**
   - Elevation Range: **267.0 m to 298.0 m**
2. **Pond Location:**
   - **Longitude:** `81.2886285`
   - **Latitude:** `21.2526051`
   - **Elevation:** `269.01 m`
3. **Estimated Catchment:**
   - **Area ($m^2$):** `49,610.0 m²` (**4.961 Hectares**)
   - **Cell Count:** `1,450 cells`
   - **Total Relief:** `18.98 m`

### Visualization Diagram
The pipeline generates a 3-panel verification figure (`docs/demo_output.png`):
1. **Interpolated DEM (m):** Topographic elevation surface.
2. **Flow Accumulation (log scale):** Downstream drainage channels.
3. **Catchment Mask & Pond Site:** Delineated catchment area highlighted in red with the optimal pond pour point marked.
