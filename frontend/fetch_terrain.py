"""Cache real SRTM elevation and Sentinel-2 RGB context for the 3D frontend.

Uses the existing Earth Engine authentication and project configuration. No
analysis outputs are modified. See Google's ee.Image.getThumbURL documentation.
"""
import argparse
import io
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import ee
import numpy as np
import rasterio
from rasterio.warp import reproject, Resampling
import requests
from PIL import Image
import config
from src.gee_init import initialize
from src.hazard_landslide import _mask_s2

ASSETS = Path(__file__).with_name("assets")


def fetch(project=None):
    ASSETS.mkdir(exist_ok=True)
    initialize(project=project, interactive_auth=False)
    with rasterio.open(ROOT / "outputs/rasters/risk.tif") as src:
        bounds = list(src.bounds)
        target_transform, target_shape, target_crs = src.transform, src.shape, src.crs
    region = ee.Geometry.Rectangle(bounds, geodesic=False)
    dem_path = ASSETS / "elevation.tif"
    if not dem_path.exists():
        print("Downloading SRTM elevation...", flush=True)
        url = ee.Image(config.DS_SRTM).select("elevation").getDownloadURL({
            "region":region, "scale":config.EXPORT_SCALE, "crs":config.EXPORT_CRS, "format":"GEO_TIFF"})
        response = requests.get(url, timeout=180)
        response.raise_for_status()
        with rasterio.MemoryFile(response.content) as memory, memory.open() as source:
            elevation = np.full(target_shape, np.nan, dtype="float32")
            reproject(source=rasterio.band(source, 1), destination=elevation,
                      src_transform=source.transform, src_crs=source.crs,
                      dst_transform=target_transform, dst_crs=target_crs,
                      resampling=Resampling.bilinear, dst_nodata=np.nan)
        with rasterio.open(dem_path, "w", driver="GTiff", height=target_shape[0], width=target_shape[1],
                           count=1, dtype="float32", crs=target_crs, transform=target_transform,
                           nodata=np.nan, compress="deflate") as dst:
            dst.write(elevation, 1)
        print("SRTM saved and aligned to the analysis grid.", flush=True)
    satellite_path = ASSETS / "satellite.png"
    if not satellite_path.exists():
        print("Downloading cloud-masked Sentinel-2 dry-season RGB composite...", flush=True)
        collection = (ee.ImageCollection(config.DS_S2).filterBounds(region)
                      .filterDate(*config.NDVI_DATE_RANGE)
                      .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", config.S2_MAX_CLOUD_PROB)))
        image = collection.map(_mask_s2).select(["B4", "B3", "B2"]).median()
        rgb = image.visualize(min=0, max=3000, gamma=1.3)
        url = rgb.getThumbURL({"region":region, "dimensions":"1200x948", "crs":config.EXPORT_CRS, "format":"png"})
        response = requests.get(url, timeout=180)
        response.raise_for_status()
        with Image.open(io.BytesIO(response.content)) as image:
            image.save(satellite_path)
        print("Sentinel-2 RGB saved.", flush=True)
    (ASSETS / "provenance.json").write_text(json.dumps({
        "elevation_dataset": config.DS_SRTM, "elevation_grid_m": config.EXPORT_SCALE,
        "satellite_dataset": config.DS_S2, "satellite_date_range": list(config.NDVI_DATE_RANGE),
        "satellite_method": "SCL cloud/shadow mask, dry-season median B4/B3/B2; display stretch 0..3000 gamma 1.3",
        "bounds":bounds,
    }, indent=2), encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", help="Existing Earth Engine Cloud project")
    fetch(parser.parse_args().project)
