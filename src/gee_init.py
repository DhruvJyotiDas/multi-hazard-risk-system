"""Earth Engine authentication/initialisation and study-area (AOI) retrieval.

Purpose : give every other module a single, failure-tolerant entry point to GEE
          and a consistent area of interest.
Method  : ``ee.Initialize`` with an explicit Cloud project; if credentials are
          missing and a terminal is attached, run the interactive
          ``ee.Authenticate`` flow once; otherwise raise ``GEEInitError`` with
          step-by-step instructions (never a bare stack trace). The AOI is the
          OSM administrative polygon of Wayanad (via OSMnx geocoding) with the
          configured bounding box as fallback.
Reference: Gorelick et al. 2017, *Remote Sensing of Environment* 202 -- Google
          Earth Engine: planetary-scale geospatial analysis.
"""
from __future__ import annotations

import json
import logging
import math
import sys

import ee
from shapely.geometry import box, mapping, shape
from shapely.geometry.base import BaseGeometry

import config

log = logging.getLogger(__name__)

AUTH_HELP = """
Google Earth Engine could not be initialised.

Fix it in three steps:
  1. Register a Google Cloud project for Earth Engine:
       https://code.earthengine.google.com/register
  2. Authenticate once on this machine:
       earthengine authenticate
     (or: python -c "import ee; ee.Authenticate()")
  3. Tell the code which project to use, then re-run:
       Windows PowerShell:  $env:GEE_PROJECT = "your-project-id"
       macOS / Linux:       export GEE_PROJECT=your-project-id
     (or set GEE_PROJECT in config.py)

Underlying error: {err}
"""


class GEEInitError(RuntimeError):
    """Raised when Earth Engine cannot be initialised; message holds fix-it steps."""


def initialize(project: str | None = None, interactive_auth: bool = True) -> None:
    """Initialise Earth Engine, authenticating interactively if allowed.

    Raises GEEInitError with clear instructions on failure.
    """
    project = project or config.GEE_PROJECT
    if not project:
        raise GEEInitError(AUTH_HELP.format(err="no Cloud project configured (GEE_PROJECT is empty)"))
    try:
        ee.Initialize(project=project)
        log.info("Earth Engine initialised (project=%s)", project)
        return
    except Exception as first_err:  # noqa: BLE001 - ee raises several exception types
        if interactive_auth and sys.stdin is not None and sys.stdin.isatty():
            log.warning("EE credentials not usable (%s); starting interactive authentication", first_err)
            try:
                ee.Authenticate()
                ee.Initialize(project=project)
                log.info("Earth Engine initialised after authentication (project=%s)", project)
                return
            except Exception as second_err:  # noqa: BLE001
                raise GEEInitError(AUTH_HELP.format(err=second_err)) from second_err
        raise GEEInitError(AUTH_HELP.format(err=first_err)) from first_err


def _geocode_aoi() -> BaseGeometry | None:
    """Return the OSM polygon for config.PLACE_NAME or None when unavailable."""
    if config.AOI_CACHE.exists():
        try:
            return shape(json.loads(config.AOI_CACHE.read_text(encoding="utf-8")))
        except (ValueError, KeyError):
            log.warning("AOI cache unreadable; re-geocoding")
    try:
        import osmnx as ox

        gdf = ox.geocode_to_gdf(config.PLACE_NAME)
        geom = gdf.geometry.union_all()
        if geom.is_empty or geom.geom_type not in ("Polygon", "MultiPolygon"):
            return None
        config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        config.AOI_CACHE.write_text(json.dumps(mapping(geom)), encoding="utf-8")
        return geom
    except Exception as err:  # noqa: BLE001 - network / Nominatim failures are expected
        log.warning("OSM geocoding of '%s' failed (%s); using fallback bounding box", config.PLACE_NAME, err)
        return None


def area_km2(geom: BaseGeometry) -> float:
    """Approximate planar area of a lon/lat geometry in km2 (cos-latitude scaling)."""
    lat = geom.centroid.y
    return geom.area * (111.32 ** 2) * math.cos(math.radians(lat))


def study_area_geometry() -> tuple[BaseGeometry, BaseGeometry, str]:
    """Return (full shapely geometry, simplified geometry for EE, source description) -- no EE call needed."""
    geom = _geocode_aoi()
    if geom is not None:
        source = f"OSM polygon: {config.PLACE_NAME}"
        simple = geom.simplify(0.001, preserve_topology=True)  # ~100 m: light enough to embed in EE requests
    else:
        geom = simple = box(*config.FALLBACK_BBOX)
        source = f"fallback bounding box {config.FALLBACK_BBOX}"
    log.info("Study area: %s (%.0f km2)", source, area_km2(geom))
    return geom, simple, source


def get_study_area() -> tuple[ee.Geometry, BaseGeometry, str]:
    """Return (ee.Geometry, shapely geometry, source description) for the AOI."""
    geom, simple, source = study_area_geometry()
    ee_geom = ee.Geometry(mapping(simple), proj="EPSG:4326", geodesic=False)
    return ee_geom, geom, source
