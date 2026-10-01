"""Bundle real pipeline grids, vectors and terrain into an offline browser script."""
from pathlib import Path
import csv
import json
import base64
import math
import numpy as np
import rasterio
from PIL import Image
import io

ROOT = Path(__file__).resolve().parents[1]
OUTPUTS = ROOT / "outputs"


def read_csv(name):
    path = OUTPUTS / name
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def read_json(name):
    path = OUTPUTS / name
    return json.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else None


def export():
    summary = read_json("summary.json") or {}
    raster_dir = OUTPUTS / "rasters"
    with rasterio.open(raster_dir / "risk.tif") as reference:
        rows, cols = reference.shape
        bounds = list(reference.bounds)
        transform, crs = reference.transform, reference.crs
    grids = {}
    for name in ["risk", "flood", "landslide", "fire", "exposure", "built"]:
        with rasterio.open(raster_dir / f"{name}.tif") as source:
            if source.shape != (rows, cols) or source.transform != transform or source.crs != crs:
                raise ValueError(f"{name} is not aligned to the risk grid")
            values = source.read(1)
            encoded = np.where(np.isfinite(values), np.rint(np.clip(np.nan_to_num(values), 0, 1)*254), 255).astype("uint8")
            grids[name] = base64.b64encode(encoded.tobytes()).decode("ascii")
    with rasterio.open(raster_dir / "risk_class.tif") as source:
        classes = np.nan_to_num(source.read(1)).astype("uint8")
    assets = Path(__file__).with_name("assets")
    elevation = None
    if (assets / "elevation.tif").exists():
        with rasterio.open(assets / "elevation.tif") as source:
            if source.shape != (rows,cols) or source.transform != transform:
                raise ValueError("Elevation is not aligned to the risk grid")
            values = source.read(1)
            elevation = base64.b64encode(np.where(np.isfinite(values),np.rint(values),-32768).astype("<i2").tobytes()).decode("ascii")
    texture = None
    if (assets / "satellite.png").exists():
        # Embed imagery so file:// previews and PNG export never need cross-origin access.
        with Image.open(assets / "satellite.png") as source:
            output = io.BytesIO()
            source.convert("RGB").save(output, format="JPEG", quality=88)
            texture = "data:image/jpeg;base64," + base64.b64encode(output.getvalue()).decode("ascii")
    routes = read_json("routes.geojson") or {"type":"FeatureCollection", "features":[]}
    metrics = read_csv("route_metrics.csv")
    # Routing outputs must agree before they are shown as a single snapshot.
    for row in metrics:
        for route_type, key in [("shortest", "shortest_km"),("least_risk", "least_risk_km")]:
            matches = [f for f in routes["features"] if str(f["properties"]["origin_id"]) == row["origin_id"]
                       and f["properties"]["destination_type"] == row["destination_type"]
                       and f["properties"]["route_type"] == route_type]
            if len(matches)!=1 or abs(float(matches[0]["properties"]["distance_km"])-float(row[key]))>.002:
                raise ValueError("Route GeoJSON and metrics disagree; retry after routing finishes")
    aoi = read_json("cache/aoi.geojson")
    if aoi:
        from shapely.geometry import shape, mapping
        aoi = mapping(shape(aoi).simplify(.00015, preserve_topology=True))
    hotspots = read_json("hotspots.geojson")
    if hotspots:
        from shapely.geometry import shape, mapping
        for feature in hotspots["features"]:
            feature["geometry"] = mapping(shape(feature["geometry"]).simplify(.0002, preserve_topology=True))
    payload = {
        "stats": read_csv("risk_stats.csv"),
        "weights": read_csv("mcdm_weights.csv"),
        "validation": read_json("landslide_validation.json"),
        "summary": summary,
        "metrics": metrics,
        "routes": routes,
        "aoi": aoi,
        "hotspots": hotspots,
        "pois": read_json("cache/wayanad_pois.geojson"),
        "raster": {"rows":rows, "cols":cols, "bounds":bounds, "grids":grids,
                   "classes":base64.b64encode(classes.tobytes()).decode("ascii"), "elevation":elevation,
                   "width_m":(bounds[2]-bounds[0])*111320*math.cos(math.radians((bounds[1]+bounds[3])/2))},
        "texture": texture,
        "provenance": json.loads((assets/"provenance.json").read_text()) if (assets/"provenance.json").exists() else None,
        "snapshot": {"routes_modified":(OUTPUTS/"routes.geojson").stat().st_mtime,
                     "note":"Current output files from the latest completed pipeline run."},
        "availability": {
            "routes": (OUTPUTS / "routes.geojson").exists(),
            "summary": (OUTPUTS / "summary.json").exists(),
            "composite_raster": (OUTPUTS / "rasters" / "risk.tif").exists(),
        },
        "sources": ["outputs/risk_stats.csv", "outputs/mcdm_weights.csv", "outputs/landslide_validation.json"],
        "scene": "geographic",
    }
    # Escape script-sensitive characters so the bundle is safe to embed later.
    encoded = json.dumps(payload, ensure_ascii=True, separators=(",",":"), allow_nan=False).replace("<", "\\u003c")
    target = Path(__file__).with_name("data.js")
    target.write_text("// Generated by export_data.py from saved project outputs.\nwindow.OBSERVATORY_DATA = " + encoded + ";\n", encoding="utf-8")
    print(f"Wrote {target}: {len(payload['stats'])} risk classes, {len(payload['weights'])} weights")


if __name__ == "__main__":
    export()
