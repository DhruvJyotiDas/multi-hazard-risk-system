"""Synthetic rasters and road graph used by the offline tests (no Earth Engine / network needed)."""
from __future__ import annotations

import math

import geopandas as gpd
import networkx as nx
import numpy as np
from rasterio.transform import from_origin
from shapely.geometry import Point

from src.rasters import RasterStack, classify_array

ROWS = COLS = 220
RES = 0.0009


def make_stack() -> RasterStack:
    """Gaussian risk dome with a high-risk centre, two built-up blobs and a nodata strip."""
    yy, xx = np.mgrid[0:ROWS, 0:COLS]
    risk = np.exp(-(((xx - 110) / 50) ** 2 + ((yy - 100) / 60) ** 2)).astype("float32")
    risk[:30, :] = np.nan
    built = np.zeros((ROWS, COLS), "float32")
    built[96:104, 30:38] = 0.9
    built[150:160, 150:160] = 0.8
    built[60:68, 160:170] = 0.85
    nan = np.isnan(risk)
    arrays = {"risk": risk, "built": built, "flood": np.clip(risk * 1.1, 0, 1), "landslide": risk ** 0.5,
              "fire": risk * 0.5, "exposure": np.clip(built + 0.2 * risk, 0, 1)}
    arrays = {k: np.where(nan, np.nan, v).astype("float32") for k, v in arrays.items()}
    stack = RasterStack(arrays=arrays, transform=from_origin(76.0, 11.8, RES, RES))
    stack.breaks = [float(b) for b in np.nanquantile(risk, [0.2, 0.4, 0.6, 0.8])]
    stack.classes = classify_array(risk, stack.breaks)
    return stack


def make_graph(n: int = 20, highway: str = "primary") -> nx.MultiDiGraph:
    """n x n grid of two-way roads, ~1.1 km spacing, lon/lat node coordinates."""
    g = nx.MultiDiGraph(crs="EPSG:4326")
    for i in range(n):
        for j in range(n):
            g.add_node(i * n + j, x=76.0 + i * 0.01, y=11.8 - j * 0.01)

    def length(a, b):
        dx = (g.nodes[a]["x"] - g.nodes[b]["x"]) * math.cos(math.radians(11.7))
        return 111000 * math.hypot(dx, g.nodes[a]["y"] - g.nodes[b]["y"])

    for i in range(n):
        for j in range(n):
            a = i * n + j
            for di, dj in ((1, 0), (0, 1)):
                if i + di < n and j + dj < n:
                    b = (i + di) * n + j + dj
                    g.add_edge(a, b, length=length(a, b), highway=highway)
                    g.add_edge(b, a, length=length(a, b), highway=highway)
    return g


def make_pois() -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    hospitals = gpd.GeoDataFrame({"name": ["Test Hospital"]}, geometry=[Point(76.185, 11.72)], crs=4326)
    shelters = gpd.GeoDataFrame({"name": ["Test Shelter"]}, geometry=[Point(76.05, 11.65)], crs=4326)
    return hospitals, shelters
