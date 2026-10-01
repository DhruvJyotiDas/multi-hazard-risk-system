# Wayanad Risk Observatory — 3D frontend draft

Open `index.html` directly in a browser. No build tools, API keys, CDN, network connection or Earth Engine authentication are needed. Alternatively, from the project root:

```powershell
python -m http.server 8080 --directory frontend
```

Visit `http://localhost:8080`. This draft does not replace the pipeline's generated Folium `index.html`.

## What works

- A shaded, triangulated 3D terrain scene rendered in Canvas, orbit by drag or arrow keys, zoom by wheel/buttons/plus/minus, camera reset, 2D plan view and vertical exaggeration.
- Five selectable hazard/composite surfaces, opacity, wireframe, settlement markers and route visibility controls.
- Click a terrain point to inspect an explicitly illustrative layer value; event inspection shows the saved validation result with its real coordinates.
- Saved risk area statistics, combined AHP + entropy weights and event validation, loaded from a bundled local script.
- Three example route comparisons with origin selection; Overview, Emergency routes and Model & data navigation changes the insight panel.
- PNG export of the canvas terrain view, responsive desktop/mobile layouts and keyboard controls.

## Data boundary

The terrain mesh, district-shaped footprint, river, scene markers, hazard surfaces, paths and route metrics are **illustrative design data**. No real DEM or georeferenced raster is being rendered. Scene inspection uses equal-width demo classes and does not apply the saved model's quantile thresholds. Example routes are not Dijkstra results and cannot guide travel. The cards, distribution, weights and event validation come from existing project outputs. The classified area total is the sum of `risk_stats.csv`, which differs slightly from the AOI's polygon area.

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
