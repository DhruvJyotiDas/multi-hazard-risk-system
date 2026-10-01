"""Earth Engine -> NumPy bridge: export analysis layers on one common grid.

Purpose : the routing graph, the hotspot vectoriser and the web map all need
          the final rasters on the client. This module pulls them straight out
          of Earth Engine (``getDownloadURL``; no manual downloads) on a single
          shared EXPORT_SCALE grid, and writes GeoTIFFs to ``outputs/rasters``.
Method  : the 30 m analysis layers are averaged to EXPORT_SCALE with
          ``reduceResolution(mean)``, stacked into one multi-band float32
          GeoTIFF and downloaded (band-by-band fallback if the request is too
          large). A band named ``inside`` (1 inside the AOI) preserves the AOI
          mask because Earth Engine writes masked pixels as 0.
Reference: Gorelick et al. 2017, *Remote Sensing of Environment* 202.
"""
from __future__ import annotations

import io
import logging
import zipfile
from dataclasses import dataclass, field

import ee
import numpy as np
import rasterio
import requests
from rasterio.transform import Affine, rowcol

import config

log = logging.getLogger(__name__)

RASTER_DIR = config.OUTPUT_DIR / "rasters"
RASTER_NAMES = ("flood", "landslide", "exposure", "fire", "risk", "built")


@dataclass
class RasterStack:
    """Co-registered 2-D float arrays (NaN outside the AOI) on one lon/lat grid."""

    arrays: dict[str, np.ndarray]
    transform: Affine
    crs: str = "EPSG:4326"
    classes: np.ndarray | None = None
    breaks: list[float] = field(default_factory=list)

    @property
    def shape(self) -> tuple[int, int]:
        return next(iter(self.arrays.values())).shape

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        """(west, south, east, north)."""
        rows, cols = self.shape
        w, n = self.transform * (0, 0)
        e, s = self.transform * (cols, rows)
        return w, s, e, n

    def sample(self, name: str, lon: float, lat: float) -> float:
        """Value of layer ``name`` at a lon/lat (NaN when outside the grid)."""
        r, c = rowcol(self.transform, lon, lat)
        rows, cols = self.shape
        if 0 <= r < rows and 0 <= c < cols:
            return float(self.arrays[name][r, c])
        return float("nan")

    def pixel_area_km2(self) -> np.ndarray:
        """Per-row pixel area (km2), shape (rows, 1), accounting for latitude."""
        rows, _ = self.shape
        lat = self.transform.f + (np.arange(rows) + 0.5) * self.transform.e
        dx = abs(self.transform.a) * 111.32 * np.cos(np.radians(lat))
        dy = abs(self.transform.e) * 110.57
        return (dx * dy)[:, None]


def _to_export_grid(img: ee.Image) -> ee.Image:
    """Average a 30 m layer down to the export grid."""
    # constant / derived images may lack a default projection, which reduceResolution requires
    img = img.setDefaultProjection(config.ANALYSIS_CRS, None, config.ANALYSIS_SCALE)
    return (img.toFloat().reduceResolution(ee.Reducer.mean(), bestEffort=True, maxPixels=4096)
            .reproject(crs=config.EXPORT_CRS, scale=config.EXPORT_SCALE))


def _download(img: ee.Image, region: ee.Geometry, per_band: bool) -> bytes:
    params = {"region": region, "scale": config.EXPORT_SCALE, "crs": config.EXPORT_CRS,
              "format": "GEO_TIFF", "filePerBand": per_band}
    url = img.getDownloadURL(params)
    resp = requests.get(url, timeout=600)
    if resp.status_code != 200:     # surface Earth Engine's own explanation, not just the status code
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:600]}")
    return resp.content


def _read_tifs(payload: bytes) -> tuple[list[np.ndarray], Affine]:
    """Read bands from a GeoTIFF or a zip of GeoTIFFs; returns (bands, transform)."""
    blobs = []
    if payload[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(payload)) as zf:
            blobs = [zf.read(n) for n in sorted(zf.namelist()) if n.lower().endswith(".tif")]
    else:
        blobs = [payload]
    bands, transform = [], None
    for blob in blobs:
        with rasterio.MemoryFile(blob) as mem, mem.open() as src:
            transform = transform or src.transform
            bands.extend(src.read(i + 1).astype("float32") for i in range(src.count))
    return bands, transform


