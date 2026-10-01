# Project review and 3D frontend plan

## Current architecture

This is a Python batch geospatial decision-support system for Wayanad, Kerala. It has no web API, JavaScript build system or persistent application service. `config.py` contains analysis dates, datasets, AOI, weights, routing parameters and output paths. `src/pipeline.py` orchestrates the workflow; `src/webmap.py` builds an embedded-data Folium/Leaflet page and supplies the CLI.

| Stage | Modules | Behaviour |
| --- | --- | --- |
| Area and authentication | `gee_init.py` | Earth Engine project/authentication; OSM district geometry with fallback bounds |
| Flood | `hazard_flood.py` | Sentinel-1 VV Otsu water extraction, JRC permanent-water and slope masks; event frequency |
| Landslide | `hazard_landslide.py` | Literature-derived factor ratings for SRTM slope/elevation, Sentinel-2 NDVI and CHIRPS rainfall; 2024 event-zone check |
| Fire | `hazard_fire.py` | MODIS/VIIRS fire density and Dynamic World fuel weighting |
| Exposure | `exposure.py` | Dynamic World built-area probability and road proximity |
| Risk fusion | `mcdm.py` | AHP consistency check, entropy and CRITIC comparison, combined weighting, quantile/natural-break classes and area statistics |
| Raster bridge | `rasters.py` | Common-grid Earth Engine exports, NumPy arrays and GeoTIFF persistence |
| Hotspots | `hotspots.py` | Top-class polygons, area and built-area summaries |
| Road graph | `osm_pbf.py`, `routing.py` | PBF/Overpass roads and POIs, raster edge sampling, settlement origins and dual Dijkstra routes |
| Delivery | `webmap.py` | Folium basemaps, embedded raster overlays/click values, toggles, statistics, route comparisons and validation |

`tests/test_core.py` covers numerical weighting, Otsu, hotspot extraction, risk-aware route trade-offs and standalone map generation. `demo.ipynb` provides notebook orchestration. The root `Master_OneShot_Prompt.md` describes the original requested system rather than a separate runtime component.

## Observed saved state

At review time, `outputs/` contains risk statistics, MCDM weights, the checkpoint, a passing landslide validation, cached AOI/road graph/PBF and one intermediate flood raster. Final composite rasters, `summary.json`, route outputs and the generated map are absent. The latest run log records a connection reset during landslide raster export; it does not establish that the whole pipeline completed.

The saved classified area totals 2,129.802 km²; High + Very High totals 886.037 km², approximately 41.6%. Combined AHP + entropy weighting is about 55.4% flood, 15.5% landslide, 12.8% exposure and 16.2% fire. These are the actual saved combined weights, distinct from configured AHP target weights. The saved verified event-zone result passes with 68.11% High/Very High pixels. The brief's alternative event coordinate has a separate failed/outside-area result; the headline reflects the verified coordinate.

## Design direction and delivered draft

Build a research observatory around an exploratory 3D map, with layer controls to the left and interpretation to the right. Use dark forest surfaces, pale green accents and restrained warm hazard colours. Keep terrain readable and put data provenance close to results. The primary tasks are exploring a hazard, inspecting risk, understanding class distribution, validating the event, and comparing route distance against risk exposure.

`frontend/` implements the visual and interaction draft using plain HTML/CSS/JavaScript. A projected Canvas mesh provides actual rotation, depth ordering and terrain shading, with no external dependencies. `export_data.py` bundles real saved summary results into `data.js`; every procedural map element and route example is labelled illustrative. The existing scientific pipeline remains the source of analysis and routing.

## Plan for geographic integration

1. **Complete and verify pipeline outputs.** Resolve export failures using the existing resume path; verify all registered rasters, masks, route metrics, GeoJSON and summary. Preserve the event validation distinction and actual combined weights.
2. **Define an output contract.** Export a frontend manifest with analysis periods, run timestamp, CRS, bounds, nodata mask, class thresholds, summary, provenance and relative artifact paths. Add an SRTM elevation export on a known grid: the existing risk stack does not persist a DEM. Export hospital/shelter/origin locations explicitly alongside routes.
3. **Replace procedural geometry with geographic terrain.** Evaluate a WebGL geographic renderer such as MapLibre GL JS or CesiumJS against local DEM/raster hosting, terrain size and deployment needs. Verify the selected renderer's current documentation and licensing during implementation. Serve terrain in a supported elevation format; convert analytical rasters to map tiles while retaining quantitative source grids for inspection. Keep the research pipeline responsible for classification.
4. **Connect analysis outputs.** Load the true district boundary, rasters, hotspots, POIs and route geometries. Use their stored geographic coordinates; show real distance/mean risk/max risk and risk reduction. Keep the raster nodata mask and saved quantile breaks for accurate click results. Handle missing artifacts with an explicit unavailable state.
5. **Add computation only where needed.** Static output browsing needs no API. A Python API becomes useful for new routing requests, scenario parameters, cached job status and sampling larger rasters. Keep Earth Engine credentials server-side; do not expose them in the browser. Scenario weight changes must be labelled exploratory and trigger consistent reclassification and rerouting if presented as computed results.
6. **Verify release behaviour.** Compare displayed values against Python outputs, test no-data and incomplete runs, inspect geographic alignment, keyboard and touch interactions, responsive layouts, browser rendering and large raster performance. Benchmark before choosing tiling and caching limits.

## Scope of this draft

This is a frontend design and interaction artifact, not a completed geographic 3D integration. It can be opened offline and reviewed before choosing a frontend framework or renderer. The next implementation milestone is a georeferenced terrain viewer backed by a completed, versioned pipeline output manifest.
