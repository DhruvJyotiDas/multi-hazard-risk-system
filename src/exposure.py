"""Exposure layer: Dynamic World built-area probability + proximity to roads.

Purpose : represent population / asset exposure. In Wayanad, settlements and
          plantations creep onto mid-slope and escarpment-foot zones, so built-
          area density (10 m Dynamic World) is the key exposure signal; road
          proximity adds accessibility-linked assets and people in transit.
Method  : exposure = w_b * smooth(built probability) + w_r * road proximity,
          where road proximity = 1 - min(d, d_max)/d_max and d is the Euclidean
          distance to the nearest OSM road (OSM edges are simplified and
          pushed to Earth Engine as a FeatureCollection). The result is
          min-max normalised to 0-1.
References:
  * Brown et al. 2022, *Scientific Data* 9 -- Dynamic World near-real-time
    10 m land-use/land-cover.
  * Rehman et al. 2022, *Remote Sensing* -- built-up/road proximity as
    exposure and vulnerability criteria in multi-hazard GIS-MCDM.
  * Boeing 2017, *Computers, Environment and Urban Systems* 65 -- OSMnx.
"""
from __future__ import annotations

import ast
import logging

import ee
import geopandas as gpd
from shapely.ops import linemerge, unary_union

import config
from src.utils import dynamic_world_probabilities, finalize_layer, normalize_minmax

log = logging.getLogger(__name__)


def highway_values(value) -> set[str]:
    """OSM 'highway' tag may be a string, a list, or the string form of a list (GraphML round-trip)."""
    if isinstance(value, str) and value.startswith("["):
        try:
            value = ast.literal_eval(value)
        except (ValueError, SyntaxError):
            pass
    if isinstance(value, (list, tuple, set)):
        return {str(v) for v in value}
    return {str(value)}


def _merged_simplified_lines(edges: gpd.GeoDataFrame, classes: tuple, tol: float) -> list[list[list[float]]]:
    """Drop two-way duplicates, merge segments into polylines, simplify (metres) -> lon/lat coordinate lists."""
    keep = edges[edges["highway"].apply(lambda v: bool(highway_values(v) & set(classes)))]
    if keep.empty:
        return []
    if {"u", "v"} <= set(keep.columns):                      # an undirected road appears once per direction
        pair = keep[["u", "v"]].apply(lambda r: tuple(sorted((r["u"], r["v"]))), axis=1)
        keep = keep[~pair.duplicated()]
    projected = keep.to_crs(config.ANALYSIS_CRS)
    merged = linemerge(unary_union(list(projected.geometry)))
    parts = list(merged.geoms) if hasattr(merged, "geoms") else [merged]
    simple = gpd.GeoSeries([p.simplify(tol) for p in parts], crs=config.ANALYSIS_CRS).to_crs("EPSG:4326")
    return [[list(c) for c in g.coords] for g in simple if g.geom_type == "LineString" and len(g.coords) >= 2]


def roads_to_feature_collection(edges: gpd.GeoDataFrame) -> ee.FeatureCollection:
    """Upload simplified OSM roads to Earth Engine as a FeatureCollection of MultiLineStrings.

    Segments are de-duplicated and merged into polylines, then simplified with a tolerance
    that doubles (at most ROAD_EE_MAX_SIMPLIFY_STEPS times); if the vertex count still exceeds
    config.ROAD_EE_MAX_VERTICES the least important road class is dropped and the process repeats.
    Both loops are bounded, so the upload always terminates with a request EE accepts.
    """
    classes = list(config.ROAD_EE_CLASSES)                    # ordered most -> least important
    while classes:
        tol = float(config.ROAD_EE_SIMPLIFY_M)
        for _ in range(config.ROAD_EE_MAX_SIMPLIFY_STEPS):
            lines = _merged_simplified_lines(edges, tuple(classes), tol)
            n_vertices = sum(len(ln) for ln in lines)
            if n_vertices <= config.ROAD_EE_MAX_VERTICES:
                break
            tol *= 2
        if lines and n_vertices <= config.ROAD_EE_MAX_VERTICES:
            break
        log.info("  roads: %d vertices with classes %s still exceeds the cap; dropping '%s'", n_vertices, classes,
                 classes[-1])
        classes.pop()
    if not lines:
        raise ValueError("No OSM edges match config.ROAD_EE_CLASSES")
    chunk = 400
    feats = [ee.Feature(ee.Geometry.MultiLineString(lines[i:i + chunk], proj="EPSG:4326", geodesic=False))
             for i in range(0, len(lines), chunk)]
    log.info("  roads uploaded to EE: %d polylines, %d vertices, classes %s (simplify %.0f m)", len(lines),
             n_vertices, classes, tol)
    return ee.FeatureCollection(feats)


def built_probability(aoi: ee.Geometry) -> ee.Image:
    """Mean Dynamic World built-area probability (0-1), band 'built'."""
    return dynamic_world_probabilities(aoi).select("built")


def compute_exposure_layer(aoi: ee.Geometry, roads: ee.FeatureCollection | None) -> dict:
    """Build the normalised 0-1 exposure layer (band 'exposure').

    ``roads`` may be None (e.g. OSM unavailable); exposure then uses built area only.
    Returns ``image`` and ``built`` (raw smoothed built probability, used to find settlements).
    """
    log.info("Exposure layer: Dynamic World built-area + distance to roads")
    built = built_probability(aoi)
    built_smooth = built.focalMean(config.EXPOSURE_BUILT_SMOOTH_M, "circle", "meters").rename("built_smooth")
    if roads is not None:
        d_max = config.EXPOSURE_ROAD_MAX_DIST_M
        dist = roads.distance(searchRadius=d_max, maxError=config.ANALYSIS_SCALE).unmask(d_max).min(d_max)
        proximity = ee.Image(d_max).subtract(dist).divide(d_max).rename("road_proximity")
        raw = built_smooth.multiply(config.EXPOSURE_BUILT_WEIGHT).add(proximity.multiply(config.EXPOSURE_ROAD_WEIGHT))
    else:
        log.warning("No road data -- exposure uses built-area probability only")
        raw = built_smooth
    raw = finalize_layer(raw.rename("exposure_raw"), aoi, "exposure_raw")
    image = normalize_minmax(raw, aoi, "exposure_raw", "exposure")
    built_layer = finalize_layer(built_smooth, aoi, "built")
    log.info("Exposure layer computed")
    return {"image": image, "built": built_layer}
