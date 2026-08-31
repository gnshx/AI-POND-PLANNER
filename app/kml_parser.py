"""
kml_parser.py
-------------
Parses a contour map supplied as KML or KMZ and extracts a scattered
point cloud of (longitude, latitude, elevation) triples.

Design notes (generalisation to Phase 3):
- Contour generators (gdal_contour, QGIS, custom scripts, etc.) place the
  elevation value in different spots: most commonly the <name> of the
  Placemark (as in the sample file), sometimes inside <ExtendedData>, and
  occasionally in <description>. We try several strategies in order and
  fall back gracefully instead of hard failing, so a differently-produced
  KML from Phase 3 still has a decent chance of parsing correctly.
- We deliberately do NOT assume a specific coordinate range, elevation
  range, or number of contour levels - all of that is derived at runtime.
"""

from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass
from typing import BinaryIO

import numpy as np
from lxml import etree

KML_NS = "http://www.opengis.net/kml/2.2"
NSMAP = {"kml": KML_NS}

_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")


@dataclass
class ContourPointCloud:
    lons: np.ndarray  # (N,) float64, degrees
    lats: np.ndarray  # (N,) float64, degrees
    elevations: np.ndarray  # (N,) float64, same unit as source KML (assumed metres)
    n_lines: int
    elevation_levels: list[float]

    @property
    def bounds(self):
        return (
            float(self.lons.min()),
            float(self.lats.min()),
            float(self.lons.max()),
            float(self.lats.max()),
        )


def _load_kml_bytes(file_bytes: bytes) -> bytes:
    """Return raw KML bytes, transparently un-zipping KMZ if needed."""
    if file_bytes[:2] == b"PK":  # KMZ is a zip archive
        with zipfile.ZipFile(io.BytesIO(file_bytes)) as zf:
            # KMZ spec: the main doc is usually doc.kml, but be lenient
            kml_names = [n for n in zf.namelist() if n.lower().endswith(".kml")]
            if not kml_names:
                raise ValueError("KMZ archive does not contain a .kml file")
            preferred = [n for n in kml_names if n.lower() == "doc.kml"]
            target = preferred[0] if preferred else kml_names[0]
            return zf.read(target)
    return file_bytes


def _extract_elevation(placemark: etree._Element) -> float | None:
    """Try several strategies to recover the contour elevation value."""
    # Strategy 1: <name> is a plain number (matches the sample file)
    name_el = placemark.find("kml:name", NSMAP)
    if name_el is not None and name_el.text:
        text = name_el.text.strip()
        try:
            return float(text)
        except ValueError:
            m = _NUM_RE.search(text)
            if m:
                return float(m.group())

    # Strategy 2: ExtendedData / SchemaData SimpleData with an elevation-like key
    for sd in placemark.findall(".//kml:SimpleData", NSMAP):
        key = (sd.get("name") or "").lower()
        if any(k in key for k in ("elev", "height", "contour", "z")):
            if sd.text:
                try:
                    return float(sd.text.strip())
                except ValueError:
                    continue

    # Strategy 3: <description> containing a number
    desc_el = placemark.find("kml:description", NSMAP)
    if desc_el is not None and desc_el.text:
        m = _NUM_RE.search(desc_el.text)
        if m:
            return float(m.group())

    return None


def parse_contours(file_bytes: bytes) -> ContourPointCloud:
    """
    Parse a KML/KMZ contour map into a flat point cloud.

    Every vertex of every contour LineString becomes one sample point,
    tagged with the elevation of the contour it belongs to. This is a
    simple, robust way to turn a contour map into scattered 3D data that
    can be interpolated onto a regular elevation grid.
    """
    kml_bytes = _load_kml_bytes(file_bytes)
    parser = etree.XMLParser(recover=True, huge_tree=True)
    try:
        root = etree.fromstring(kml_bytes, parser=parser)
    except etree.XMLSyntaxError as e:
        raise ValueError(f"Could not parse file as XML/KML: {e}")
    if root is None:
        raise ValueError("Could not parse file as XML/KML: no content recovered.")

    lons: list[float] = []
    lats: list[float] = []
    elevs: list[float] = []
    n_lines = 0
    levels: set[float] = set()

    placemarks = root.findall(".//kml:Placemark", NSMAP)
    for pm in placemarks:
        elevation = _extract_elevation(pm)
        if elevation is None:
            continue

        line_strings = pm.findall(".//kml:LineString", NSMAP)
        if not line_strings:
            # Some contour exports use MultiGeometry -> LineString already
            # covered by the .// search above; if truly absent, skip.
            continue

        for ls in line_strings:
            coords_el = ls.find("kml:coordinates", NSMAP)
            if coords_el is None or not coords_el.text:
                continue
            n_lines += 1
            levels.add(elevation)
            for token in coords_el.text.split():
                parts = token.split(",")
                if len(parts) < 2:
                    continue
                lon, lat = float(parts[0]), float(parts[1])
                lons.append(lon)
                lats.append(lat)
                elevs.append(elevation)

    if not lons:
        raise ValueError(
            "No usable contour coordinates found in the uploaded file. "
            "Expected Placemark/LineString elements with an elevation "
            "value in <name>, <ExtendedData>, or <description>."
        )

    return ContourPointCloud(
        lons=np.asarray(lons, dtype=np.float64),
        lats=np.asarray(lats, dtype=np.float64),
        elevations=np.asarray(elevs, dtype=np.float64),
        n_lines=n_lines,
        elevation_levels=sorted(levels),
    )
