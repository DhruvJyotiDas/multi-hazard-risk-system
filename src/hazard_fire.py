"""Fire hazard layer: MODIS/VIIRS active-fire density weighted by fuel cover.

Purpose : map recurrent dry-season (Jan-May) forest-fire exposure in Wayanad's
          deciduous forest tracts and wildlife sanctuaries.
Method  : (1) count MODIS (FIRMS, Collection 6.1) and VIIRS (VNP14A1) active-
          fire detections of the last 10 years inside the dry-season window,
          keeping only detections above a confidence threshold; (2) turn the
          1 km detection counts into a smooth ignition-frequency surface with
          a Gaussian kernel density estimate; (3) weight the density by fuel
          availability = Dynamic World probability of tree/grass/shrub/crop
          cover (a floor keeps density from vanishing over sparse fuel);
          (4) min-max normalise to 0-1.
References:
  * Adab, Kanniah & Solaimani 2013, *Natural Hazards* 65 -- modelling forest
    fire risk with MODIS hotspots validated against fire records.
  * Parajuli et al. 2020 -- GIS/remote-sensing forest fire risk assessment
    using active-fire data and MCDM.
  * Giglio et al. 2016, *Remote Sensing of Environment* 178 -- MODIS C6
    active-fire product.
"""
from __future__ import annotations

import logging
from datetime import date

import ee

import config
from src.utils import collection_size, dynamic_world_probabilities, finalize_layer, normalize_minmax

log = logging.getLogger(__name__)


def _date_window() -> tuple[str, str]:
    """(start, end) ISO dates covering FIRE_YEARS_BACK years up to FIRE_END_DATE."""
    end = date.fromisoformat(config.FIRE_END_DATE)
    start = end.replace(year=end.year - config.FIRE_YEARS_BACK)
    return start.isoformat(), end.isoformat()


def _detection_count(aoi: ee.Geometry, start: str, end: str) -> tuple[ee.Image, dict]:
    """Per-pixel number of dry-season active-fire detections (MODIS + optional VIIRS)."""
    m0, m1 = config.FIRE_DRY_SEASON_MONTHS
    region = aoi.buffer(config.FIRE_KERNEL_RADIUS_M * 2)     # include fires just outside the AOI edge
    month_filter = ee.Filter.calendarRange(m0, m1, "month")

    modis = (ee.ImageCollection(config.DS_FIRMS).filterBounds(region).filterDate(start, end).filter(month_filter))
    n_modis = collection_size(modis)
    detections = {"modis_days": n_modis}
    layers = []
    if n_modis:
        layers.append(modis.select("confidence").map(
            lambda i: i.clip(region).gte(config.FIRE_MIN_CONFIDENCE).unmask(0).clip(region).rename("fires")).sum())
    else:
        log.warning("No MODIS FIRMS images found in %s..%s", start, end)

    if config.USE_VIIRS:
        viirs = (ee.ImageCollection(config.DS_VIIRS).filterBounds(region).filterDate(start, end).filter(month_filter))
        n_viirs = collection_size(viirs)
        detections["viirs_days"] = n_viirs
        if n_viirs:
            layers.append(viirs.select("FireMask").map(
                lambda i: i.clip(region).gte(config.VIIRS_MIN_FIREMASK).unmask(0).clip(region).rename("fires")).sum())
        else:
            log.warning("No VIIRS VNP14A1 images found in %s..%s", start, end)

    if not layers:
        raise RuntimeError("No MODIS or VIIRS active-fire data available for the requested period")
    count = ee.ImageCollection.fromImages(layers).sum().rename("fires")
    return count, detections


def compute_fire_layer(aoi: ee.Geometry) -> dict:
    """Build the normalised 0-1 fire hazard layer (band 'fire')."""
    start, end = _date_window()
    log.info("Fire layer: MODIS%s active fires %s..%s, months %s-%s, kernel radius %d m",
             "/VIIRS" if config.USE_VIIRS else "", start, end, *config.FIRE_DRY_SEASON_MONTHS,
             config.FIRE_KERNEL_RADIUS_M)
    count, detections = _detection_count(aoi, start, end)

    # Kernel density estimate: Gaussian smoothing of the detection counts on the native 1 km grid
    # note: each daily image is clipped to the region *before* summing (see _detection_count); reprojecting
    # an unbounded sinusoidal-grid image to UTM fails with "Can't transform (0.0,0.0)"
    count_1km = count.reproject(crs=config.ANALYSIS_CRS, scale=config.FIRE_NATIVE_SCALE)
    kernel = ee.Kernel.gaussian(radius=config.FIRE_KERNEL_RADIUS_M, sigma=config.FIRE_KERNEL_SIGMA_M,
                                units="meters", normalize=True)
    density = count_1km.convolve(kernel).resample("bilinear").rename("fire_density")

    # Fuel weighting from Dynamic World vegetation probabilities
    dw = dynamic_world_probabilities(aoi)
    fuel = dw.select(list(config.FIRE_VEG_CLASSES)).reduce(ee.Reducer.sum()).clamp(0, 1)
    fuel_w = fuel.multiply(1 - config.FIRE_VEG_FLOOR).add(config.FIRE_VEG_FLOOR)

    density_n = normalize_minmax(finalize_layer(density.clip(aoi), aoi, "fire_density"), aoi, "fire_density")
    raw = density_n.multiply(fuel_w).rename("fire_raw")
    raw = finalize_layer(raw, aoi, "fire_raw")
    image = normalize_minmax(raw, aoi, "fire_raw", "fire")
    log.info("Fire layer computed (%s)", detections)
    return {"image": image, "detections": detections}