def _download_band(name: str, img: ee.Image, region: ee.Geometry, resume: bool, retries: int = 3) -> bytes:
    """Download one band as GeoTIFF bytes; cached in outputs/rasters/_bands so a failed run can resume."""
    cache = RASTER_DIR / "_bands" / f"{name}.tif"
    if resume and cache.exists():
        log.info("  band '%s' reused from %s", name, cache.name)
        return cache.read_bytes()
    last = None
    for attempt in range(1, retries + 1):
        try:
            payload = _download(img, region, per_band=False)
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_bytes(payload)
            log.info("  band '%s' downloaded (%.0f kB)", name, len(payload) / 1e3)
            return payload
        except Exception as err:  # noqa: BLE001 - transient EE / network errors are retried
            last = err
            log.warning("  band '%s' attempt %d/%d failed: %s", name, attempt, retries, err)
    raise RuntimeError(f"Could not export band '{name}': {last}")


def export_rasters(layers: dict[str, ee.Image], aoi: ee.Geometry, resume: bool = False) -> RasterStack:
    """Download all layers on one grid and return a RasterStack (also saved to outputs/rasters).

    Bands are fetched one request each (a single 7-band request exceeds Earth Engine's compute limits for
    the deep composite graph) and cached, so ``resume=True`` skips bands that are already on disk.
    """
    names = [n for n in RASTER_NAMES if n in layers] + ["inside"]
    imgs = [_to_export_grid(layers[n].rename(n).unmask(0)) for n in names[:-1]]
    imgs.append(_to_export_grid(ee.Image.constant(1).clip(aoi).unmask(0).rename("inside")))
    region = aoi.bounds()
    log.info("Exporting %d rasters at %d m from Earth Engine (one request per band) ...", len(names), config.EXPORT_SCALE)
    bands, transform = [], None
    for name, img in zip(names, imgs):
        b, t = _read_tifs(_download_band(name, img, region, resume))
        bands.append(b[0])
        transform = transform or t
    shapes = {b.shape for b in bands}
    if len(shapes) != 1:
        raise RuntimeError(f"Exported bands are not co-registered: {shapes}")
    data = dict(zip(names, bands))
    inside = data.pop("inside") > 0.5
    for k in data:
        data[k] = np.where(inside, data[k], np.nan).astype("float32")
    stack = RasterStack(arrays=data, transform=transform)
    save_stack(stack)
    log.info("Rasters exported: grid %dx%d px, bounds W%.3f S%.3f E%.3f N%.3f", *stack.shape[::-1], *stack.bounds)
    return stack


def save_stack(stack: RasterStack) -> None:
    """Write each layer (and the class raster, if present) as a GeoTIFF in outputs/rasters."""
    RASTER_DIR.mkdir(parents=True, exist_ok=True)
    rows, cols = stack.shape
    items = dict(stack.arrays)
    if stack.classes is not None:
        items["risk_class"] = stack.classes.astype("float32")
    for name, arr in items.items():
        with rasterio.open(RASTER_DIR / f"{name}.tif", "w", driver="GTiff", height=rows, width=cols, count=1,
                           dtype="float32", crs=stack.crs, transform=stack.transform, nodata=np.nan,
                           compress="deflate") as dst:
            dst.write(arr.astype("float32"), 1)


def load_stack() -> RasterStack:
    """Reload a previously exported stack from outputs/rasters (offline re-runs)."""
    arrays, transform = {}, None
    for name in (*RASTER_NAMES, "risk_class"):
        path = RASTER_DIR / f"{name}.tif"
        if not path.exists():
            continue
        with rasterio.open(path) as src:
            arrays[name] = src.read(1).astype("float32")
            transform = transform or src.transform
    if "risk" not in arrays:
        raise FileNotFoundError(f"No exported rasters in {RASTER_DIR}; run the pipeline with Earth Engine first")
    classes = arrays.pop("risk_class", None)
    return RasterStack(arrays=arrays, transform=transform, classes=classes)


def classify_array(risk: np.ndarray, breaks: list[float]) -> np.ndarray:
    """Client-side 1..5 class raster (0 outside the AOI) from the class boundaries."""
    cls = np.digitize(np.nan_to_num(risk, nan=-1.0), breaks, right=False) + 1
    cls = np.where(np.isnan(risk), 0, cls)
    return cls.astype("uint8")
