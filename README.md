# Multi-Hazard Risk & Emergency Routing — Wayanad District, Kerala

A Google Earth Engine decision-support system that turns satellite data into flood, landslide, fire and
exposure layers, fuses them with an AHP + entropy MCDM model into a composite risk index, extracts
high-risk hotspots, and computes **least-risk** (not merely shortest) emergency routes from the riskiest
settlements to the nearest hospital and shelter. Everything is delivered as a standalone interactive web map.

```
Satellite Data  →  Indicator / Model  →  Recommendation
Sentinel-1/2, SRTM,   flood · landslide · fire ·   hotspots + least-risk routes
CHIRPS, MODIS/VIIRS,  exposure → AHP+entropy       to hospitals / shelters
JRC, Dynamic World    composite risk index
```

## Why Wayanad?

Wayanad is a high-relief Western Ghats plateau and one of India's most multi-hazard-dense districts.

| Hazard | Context | Layer |
|---|---|---|
| **Landslide** (dominant) | July 2024 Mundakkai–Chooralmala disaster; 2019 slides; slopes often > 30°, deep weathered soil, extreme monsoon rainfall | SRTM slope/elevation + NDVI + CHIRPS monsoon max, Frequency-Ratio ratings |
| **Flood** | 2018 and 2019 Kerala floods; settlements in Kabini-tributary valley bottoms | Sentinel-1 SAR, Otsu threshold |
| **Fire** | Recurrent Feb–May forest fires in deciduous tracts and sanctuaries | 10-year MODIS/VIIRS active-fire density × fuel cover |
| **Exposure** | Settlements and plantations creeping onto mid-slope / escarpment-foot zones | Dynamic World built area + road proximity |

**Design rationale for SAR.** The southwest monsoon (Jun–Sep) keeps Wayanad under heavy cloud exactly when
floods occur, so optical sensors are unreliable in-season. Sentinel-1 C-band radar sees through cloud, and open
water gives very low VV backscatter — hence the flood layer is SAR-based. Optical data (Sentinel-2 NDVI,
Dynamic World) is used only from cloud-free dry-season windows that **end before the July 2024 event**, so the
landslide validation is free of label leakage (no landslide scars in the inputs).

## Quick start

### 3D frontend design draft

Open [`frontend/index.html`](frontend/index.html) in a browser for the interactive Wayanad Risk Observatory draft. It works offline and includes orbitable terrain, hazard controls, saved analysis summaries and illustrative route comparisons. Terrain and routes are explicitly demo data; the summary cards use saved project outputs. See [`frontend/README.md`](frontend/README.md) for controls and [`docs/frontend-plan.md`](docs/frontend-plan.md) for the project review and geographic integration plan.

```bash
git clone <this repo> && cd multi-hazard-risk-system
python -m venv .venv && source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

earthengine authenticate                                    # one-off browser login
export GEE_PROJECT=your-cloud-project-id                    # PowerShell: $env:GEE_PROJECT="your-cloud-project-id"

python -m src.webmap                                        # full pipeline → index.html
```

Register a Cloud project for Earth Engine at <https://code.earthengine.google.com/register> if you do not
have one. If authentication is missing the program prints these steps and exits cleanly — it never crashes silently.

Useful variants:

| Command | Effect |
|---|---|
| `python -m src.webmap --from-cache` | Rebuild map/routes from `outputs/` without Earth Engine |
| `python -m src.webmap --live-tiles` | Also add live GEE tile layers (their tokens expire in ~1 day) |
| `python -m pytest tests -q` | Offline unit tests (MCDM maths, Otsu, hotspots, routing, web map) |
| `jupyter notebook demo.ipynb` | Same pipeline, step by step, plus a geemap explorer |

First run downloads the OSM drive network (Overpass) and caches it in `outputs/cache/`; expect several minutes.
All raster data comes straight from the Earth Engine catalog — there are no manual downloads.

## What you get

| Output | Description |
|---|---|
| `index.html` | **Standalone interactive map** (see below) |
| `outputs/risk_stats.csv` | Area (km²) and % per risk class |
| `outputs/mcdm_weights.csv` | AHP, entropy, combined and CRITIC weights side by side |
| `outputs/landslide_validation.json` | July-2024 event-zone validation (PASS/FAIL + class shares) |
| `outputs/hotspots.geojson` | Very-High-risk hotspot polygons with area, mean/max risk, built-up area |
| `outputs/routes.geojson`, `route_metrics.csv` | Shortest and least-risk routes + comparison metrics |
| `outputs/rasters/*.tif` | Flood, landslide, fire, exposure, composite risk, built area, risk class (100 m) |
| `outputs/summary.json` | Everything the map needs (lets `--from-cache` work offline) |

### Web map (`index.html`)

* Layer toggles + **opacity sliders**: composite risk (green→yellow→orange→red→dark red), flood, landslide, fire,
  exposure, hotspots, roads (coloured by risk), hospitals, shelters, origin settlements, both route types
  (shortest = dashed grey, least-risk = solid green) and the 2024 landslide event markers with its 2 km zone.
