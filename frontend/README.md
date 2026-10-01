# Wayanad Risk Observatory — 3D frontend draft

Open `index.html` directly in a browser. No build tools, API keys, CDN, network connection or Earth Engine authentication are needed. Alternatively, from the project root:

```powershell
python -m http.server 8080 --directory frontend
```

Visit `http://localhost:8080`. This draft does not replace the pipeline's generated Folium `index.html`.

## What works

- A shaded, triangulated 3D terrain scene rendered in Canvas, orbit by drag or arrow keys, zoom by wheel/buttons/plus/minus, shift-drag to pan, camera reset, 2D plan view and vertical exaggeration.
- Five selectable layers (composite risk, flood, landslide, fire, exposure), opacity, wireframe, hotspot boundaries, settlement markers, route visibility and a Sentinel-2 satellite-imagery toggle.
- Click a terrain point to inspect the **saved 100 m raster value** (index, class, elevation, coordinates); "Inspect event location" shows the saved July 2024 validation result at its real coordinates.
- Saved risk-area statistics, combined AHP + entropy weights and event validation, loaded from a bundled local script.
- Real route comparisons (10 origin–destination pairs) with origin and destination selection; Overview, Emergency routes and Model & data navigation changes the insight panel.
- PNG export of the canvas terrain view, responsive desktop/mobile layouts and keyboard controls. On desktop and laptop screens the workspace is sized to the viewport so the 3D scene is visible without scrolling; the side panels scroll independently.

## Data boundary

The viewer renders **real, georeferenced data**: SRTM elevation (`USGS/SRTMGL1_003`, 100 m, `assets/elevation.tif`), a cloud-masked dry-season Sentinel-2 RGB composite (2023-11-01 to 2024-04-30, `assets/satellite.png`), the saved 100 m flood / landslide / fire / exposure / composite grids and class raster, the district boundary, hotspot polygons, OSM hospitals/shelters/places and the saved Dijkstra routes and metrics (`assets/provenance.json` records the sources).

Display caveats: the 3D mesh is coarser than the data (about four raster cells per triangle), so each triangle is coloured from the **mean of the valid cells under it** (this removes aliasing speckle; click inspection still reads the exact 100 m value); relief is visually exaggerated; imagery is a historical composite; routes are saved model output, not live road conditions and cannot guide travel. The classified area total is the sum of `risk_stats.csv`, which differs slightly from the AOI polygon area.

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
