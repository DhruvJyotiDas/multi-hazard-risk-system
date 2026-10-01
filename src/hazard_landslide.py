"""Landslide susceptibility layer (Frequency-Ratio-style) + event validation.

Purpose : map rainfall-triggered landslide susceptibility in the Western Ghats
          escarpment of Wayanad and validate it against the documented
          30 July 2024 Mundakkai-Chooralmala disaster.
Method  : four conditioning factors -- SRTM slope, SRTM elevation, Sentinel-2
          NDVI (low vegetation = higher susceptibility) and the maximum
          SW-monsoon (Jun-Sep) CHIRPS seasonal rainfall -- are rated with
          literature-derived Frequency-Ratio class values (FR > 1 = class more
          landslide-prone than average) and summed with FR-AHP factor weights:

              LSI = sum_f  w_f * FR_f(class of factor f)

          The LSI is min-max normalised to 0-1. All optical inputs end before
          the July 2024 event so the validation is free of label leakage.
          Validation: the zone within EVENT_BUFFER_M of the documented event
          point must fall in the High / Very High (quantile) classes; the
          function prints PASS/FAIL and, on FAIL, names the dominating factor
          and a concrete calibration change.
References:
  * Wang et al. 2020, *Int. J. Environ. Res. Public Health* -- FR vs random
    forest landslide susceptibility.
  * Rehman et al. 2022, *Remote Sensing* -- FR-AHP-GIS multi-hazard mapping.
  * He et al. 2023, *Sensors* -- FR-RF hybrid as top performer.
  * Lee & Pradhan 2007, *Landslides* 4 -- frequency-ratio landslide mapping.
"""
from __future__ import annotations

import json
import logging

import ee

import config
from src.utils import collection_size, finalize_layer, normalize_minmax, rate_classes, srtm_slope_elevation

log = logging.getLogger(__name__)


def _mask_s2(img: ee.Image) -> ee.Image:
    """Mask cloud, shadow and cirrus using the SCL band of S2 SR."""
    scl = img.select("SCL")
    bad = scl.eq(3).Or(scl.eq(8)).Or(scl.eq(9)).Or(scl.eq(10)).Or(scl.eq(11))
    return img.updateMask(bad.Not())


