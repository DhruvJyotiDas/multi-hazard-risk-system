"""High-risk hotspot extraction and statistics.

Purpose : turn the top (Very High) risk class into mappable hotspot polygons
          with area statistics, and quantify how much built-up land they cover.
Method  : the Very High class raster (class 5) is vectorised (8-connected), tiny
          fragments below HOTSPOT_MIN_AREA_KM2 are dropped, polygons are
          simplified, and for each polygon area (km2), mean/max composite risk
          and built-area cover are computed from the co-registered rasters.
References:
  * Skilodimou et al. 2019 -- multi-hazard zoning with weighted
    overlay and top-class extraction.
  * Lyu & Yin 2023 -- interval-FAHP-GIS multi-hazard risk (upgrade path).
"""
from __future__ import annotations

import logging

import geopandas as gpd
import numpy as np
from rasterio import features
from shapely.geometry import shape

import config
from src.rasters import RasterStack

log = logging.getLogger(__name__)


def extract_hotspots(stack: RasterStack) -> gpd.GeoDataFrame:
    """Vectorise the Very High class into hotspot polygons (EPSG:4326) with statistics."""
    if stack.classes is None:
        raise ValueError("RasterStack has no class raster; classify the composite first")
    top = len(config.RISK_CLASS_NAMES)
    mask = (stack.classes == top).astype("uint8")
    if mask.sum() == 0:
        log.warning("No pixels in the top risk class -- no hotspots to extract")
        return gpd.GeoDataFrame({"hotspot_id": [], "area_km2": []}, geometry=[], crs="EPSG:4326")

    polys = [shape(g) for g, v in features.shapes(mask, mask=mask.astype(bool), transform=stack.transform,
                                                  connectivity=8) if v == 1]
    gdf = gpd.GeoDataFrame(geometry=polys, crs="EPSG:4326")
    utm = gdf.to_crs(config.ANALYSIS_CRS)
    utm["geometry"] = utm.geometry.simplify(config.HOTSPOT_SIMPLIFY_M).buffer(0)
    utm["area_km2"] = utm.geometry.area / 1e6
    utm = utm[utm["area_km2"] >= config.HOTSPOT_MIN_AREA_KM2].sort_values("area_km2", ascending=False)
    utm = utm.reset_index(drop=True)
    utm.insert(0, "hotspot_id", np.arange(1, len(utm) + 1))

    # per-polygon zonal statistics from the rasters
    gdf = utm.to_crs("EPSG:4326")
    labels = features.rasterize(((g, int(i)) for g, i in zip(gdf.geometry, gdf["hotspot_id"])),
                                out_shape=stack.shape, transform=stack.transform, fill=0, dtype="int32")
    risk = np.nan_to_num(stack.arrays["risk"], nan=0.0)
    built = np.nan_to_num(stack.arrays.get("built", np.zeros_like(risk)), nan=0.0)
    n = len(gdf) + 1
    flat = labels.ravel()
    cnt = np.bincount(flat, minlength=n).astype(float)
    cnt[cnt == 0] = np.nan
    gdf["mean_risk"] = (np.bincount(flat, weights=risk.ravel(), minlength=n)[1:] / cnt[1:]).round(4)
    max_risk = np.zeros(n)
    np.maximum.at(max_risk, flat, risk.ravel())
    gdf["max_risk"] = max_risk[1:].round(4)
    built_area = built.ravel() * np.broadcast_to(stack.pixel_area_km2(), stack.shape).ravel()
    gdf["built_area_km2"] = np.bincount(flat, weights=built_area, minlength=n)[1:].round(4)
    gdf["geometry"] = gdf.geometry.simplify(1e-5)
    return gdf


def hotspot_summary(gdf: gpd.GeoDataFrame, study_area_km2: float) -> dict:
    """Aggregate hotspot statistics; logged and returned for the web-map side panel."""
    if gdf.empty:
        return {"n_hotspots": 0, "total_area_km2": 0.0, "percent_of_study_area": 0.0, "built_area_km2": 0.0}
    total = float(gdf["area_km2"].sum())
    summary = {"n_hotspots": int(len(gdf)), "total_area_km2": round(total, 2),
               "percent_of_study_area": round(100 * total / study_area_km2, 2),
               "largest_km2": round(float(gdf["area_km2"].max()), 2),
               "built_area_km2": round(float(gdf["built_area_km2"].sum()), 2)}
    log.info("Hotspots: %d polygons, %.1f km2 (%.1f%% of study area), largest %.2f km2, built-up land inside %.2f km2",
             summary["n_hotspots"], summary["total_area_km2"], summary["percent_of_study_area"],
             summary["largest_km2"], summary["built_area_km2"])
    return summary


def save_hotspots(gdf: gpd.GeoDataFrame) -> None:
    """Write hotspot polygons to outputs/hotspots.geojson."""
    config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    if gdf.empty:
        config.HOTSPOTS_GEOJSON.write_text('{"type": "FeatureCollection", "features": []}', encoding="utf-8")
    else:
        gdf.to_file(config.HOTSPOTS_GEOJSON, driver="GeoJSON")
    log.info("Hotspot polygons saved to %s", config.HOTSPOTS_GEOJSON)
