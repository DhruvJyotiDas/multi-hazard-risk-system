"""End-to-end pipeline: Satellite Data -> Indicator/Model -> Recommendation.

Purpose : orchestrate every module in order and persist a run summary so the
          web map can be rebuilt offline (``--from-cache``).
Method  : Earth Engine init -> study area -> OSM network -> flood / landslide /
          fire / exposure layers -> landslide validation gate -> MCDM composite
          + classes + statistics -> raster export -> hotspots -> least-risk
          routing -> interactive map.
Reference: see the individual modules for method-level citations.
"""
from __future__ import annotations

import json
import logging

import geopandas as gpd

import config
from src import exposure, gee_init, hazard_fire, hazard_flood, hazard_landslide, hotspots, mcdm, rasters, routing
from src.utils import setup_logging

log = logging.getLogger(__name__)
SUMMARY_JSON = config.OUTPUT_DIR / "summary.json"


def _save_summary(summary: dict) -> None:
    config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    SUMMARY_JSON.write_text(json.dumps(summary, indent=2, default=float), encoding="utf-8")


def _load_summary() -> dict:
    if not SUMMARY_JSON.exists():
        raise FileNotFoundError(f"{SUMMARY_JSON} not found; run the pipeline once with Earth Engine first")
    return json.loads(SUMMARY_JSON.read_text(encoding="utf-8"))


def compute_layers(aoi, roads_fc) -> dict:
    """Compute the four hazard/exposure layers (all 0-1) plus validation inputs."""
    flood = hazard_flood.compute_flood_layer(aoi)
    landslide = hazard_landslide.compute_landslide_layer(aoi)
    fire = hazard_fire.compute_fire_layer(aoi)
    expo = exposure.compute_exposure_layer(aoi, roads_fc)
    return {"flood": flood, "landslide": landslide, "fire": fire, "exposure": expo}


def run(from_cache: bool = False, build_map: bool = True, live_tiles: bool = False, resume: bool = False) -> dict:
    """Run the whole workflow and return the artefacts (stack, summary, routing, hotspots).

    ``resume=True`` reuses the MCDM checkpoint, the saved validation result and any raster bands already
    downloaded, so a run that failed late (export, routing, map) does not repeat the slow statistics.
    """
    setup_logging()
    geom, simple, source = gee_init.study_area_geometry()
    study_km2 = gee_init.area_km2(geom)

    live_layers = None
    if from_cache:
        log.info("--from-cache: rebuilding from outputs/ without Earth Engine")
        stack = rasters.load_stack()
        summary = _load_summary()
        G = routing.load_graph(geom)
    else:
        gee_init.initialize()
        aoi, geom, source = gee_init.get_study_area()
        G = routing.load_graph(geom)
        try:
            roads_fc = exposure.roads_to_feature_collection(routing.graph_edges(G))
        except Exception as err:  # noqa: BLE001 - exposure degrades gracefully without road distance
            log.warning("Could not push roads to Earth Engine (%s); exposure will use built area only", err)
            roads_fc = None

        res = compute_layers(aoi, roads_fc)
        if resume and config.VALIDATION_JSON.exists():
            validation = json.loads(config.VALIDATION_JSON.read_text(encoding="utf-8"))
            log.info("Resume: landslide validation reloaded (headline %s)", "PASS" if validation["headline_passed"] else "FAIL")
        else:
            validation = hazard_landslide.validate_event_zone(aoi, res["landslide"])
        layers = {"flood": res["flood"]["image"], "landslide": res["landslide"]["image"],
                  "exposure": res["exposure"]["image"], "fire": res["fire"]["image"]}
        mc = mcdm.build_composite(layers, aoi, resume=resume)

        export_layers = dict(layers)
        export_layers["built"] = res["exposure"]["built"]
        if live_tiles:
            live_layers = {"Live: composite risk (EE tiles)": mc["risk"], "Live: flood (EE tiles)": layers["flood"],
                           "Live: landslide (EE tiles)": layers["landslide"], "Live: fire (EE tiles)": layers["fire"],
                           "Live: exposure (EE tiles)": layers["exposure"]}
        stack = rasters.export_rasters(export_layers, aoi, resume=resume)
        # composite computed client-side from the exported layers (same linear map as the EE composite)
        stack.arrays["risk"] = mcdm.risk_from_layers(stack.arrays, mc["weights"], mc["risk_bounds"])
        stack.breaks = mc["breaks"]
        stack.classes = rasters.classify_array(stack.arrays["risk"], mc["breaks"])
        rasters.save_stack(stack)

        summary = {
            "study_area": source, "study_area_km2": study_km2, "breaks": mc["breaks"],
            "stats": mc["stats"].to_dict(orient="records"),
            "weight_table": mc["weight_table"].to_dict(orient="index"),
            "ahp": {k: v for k, v in mc["ahp"].items() if k != "weights"},
            "validation": validation, "flood_thresholds": res["flood"]["thresholds"],
            "fire_detections": res["fire"]["detections"],
        }

    hs = hotspots.extract_hotspots(stack)
    summary["hotspots"] = hotspots.hotspot_summary(hs, summary["study_area_km2"])
    hotspots.save_hotspots(hs)

    route_result = routing.run_routing(G, stack, geom)
    summary["routes"] = route_result.metrics.to_dict(orient="records")
    _save_summary(summary)

    if build_map:
        from src import webmap
        webmap.build_map(stack, summary, route_result, hs, live_layers=live_layers)
    _print_acceptance(summary, route_result)
    return {"stack": stack, "summary": summary, "routing": route_result, "hotspots": hs}


def _print_acceptance(summary: dict, route_result: routing.RoutingResult) -> None:
    """Console self-check against the project's acceptance criteria."""
    val = summary.get("validation") or {}
    verdict = "PASS" if val.get("headline_passed") else "FAIL"
    n_pairs = len(route_result.metrics)
    log.info("=" * 78)
    log.info("ACCEPTANCE SUMMARY")
    log.info("  AHP CR = %.4f (< %.2f): %s", summary["ahp"]["cr"], config.AHP_MAX_CR,
             "OK" if summary["ahp"]["cr"] < config.AHP_MAX_CR else "FAIL")
    log.info("  risk_stats.csv: %s", "written" if config.RISK_STATS_CSV.exists() else "MISSING")
    log.info("  least-risk routes vs shortest: %d pairs (target >= 5): %s", n_pairs, "OK" if n_pairs >= 5 else "SHORT")
    log.info("  landslide validation gate (headline): %s", verdict if val else "n/a")
    log.info("  index.html: %s", "written" if config.INDEX_HTML.exists() else "MISSING")
    log.info("=" * 78)
