"""Flood hazard layer from Sentinel-1 SAR with automatic Otsu thresholding.

Purpose : map flood-prone ground in Wayanad's valley bottoms (2018/2019 Kerala
          floods, 2024 monsoon) in a form that is robust to monsoon cloud.
Design rationale: the Southwest monsoon (Jun-Sep) keeps Wayanad under thick
          cloud, so optical sensors are unreliable exactly when floods happen.
          C-band SAR (Sentinel-1) sees through cloud, and open water is a
          specular reflector with very low VV backscatter -- hence an SAR layer.
Method  : (1) per flood window, median VV composite (dB) + focal-median speckle
          filter; (2) Otsu threshold derived automatically from the VV
          histogram of the flood-plausible terrain; (3) water = VV < threshold;
          (4) remove permanent water (JRC occurrence > 50 %) and slope > 5 deg
          (radar-shadow false positives); (5) flood frequency = mean of the
          window masks; (6) blend with JRC non-permanent occurrence, smooth,
          and min-max normalise to a 0-1 hazard surface.
References:
  * Moharrami et al. 2021, *Environmental Monitoring and Assessment* --
    automatic flood detection with Sentinel-1 on Google Earth Engine.
  * Tran et al. 2022, *Remote Sensing* -- Otsu thresholding on Sentinel-1
    time series for flood mapping.
  * Otsu 1979, *IEEE Trans. Systems, Man, Cybernetics* 9(1) -- threshold
    selection from grey-level histograms.
"""
from __future__ import annotations

import logging

import ee
import numpy as np

import config
from src.utils import collection_size, finalize_layer, normalize_minmax, srtm_slope_elevation

log = logging.getLogger(__name__)


def otsu_threshold(counts: np.ndarray, means: np.ndarray) -> float:
    """Otsu threshold maximising between-class variance of a histogram.

    ``counts`` are bucket counts and ``means`` the bucket centre values.
    Returns the value of the last bucket of the lower (water) class.
    """
    counts = np.asarray(counts, dtype=float)
    means = np.asarray(means, dtype=float)
    total = counts.sum()
    if counts.size < 3 or total <= 0:
        raise ValueError("histogram is too small for Otsu thresholding")
    cum_n = np.cumsum(counts)
    cum_s = np.cumsum(counts * means)
    grand_mean = cum_s[-1] / total
    n_b = total - cum_n
    valid = (cum_n > 0) & (n_b > 0)
    mean_a = np.where(valid, cum_s / np.where(cum_n > 0, cum_n, 1), 0.0)
    mean_b = np.where(valid, (cum_s[-1] - cum_s) / np.where(n_b > 0, n_b, 1), 0.0)
    between = np.where(valid, cum_n * (mean_a - grand_mean) ** 2 + n_b * (mean_b - grand_mean) ** 2, -1.0)
    return float(means[int(np.argmax(between))])


def _s1_composite(aoi: ee.Geometry, start: str, end: str) -> tuple[ee.Image | None, int]:
    """Median VV (dB) composite for one window, or (None, 0) when no scenes exist."""
    col = (ee.ImageCollection(config.DS_S1)
           .filterBounds(aoi)
           .filterDate(start, end)
           .filter(ee.Filter.eq("instrumentMode", config.S1_INSTRUMENT_MODE))
           .filter(ee.Filter.listContains("transmitterReceiverPolarisation", config.S1_POLARISATION))
           .select(config.S1_POLARISATION))
    n = collection_size(col)
    if n == 0:
        return None, 0
    vv = col.median().focalMedian(config.S1_SPECKLE_RADIUS_M, "circle", "meters")
    return vv.rename("vv").clip(aoi), n