def ndvi_composite(aoi: ee.Geometry) -> ee.Image:
    """Cloud-masked dry-season median NDVI from Sentinel-2 SR (pre-event window)."""
    start, end = config.NDVI_DATE_RANGE
    col = (ee.ImageCollection(config.DS_S2).filterBounds(aoi).filterDate(start, end)
           .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", config.S2_MAX_CLOUD_PROB)))
    n = collection_size(col)
    if n == 0:
        raise RuntimeError(f"No Sentinel-2 scenes in {start}..{end} for the study area")
    log.info("  NDVI composite from %d Sentinel-2 scenes (%s..%s)", n, start, end)
    ndvi = col.map(_mask_s2).map(lambda i: i.normalizedDifference(["B8", "B4"]).rename("ndvi")).median()
    # persistent cloud gaps: fill from the neighbourhood, then with a neutral value
    ndvi = ndvi.unmask(ndvi.focalMedian(config.NDVI_GAP_RADIUS_M, "circle", "meters")).unmask(config.NDVI_GAP_FILL)
    return ndvi.clip(aoi)


def monsoon_rainfall_max(aoi: ee.Geometry) -> ee.Image:
    """Maximum (over years) of the Jun-Sep CHIRPS seasonal rainfall total, in mm."""
    m0, m1 = config.MONSOON_MONTHS
    chirps = ee.ImageCollection(config.DS_CHIRPS).select("precipitation")

    def season_total(year):
        year = ee.Number(year)
        start = ee.Date.fromYMD(year, m0, 1)
        end = ee.Date.fromYMD(year, m1, 1).advance(1, "month")
        return chirps.filterDate(start, end).sum().set("year", year)

    seasons = ee.ImageCollection.fromImages(ee.List(list(config.RAIN_YEARS)).map(season_total))
    # CHIRPS is ~5.5 km; bilinear resampling avoids blocky artefacts at 30 m
    return seasons.max().resample("bilinear").rename("rainfall").clip(aoi)


def compute_landslide_layer(aoi: ee.Geometry) -> dict:
    """Build the normalised 0-1 landslide susceptibility layer.

    Returns ``image`` (band 'landslide'), ``contributions`` (weighted FR image
    per factor), ``raw`` (un-normalised LSI) and ``factors`` (input rasters).
    """
    log.info("Landslide layer: slope + elevation + NDVI + max JJAS rainfall (FR-style)")
    slope, elevation = srtm_slope_elevation(aoi)
    ndvi = ndvi_composite(aoi)
    rain = monsoon_rainfall_max(aoi)
    factors = {"slope": slope, "elevation": elevation, "ndvi": ndvi.rename("ndvi"), "rainfall": rain}

    contributions = {}
    for name, img in factors.items():
        spec = config.LANDSLIDE_FACTORS[name]
        fr = rate_classes(img, spec["edges"], spec["ratings"])
        contributions[name] = fr.multiply(config.LANDSLIDE_FACTOR_WEIGHTS[name]).rename(name)
    stack = ee.Image.cat(list(contributions.values()))
    raw = stack.reduce(ee.Reducer.sum()).rename("landslide_raw")

    raw = finalize_layer(raw, aoi, "landslide_raw")
    contrib_img = ee.Image.cat([finalize_layer(c, aoi, n) for n, c in contributions.items()])
    image = normalize_minmax(raw, aoi, "landslide_raw", "landslide")
    log.info("Landslide layer computed (factor weights: %s)", config.LANDSLIDE_FACTOR_WEIGHTS)
    return {"image": image, "contributions": contrib_img, "raw": raw, "factors": factors}


def quantile_class_image(img: ee.Image, aoi: ee.Geometry, band: str) -> tuple[ee.Image, list[float]]:
    """Reclassify ``band`` into 5 quantile classes (1..5) -- returns (class image, breaks)."""
    pct = [20, 40, 60, 80]
    res = img.select(band).reduceRegion(
        reducer=ee.Reducer.percentile(pct), geometry=aoi, scale=config.STATS_SCALE,
        maxPixels=config.EE_MAX_PIXELS, bestEffort=True, tileScale=config.EE_TILE_SCALE).getInfo()
    breaks = [float(res[f"{band}_p{p}"]) for p in pct]
    cls = ee.Image.constant(1)
    for b in breaks:
        cls = cls.add(img.select(band).gte(b))
    return cls.rename("class").clip(aoi), breaks


def _validate_point(label: str, lat: float, lon: float, class_img: ee.Image, index_img: ee.Image,
                    contrib_img: ee.Image, aoi_means: dict) -> dict:
    """Sample the susceptibility classes at and around one event point."""
    point = ee.Geometry.Point([lon, lat])
    zone = point.buffer(config.EVENT_BUFFER_M)
    kw = dict(geometry=zone, scale=config.ANALYSIS_SCALE, maxPixels=config.EE_MAX_PIXELS, bestEffort=True)
    hist = class_img.reduceRegion(ee.Reducer.frequencyHistogram(), **kw).get("class")
    mean_idx = index_img.reduceRegion(ee.Reducer.mean(), **kw).get("landslide")
    contrib = contrib_img.reduceRegion(ee.Reducer.mean(), **kw)
    pt_class = class_img.reduceRegion(ee.Reducer.first(), geometry=point, scale=config.ANALYSIS_SCALE).get("class")
    pt_index = index_img.reduceRegion(ee.Reducer.first(), geometry=point, scale=config.ANALYSIS_SCALE).get("landslide")
    out = ee.Dictionary({"hist": hist, "mean_idx": mean_idx, "contrib": contrib,
                         "pt_class": pt_class, "pt_index": pt_index}).getInfo()
    hist = {int(float(k)): float(v) for k, v in (out["hist"] or {}).items()}
    total = sum(hist.values())
    if total == 0:
        # the zone lies outside the study area (e.g. a mis-located coordinate): report, do not crash
        return {"label": label, "lat": lat, "lon": lon, "buffer_m": config.EVENT_BUFFER_M,
                "outside_study_area": True, "passed": False,
                "note": "zone has no valid pixels: the coordinate lies outside the study area"}
    frac = sum(v for k, v in hist.items() if k in config.VALIDATION_PASS_CLASSES) / total
    modal = max(hist, key=hist.get)
    contrib_zone = {k: float(v) for k, v in (out["contrib"] or {}).items() if v is not None}
    passed = modal in config.VALIDATION_PASS_CLASSES and frac >= config.VALIDATION_MIN_FRACTION
    return {
        "label": label, "lat": lat, "lon": lon, "buffer_m": config.EVENT_BUFFER_M,
        "point_class": None if out["pt_class"] is None else int(out["pt_class"]),
        "point_class_name": None if out["pt_class"] is None else config.RISK_CLASS_NAMES[int(out["pt_class"]) - 1],
        "point_index": out["pt_index"], "zone_mean_index": out["mean_idx"],
        "zone_class_histogram": {config.RISK_CLASS_NAMES[k - 1]: round(v / total, 4) for k, v in sorted(hist.items())},
        "zone_modal_class": config.RISK_CLASS_NAMES[modal - 1],
        "zone_fraction_high_or_very_high": round(frac, 4),
        "zone_factor_contributions": contrib_zone,
        "aoi_factor_contributions": aoi_means,
        "passed": bool(passed),
    }


def _diagnose(res: dict) -> str:
    """Explain a FAIL: dominating factor + one concrete calibration change."""
    zone, aoi = res["zone_factor_contributions"], res["aoi_factor_contributions"]
    dominant = max(zone, key=zone.get)
    ratio = {k: zone[k] / aoi[k] if aoi.get(k) else float("inf") for k in zone}
    weakest = min(ratio, key=ratio.get)
    new_w = max(config.LANDSLIDE_FACTOR_WEIGHTS[weakest] - 0.05, 0.05)
    return (f"Factor '{dominant}' dominated the zone score ({zone[dominant]:.3f}); factor '{weakest}' pulled it down "
            f"(zone/AOI contribution ratio {ratio[weakest]:.2f}). Calibration: lower "
            f"LANDSLIDE_FACTOR_WEIGHTS['{weakest}'] from {config.LANDSLIDE_FACTOR_WEIGHTS[weakest]:.2f} to {new_w:.2f} and "
            f"add the 0.05 to '{dominant}', or raise the FR ratings of '{weakest}' classes that cover the zone.")


def validate_event_zone(aoi: ee.Geometry, landslide: dict) -> dict:
    """Headline validation: is the July 2024 event zone High / Very High?

    Tests both the published event location (headline gate) and the
    coordinate given in the project brief. Prints PASS/FAIL with sampled class
    values and writes outputs/landslide_validation.json.
    """
    image, contrib_img = landslide["image"], landslide["contributions"]
    class_img, breaks = quantile_class_image(image, aoi, "landslide")
    aoi_means = contrib_img.reduceRegion(
        ee.Reducer.mean(), geometry=aoi, scale=config.STATS_SCALE, maxPixels=config.EE_MAX_PIXELS,
        bestEffort=True, tileScale=config.EE_TILE_SCALE).getInfo()
    aoi_means = {k: float(v) for k, v in aoi_means.items() if v is not None}

    points = [("verified", *config.EVENT_POINT_VERIFIED), ("brief", *config.EVENT_POINT_SPEC)]
    results = [_validate_point(lbl, lat, lon, class_img, image, contrib_img, aoi_means) for lbl, lat, lon in points]

    log.info("=" * 78)
    log.info("VALIDATION GATE -- %s (quantile breaks on LSI: %s)", config.EVENT_NAME, [round(b, 3) for b in breaks])
    for r in results:
        if r.get("outside_study_area"):
            log.info("[N/A ] %s point (%.4f N, %.4f E): %s", r["label"], r["lat"], r["lon"], r["note"])
            continue
        verdict = "PASS" if r["passed"] else "FAIL"
        log.info("[%s] %s point (%.4f N, %.4f E): point class=%s (%s), zone mean LSI=%.3f, modal zone class=%s, "
                 "%.0f%% of %d m zone in High/Very High  -> %s",
                 verdict, r["label"], r["lat"], r["lon"], r["point_class"], r["point_class_name"],
                 r["zone_mean_index"] or float("nan"), r["zone_modal_class"],
                 100 * r["zone_fraction_high_or_very_high"], r["buffer_m"], verdict)
        log.info("      zone class shares: %s", r["zone_class_histogram"])
        if not r["passed"]:
            r["diagnosis"] = _diagnose(r)
            log.info("      diagnosis: %s", r["diagnosis"])
    headline = next(r for r in results if r["label"] == "verified")
    log.info("HEADLINE VALIDATION (verified event location): %s", "PASS" if headline["passed"] else "FAIL")
    log.info("=" * 78)

    summary = {"event": config.EVENT_NAME, "quantile_breaks": breaks, "headline_passed": headline["passed"],
               "results": results}
    config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    config.VALIDATION_JSON.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary
