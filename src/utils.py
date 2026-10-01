"""Shared helpers: logging, min-max normalisation, FR class rating, EE safety.

Purpose : keep the hazard modules short and consistent.
Method  : min-max rescaling of every layer to 0-1 inside the study area
          (METHODS section 5 of the project brief); piecewise-constant rating
          of continuous factors into literature-derived classes.
Reference: min-max normalisation as used in GIS-MCDM susceptibility mapping,
          Rehman et al. 2022, *Remote Sensing* 14 -- FR-AHP-GIS multi-hazard.
"""
from __future__ import annotations

import logging
import sys

import ee

import config


def setup_logging(level: int = logging.INFO) -> None:
    """Configure console logging once."""
    root = logging.getLogger()
    if root.handlers:
        root.setLevel(level)
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)-7s | %(name)s | %(message)s", "%H:%M:%S"))
    root.addHandler(handler)
    root.setLevel(level)


def collection_size(col: ee.ImageCollection) -> int:
    """Number of images in a collection (0 on failure) -- guards empty collections."""
    try:
        return int(col.size().getInfo())
    except ee.EEException:
        return 0


def minmax_stats(img: ee.Image, aoi: ee.Geometry, band: str) -> tuple[float, float]:
    """Lower / upper bound of ``band`` inside the AOI at config.STATS_SCALE.

    Lower = minimum. Upper = maximum, or the ``config.NORMALIZE_UPPER_PERCENTILE`` percentile when set
    (winsorised min-max: a handful of extreme pixels would otherwise squash the whole layer towards 0).
    """
    pct = config.NORMALIZE_UPPER_PERCENTILE
    reducer = ee.Reducer.minMax()
    if pct is not None:
        reducer = reducer.combine(ee.Reducer.percentile([pct]), sharedInputs=True)
    stats = img.select(band).reduceRegion(
        reducer=reducer, geometry=aoi, scale=config.STATS_SCALE,
        maxPixels=config.EE_MAX_PIXELS, bestEffort=True, tileScale=config.EE_TILE_SCALE,
    ).getInfo()
    lo, hi = stats.get(f"{band}_min"), stats.get(f"{band}_max")
    if lo is None or hi is None:
        raise ValueError(f"Band '{band}' has no valid pixels inside the study area")
    if pct is not None:
        p_hi = stats.get(f"{band}_p{int(pct)}")
        if p_hi is not None and p_hi > lo:          # sparse layers (e.g. >99 % zeros) fall back to the true max
            hi = p_hi
    return float(lo), float(hi)


def normalize_minmax(img: ee.Image, aoi: ee.Geometry, band: str, name: str | None = None,
                     bounds: tuple[float, float] | None = None) -> ee.Image:
    """Rescale ``band`` to 0-1 with min-max statistics computed inside the AOI (values above the
    upper bound are clamped to 1). A degenerate (constant) layer becomes all zeros.
    ``bounds`` reuses previously computed (lo, hi), e.g. when resuming from a checkpoint."""
    lo, hi = bounds if bounds is not None else minmax_stats(img, aoi, band)
    span = hi - lo
    out = img.select(band).subtract(lo).divide(span) if span > 0 else img.select(band).multiply(0)
    return out.clamp(0, 1).rename(name or band).clip(aoi)


def rate_classes(img: ee.Image, edges: tuple, ratings: tuple) -> ee.Image:
    """Assign the FR rating of the class each pixel falls in.

    ``edges`` are ascending class lower-bounds; ``ratings`` has len(edges)+1
    values (the first applies below edges[0]).
    """
    if len(ratings) != len(edges) + 1:
        raise ValueError("ratings must have exactly one more element than edges")
    rated = ee.Image.constant(ratings[0])
    for edge, rating in zip(edges, ratings[1:]):
        rated = rated.where(img.gte(edge), rating)
    return rated.updateMask(img.mask())


def srtm_slope_elevation(aoi: ee.Geometry) -> tuple[ee.Image, ee.Image]:
    """Return (slope in degrees, elevation in m) from SRTM, clipped to the AOI."""
    dem = ee.Image(config.DS_SRTM).select("elevation")
    slope = ee.Terrain.slope(dem).rename("slope")
    return slope.clip(aoi), dem.rename("elevation").clip(aoi)


def finalize_layer(img: ee.Image, aoi: ee.Geometry, name: str) -> ee.Image:
    """Fix a layer on the analysis grid (UTM 43N, 30 m) so downstream maths is scale-consistent."""
    return (img.rename(name)
            .reproject(crs=config.ANALYSIS_CRS, scale=config.ANALYSIS_SCALE)
            .clip(aoi))


def dynamic_world_probabilities(aoi: ee.Geometry) -> ee.Image:
    """Mean Dynamic World class-probability image (bands water..snow_and_ice) over config.DW_DATE_RANGE.

    Uses the pre-event window so built-area/vegetation proxies are not affected by
    post-landslide scars. Raises if the collection is empty.
    """
    start, end = config.DW_DATE_RANGE
    col = ee.ImageCollection(config.DS_DW).filterBounds(aoi).filterDate(start, end)
    n = collection_size(col)
    if n == 0:
        raise RuntimeError(f"No Dynamic World scenes in {start}..{end} for the study area")
    probs = col.select(["water", "trees", "grass", "flooded_vegetation", "crops",
                        "shrub_and_scrub", "built", "bare", "snow_and_ice"]).mean()
    return probs.clip(aoi)