* **Click anywhere** for the risk class and index (plus each layer's value) at that pixel.
* Legend, scale bar, risk-class statistics table, MCDM weights, validation result and the
  **route comparison table** (distance vs risk reduction).
* Rasters are embedded in the page as PNGs and byte arrays, so the map does not depend on expiring Earth Engine
  tile tokens. Only the Leaflet/Bootstrap CDN scripts and basemap tiles need internet.

### Screenshots

> Add screenshots here after your first run: `docs/composite_risk.png`, `docs/routes.png`, `docs/validation.png`.

## Pipeline

```
config.py                tunables: area, dates, factor ratings, weights, alpha, thresholds
src/gee_init.py          GEE auth/init with fix-it messages; AOI from OSM (bbox fallback)
src/hazard_flood.py      Sentinel-1 VV → Otsu → JRC + slope masks → frequency → 0–1
src/hazard_landslide.py  slope/elevation/NDVI/rainfall FR ratings → 0–1; July-2024 validation gate
src/hazard_fire.py       MODIS/VIIRS counts → Gaussian KDE × Dynamic World fuel → 0–1
src/exposure.py          Dynamic World built area + OSM road proximity → 0–1
src/mcdm.py              AHP (CR<0.1 asserted), entropy, CRITIC, weighted overlay, reclassification, stats
src/rasters.py           Earth Engine → NumPy export on one 100 m grid (+ GeoTIFFs)
src/hotspots.py          Very-High class → polygons with statistics
src/routing.py           OSMnx graph, risk on edges, settlement origins, dual Dijkstra
src/webmap.py            Folium/Leaflet standalone map + CLI entry point
src/pipeline.py          orchestration + acceptance self-check
```

## Methods & References

Each module's docstring and inline comments cite the precedent for the step it implements.

1. **Flood layer** — Sentinel-1 VV backscatter (IW), median composite per flood window (Aug-2018, Aug-2019,
   Jul/Aug-2024), focal-median speckle filter, **automatic Otsu threshold** from the histogram of flood-plausible
   terrain (guard-railed to −22…−12 dB), permanent water (JRC occurrence > 50 %) and slope > 5° removed
   (SAR-shadow false positives). Flood frequency is blended 70/30 with JRC non-permanent occurrence.
   * Moharrami et al. (2021). Automatic flood detection using Sentinel-1 images on Google Earth Engine.
     *Environmental Monitoring and Assessment*.
   * Tran et al. (2022). Otsu thresholding on Sentinel-1 time series for flood/surface-water mapping. *Remote Sensing*.
   * Otsu, N. (1979). A threshold selection method from gray-level histograms. *IEEE Transactions on Systems, Man,
     and Cybernetics*, 9(1), 62–66.
2. **Landslide layer** — SRTM slope and elevation, pre-event dry-season Sentinel-2 NDVI (low vegetation = higher
   susceptibility), maximum JJAS seasonal CHIRPS rainfall; **Frequency-Ratio-style** literature class ratings combined
   with FR-AHP factor weights (`LSI = Σ wf · FRf`). **Validation gate:** the 2 km zone around the July 2024
   Mundakkai–Chooralmala event must fall in the High/Very High classes (≥ 50 % of pixels and modal class); PASS/FAIL
   is printed with sampled values, and on FAIL the dominating factor and a concrete calibration change are reported.
   * Wang et al. (2020). Frequency ratio vs random forest landslide susceptibility. *International Journal of
     Environmental Research and Public Health*.
   * Rehman et al. (2022). FR–AHP–GIS multi-hazard mapping. *Remote Sensing*.
   * He et al. (2023). FR–RF hybrid landslide susceptibility (top performer). *Sensors*.
   * Lee, S., & Pradhan, B. (2007). Landslide hazard mapping at Selangor, Malaysia using frequency ratio and logistic
     regression models. *Landslides*, 4, 33–41.
3. **Fire layer** — 10 years of MODIS (FIRMS, C6.1) and VIIRS (VNP14A1) dry-season active-fire detections →
   Gaussian kernel density → weighted by Dynamic World tree/grass/shrub/crop cover.
   * Adab, H., Kanniah, K. D., & Solaimani, K. (2013). Modeling forest fire risk in the northeast of Iran using remote
     sensing and GIS techniques. *Natural Hazards*, 65, 1723–1743.
   * Parajuli et al. (2020). Forest fire risk assessment from active-fire data and GIS (Nepal).
   * Giglio, L., Schroeder, W., & Justice, C. O. (2016). The collection 6 MODIS active fire detection algorithm and
     fire products. *Remote Sensing of Environment*, 178, 31–41.
4. **Exposure layer** — Dynamic World built-area probability + proximity to OSM roads.
   * Brown, C. F., et al. (2022). Dynamic World, near real-time global 10 m land use land cover mapping.
     *Scientific Data*, 9.
   * Boeing, G. (2017). OSMnx: new methods for acquiring, constructing, analyzing, and visualizing complex street
     networks. *Computers, Environment and Urban Systems*, 65, 126–139.
5. **Normalisation** — min-max rescaling of every layer to 0–1 within the study area.
6. **MCDM weighting** — AHP pairwise matrix (Saaty scale) with terrain-justified target weights
   **flood 0.35, landslide 0.30, exposure 0.20, fire 0.15** (`config.AHP_TARGET_WEIGHTS`); the matrix reproduces them to
   ±0.005 and its **consistency ratio is asserted < 0.1** (≈ 0.019). Entropy weights are derived from 20 000 sampled
   raster pixels; the combined weights are the mean of AHP and entropy. **CRITIC** weights are printed alongside as a
   sensitivity comparison.
   * Saaty, T. L. (1980). *The Analytic Hierarchy Process*. McGraw-Hill.
   * Al-Aomar, R. (2010). A combined AHP-entropy method for deriving subjective and objective criteria weights.
   * Zhao et al. (2017). AHP + entropy landslide susceptibility mapping. *Entropy*.
   * Mukhametzyanov, I. (2021). Objective methods for determining criteria weights in MCDM: Entropy, CRITIC and SD.
     *Decision Making: Applications in Management and Engineering*.
7. **Composite risk index** — weighted linear combination → 5 classes (quantiles by default; Fisher–Jenks natural
   breaks via `config.RISK_CLASSIFICATION = "natural_breaks"`) → hotspot polygons from the Very High class, with km²
   and % per class.
   * Skilodimou et al. (2019). Multi-hazard weighted-overlay zoning and class reclassification.
   * Lyu, H.-M., & Yin, Z.-Y. (2023). Interval-FAHP-GIS risk assessment (cited upgrade path for interval-valued judgements).
   * Jenks, G. F. (1967). The data model concept in statistical mapping. *International Yearbook of Cartography*, 7.
8. **Least-risk emergency routing** — OSMnx drive network; mean risk sampled along every edge;
   `cost = length × (1 + α × mean_risk)` with α = 3.0 (`config.ROUTE_ALPHA`); Dijkstra run twice per
   origin–destination pair (pure length vs risk-weighted) and compared on distance, mean/max risk, and risk reduction
   vs distance increase. Origins are the 5 highest-risk settlement clusters (connected Dynamic World built-area blobs
   ranked by mean composite risk); destinations are the nearest OSM hospital and shelter.
   * Alçada-Almeida et al. (2009). A multiobjective approach to locate emergency shelters and identify evacuation
     routes in urban areas. *Geographical Analysis*.
   * Shekhar et al. (2012). Evacuation route planning algorithms.
   * Dijkstra, E. W. (1959). A note on two problems in connexion with graphs. *Numerische Mathematik*, 1, 269–271.
9. **Platform** — Gorelick, N., et al. (2017). Google Earth Engine: planetary-scale geospatial analysis for everyone.
   *Remote Sensing of Environment*, 202, 18–27. Wu, Q. (2020). geemap: a Python package for interactive mapping with
   Google Earth Engine. *Journal of Open Source Software*, 5(51), 2305.

> Full bibliographic details (volume/pages/DOIs) are given where certain and otherwise left out; verify against the
> publishers' records before formal use.

## Validation notes

* **Event coordinates.** The project brief gives the Mundakkai–Chooralmala event as ≈ 11.49 °N, 76.23 °E. Published
  event records (landslides.org; NRSC Charter 1029 products) place it at ≈ 11.486 °N, 76.156 °E — about 8 km
  further west. The code tests **both** points (`config.EVENT_POINT_VERIFIED`, `config.EVENT_POINT_SPEC`), prints
  PASS/FAIL for each, and uses the verified location as the headline gate. Change either constant if your records differ.
* **Quantile classes.** "High/Very High" means the top 40 % of the study area by landslide index, so the gate is a
  *necessary* check (does the model flag the zone?) rather than a strict accuracy measure. For a stricter test,
  switch `config.RISK_CLASSIFICATION` to natural breaks or compare against a full landslide inventory (e.g. GSI/KSDMA)
  with ROC/AUC.
* **Layer limitations.** CHIRPS (≈ 5 km) is coarse relative to Wayanad's orographic rainfall gradients; SRTM slope at
  30 m smooths steep escarpments; FR class ratings are literature-derived rather than fitted to a local inventory.
  Fitting FR values to a local landslide inventory would be the natural next calibration step.
* **Shelters** are OSM `amenity=shelter|community_centre|school` and `emergency=assembly_point|shelter`; official
  relief-camp lists are not in OSM. Replace or extend them via `config.SHELTER_TAGS`.

## Configuration

Everything tunable is in [`config.py`](config.py): study area, flood windows, landslide factor edges/ratings/weights,
fire parameters, AHP matrix and target weights, class method, routing α, settlement thresholds and map styling.

## License

MIT (or the licence of your choice) — add a `LICENSE` file before publishing.