def _window_threshold(vv: ee.Image, hist_zone: ee.Image, aoi: ee.Geometry) -> float:
    """Automatic Otsu threshold (dB) from the VV histogram inside ``hist_zone``."""
    hist = (vv.updateMask(hist_zone).reduceRegion(
        reducer=ee.Reducer.histogram(config.OTSU_HISTOGRAM_BUCKETS, 0.1),
        geometry=aoi, scale=config.OTSU_HISTOGRAM_SCALE, maxPixels=config.EE_MAX_PIXELS,
        bestEffort=True, tileScale=config.EE_TILE_SCALE).get("vv")).getInfo()
    lo, hi = config.OTSU_VALID_RANGE_DB
    if not hist or not hist.get("histogram"):
        log.warning("Empty VV histogram; using fallback threshold %.1f dB", config.OTSU_FALLBACK_DB)
        return config.OTSU_FALLBACK_DB
    thr = otsu_threshold(np.array(hist["histogram"]), np.array(hist["bucketMeans"]))
    clipped = float(np.clip(thr, lo, hi))
    if clipped != thr:
        log.warning("Otsu threshold %.2f dB outside guard range [%.1f, %.1f]; clamped to %.2f", thr, lo, hi, clipped)
    return clipped


def compute_flood_layer(aoi: ee.Geometry) -> dict:
    """Build the normalised 0-1 flood hazard layer.

    Returns a dict with ``image`` (band 'flood'), ``thresholds`` per window and
    ``windows_used``.
    """
    log.info("Flood layer: Sentinel-1 %s, %d flood windows", config.S1_POLARISATION, len(config.FLOOD_EVENT_WINDOWS))
    slope, _ = srtm_slope_elevation(aoi)
    jrc_occ = ee.Image(config.DS_JRC).select("occurrence").unmask(0).clip(aoi)
    permanent = jrc_occ.gt(config.FLOOD_PERMANENT_WATER_OCCURRENCE)
    plausible = slope.lte(config.FLOOD_MAX_SLOPE_DEG).And(permanent.Not())
    # Otsu needs a bimodal histogram. Wayanad is mostly land, so the histogram is built only from the
    # low-slope neighbourhood of known surface water (JRC occurrence > 5 %), including permanent water
    # (Donchyts et al. 2016-style bimodal sampling around water bodies).
    hist_zone = (jrc_occ.gt(config.OTSU_WATER_ZONE_MIN_OCCURRENCE)
                 .focalMax(config.OTSU_WATER_ZONE_RADIUS_M, "circle", "meters")
                 .And(slope.lte(config.FLOOD_MAX_SLOPE_DEG)))

    masks, thresholds = [], []
    for start, end in config.FLOOD_EVENT_WINDOWS:
        vv, n_scenes = _s1_composite(aoi, start, end)
        if vv is None:
            log.warning("No Sentinel-1 scenes for %s..%s; window skipped", start, end)
            continue
        thr = _window_threshold(vv, hist_zone, aoi)
        log.info("  window %s..%s: %d scenes, Otsu threshold = %.2f dB", start, end, n_scenes, thr)
        water = vv.lt(thr)
        flood = water.And(plausible).rename("flood_mask")      # drops permanent water + slope > 5 deg
        masks.append(flood)
        thresholds.append({"window": f"{start}..{end}", "scenes": n_scenes, "otsu_db": round(thr, 3)})

    # JRC historical exposure: non-permanent surface-water occurrence (0..0.5)
    jrc_term = jrc_occ.updateMask(permanent.Not()).unmask(0).divide(100)
    if masks:
        freq = ee.ImageCollection.fromImages(masks).mean()
        sar_term = freq.focalMean(config.FLOOD_SMOOTH_RADIUS_M, "circle", "meters")
        jrc_smooth = jrc_term.focalMean(config.FLOOD_SMOOTH_RADIUS_M, "circle", "meters")
        raw = sar_term.multiply(config.FLOOD_WEIGHT_SAR).add(jrc_smooth.multiply(config.FLOOD_WEIGHT_JRC))
    else:
        log.warning("No usable Sentinel-1 window -- flood hazard falls back to JRC occurrence only")
        raw = jrc_term.focalMean(config.FLOOD_SMOOTH_RADIUS_M, "circle", "meters")

    layer = finalize_layer(raw.rename("flood_raw"), aoi, "flood_raw")
    image = normalize_minmax(layer, aoi, "flood_raw", "flood")
    log.info("Flood layer computed (%d/%d windows used)", len(masks), len(config.FLOOD_EVENT_WINDOWS))
    return {"image": image, "thresholds": thresholds, "windows_used": len(masks)}
