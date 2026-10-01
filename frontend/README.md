# Wayanad Risk Observatory — 3D frontend draft

Open `index.html` directly in a browser. No build tools, API keys, CDN, network connection or Earth Engine authentication are needed. Alternatively, from the project root:

```powershell
python -m http.server 8080 --directory frontend
```

Visit `http://localhost:8080` (the Detailed map tab needs the project root served instead; see below). This draft does not replace the pipeline's generated Folium `index.html`.

## What works

- A **WebGL terrain renderer** (`terrain-gl.js`): the real SRTM elevation grid as a ~110,000-vertex / ~219,000-triangle mesh with per-pixel **hillshading** (light from the north-west), a depth buffer (ridges correctly hide what is behind them), the Sentinel-2 satellite composite and the selected hazard layer as smooth textures, and a hypsometric tint when satellite imagery is off. Orbit by drag or arrow keys, zoom by wheel/buttons/plus/minus, shift-drag to pan, camera reset, 2D plan view and vertical exaggeration (default 2.4×).
- Five selectable layers (composite risk, flood, landslide, fire, exposure), overlay opacity, wireframe, hillshading, hotspot boundaries, settlement markers, route visibility and a Sentinel-2 satellite-imagery toggle.
- Click a terrain point to inspect the **saved 100 m raster value** (index, class, elevation, coordinates); "Inspect event location" shows the saved July 2024 validation result at its real coordinates.
- Saved risk-area statistics, combined AHP + entropy weights and event validation, loaded from a bundled local script.
- Real route comparisons (10 origin–destination pairs) with origin and destination selection; Overview, Emergency routes and Model & data navigation changes the insight panel.
- PNG export of the canvas terrain view, responsive desktop/mobile layouts and keyboard controls. On desktop and laptop screens the workspace is sized to the viewport so the 3D scene is visible without scrolling; the side panels scroll independently.

## Detailed map tab (2D facilities, roads and route search)

The **Detailed map** tab in the header embeds the pipeline's 2D map (`../index.html?embed=1`) in a themed frame, so both
viewers live in one site:

- a master switch for every OpenStreetMap facility (hospitals, police, shelters, schools, banks, fuel, worship, ...) and all roads, with per-category switches;
- an **A to B search** that routes in the browser (shortest and least-risk) and lists every facility and place along the route;
- click-anywhere risk class and index, plus all hazard layers.

`?embed=1` hides the map's own header; the map has a "3D terrain view" link back here. Open the tab directly with
`frontend/index.html#detailed-map`. The tab needs the project root to be served so `../index.html` is reachable
(`python -m http.server 8000`, then <http://localhost:8000/frontend/>); when served with `--directory frontend` the tab
shows instructions instead. Regenerate the map with `python -m src.webmap` (or `--from-cache`).

## Data boundary

The viewer renders **real, georeferenced data**: SRTM elevation (`USGS/SRTMGL1_003`, 100 m, `assets/elevation.tif`), a cloud-masked dry-season Sentinel-2 RGB composite (2023-11-01 to 2024-04-30, `assets/satellite.png`), the saved 100 m flood / landslide / fire / exposure / composite grids and class raster, the district boundary, hotspot polygons, OSM hospitals/shelters/places and the saved Dijkstra routes and metrics (`assets/provenance.json` records the sources).

Display caveats: relief is visually exaggerated (default 2.4x, slider up to 10x); the elevation grid is the 100 m export grid (SRTM itself is 30 m, so very fine ridges are smoothed; a 30 m DEM would be the next level of detail); the satellite image is a historical dry-season composite; routes are saved model output, not live road conditions and cannot guide travel. Overlay routes, markers and labels are drawn on a 2D canvas above the terrain with the same camera maths, so they stay aligned but are not occluded by ridges. The classified area total is the sum of `risk_stats.csv`, which differs slightly from the AOI polygon area.

## Renderer

Three stacked canvases: a background canvas (grid and the vertical boundary wall), the WebGL terrain, and a transparent overlay canvas (boundary, hotspots, routes, markers, labels) that also receives pointer input. The WebGL vertex shader reproduces the oblique orthographic projection of `app.js` `project()`, which is why the overlay lines up exactly. Clicking ray-marches the DEM along the view ray, so inspection returns the true terrain point and the saved 100 m raster value under the cursor in both 3D and 2D.

If WebGL is unavailable the viewer falls back automatically to the older Canvas 2D triangle renderer; add `?renderer=canvas` to the URL to force it (useful for comparison).

Refresh the saved snapshot after a pipeline run:

```powershell
python frontend/export_data.py
```

Missing optional files are reported as unavailable. Malformed saved files fail explicitly during export. The frontend does not call Earth Engine or modify pipeline outputs.

See [the project review and implementation plan](../docs/frontend-plan.md) for the proposed path to a geographic 3D application.

## Previews and verification

- [Desktop preview](../docs/frontend-desktop.png)
- [Mobile preview](../docs/frontend-mobile.png)

Checked in headless Chrome: saved-data rendering, layer switching, 2D/3D selection, opacity, origin selection, panel navigation, event inspection and mobile horizontal overflow. No JavaScript exceptions were observed. JavaScript syntax validation passed, and all eight existing offline Python tests passed using a workspace-local temporary directory. Full Earth Engine execution was not rerun.
