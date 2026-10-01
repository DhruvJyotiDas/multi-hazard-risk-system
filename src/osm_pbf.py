"""OpenStreetMap data from a regional PBF extract (alternative to the Overpass API).

Purpose : build the drive network and the hospital / shelter / place POIs of the
          study area when the Overpass API is unreachable or rate-limited (corporate
          networks, CI, heavy-load periods) -- and as a faster, reproducible
          default: one ~180 MB download of the Kerala extract instead of many
          Overpass queries.
Method  : stream the extract once with pyosmium (node locations resolved on the
          fly), keep highway ways that cars may use (the same exclusions as
          OSMnx's ``drive`` network type), split them into directed edges
          honouring ``oneway`` tags, clip to the study area (+ buffer) and keep the
          largest strongly connected component. POIs are matched against the tag
          dictionaries in ``config.py`` and reduced to representative points.
Reference: Boeing 2017, *Computers, Environment and Urban Systems* 65 -- OSMnx
          (network semantics reproduced here); OpenStreetMap contributors, ODbL.
"""
from __future__ import annotations

import logging
import math
import urllib.request

import geopandas as gpd
import networkx as nx
import numpy as np
import osmium
import pandas as pd
import shapely
from shapely.geometry.base import BaseGeometry

import config

log = logging.getLogger(__name__)

# highway values OSMnx's 'drive' filter excludes
_NON_DRIVE = {"cycleway", "footway", "path", "pedestrian", "steps", "track", "corridor", "elevator", "escalator",
              "proposed", "construction", "bridleway", "abandoned", "platform", "raceway", "bus_guideway",
              "razed", "planned", "no", "rest_area", "services"}
_NON_DRIVE_SERVICE = {"alley", "driveway", "emergency_access", "parking", "parking_aisle", "private"}
_ONEWAY_YES = {"yes", "true", "1"}
_ONEWAY_REVERSE = {"-1", "reverse"}
_KEYS = ("highway", "amenity", "healthcare", "emergency", "place")


def ensure_pbf() -> None:
    """Download the regional extract once (cached in outputs/cache)."""
    if config.OSM_PBF_PATH.exists() and config.OSM_PBF_PATH.stat().st_size > 1_000_000:
        return
    config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    log.info("Downloading OSM extract %s ...", config.OSM_PBF_URL)
    req = urllib.request.Request(config.OSM_PBF_URL, headers={"User-Agent": "wayanad-risk-system/1.0"})
    tmp = config.OSM_PBF_PATH.with_suffix(".part")
    with urllib.request.urlopen(req, timeout=120) as resp, open(tmp, "wb") as out:
        while chunk := resp.read(1 << 20):
            out.write(chunk)
    tmp.replace(config.OSM_PBF_PATH)
    log.info("OSM extract saved (%.0f MB)", config.OSM_PBF_PATH.stat().st_size / 1e6)


def _haversine_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * 6_371_000 * math.asin(math.sqrt(a))


def _drivable(tags) -> bool:
    hw = tags.get("highway")
    if not hw or hw in _NON_DRIVE:
        return False
    if tags.get("area") == "yes" or tags.get("motor_vehicle") == "no" or tags.get("motorcar") == "no":
        return False
    if tags.get("access") in ("private", "no") and tags.get("motor_vehicle") not in ("yes", "designated"):
        return False
    return tags.get("service") not in _NON_DRIVE_SERVICE


def _matches(tags, wanted: dict) -> bool:
    return any(tags.get(k) in vals for k, vals in wanted.items())


def scan_extract(study_area: BaseGeometry) -> tuple[list, list]:
    """One streaming pass -> (drivable ways [(highway, oneway, [(id, lon, lat)])], POI records)."""
    ensure_pbf()
    w, s, e, n = study_area.buffer(config.OSM_CLIP_BUFFER_DEG).bounds
    ways, pois = [], []
    kinds = (("hospital", config.HOSPITAL_TAGS), ("hospital_fb", config.HOSPITAL_FALLBACK_TAGS),
             ("shelter", config.SHELTER_TAGS), ("place", config.PLACE_TAGS))
    proc = osmium.FileProcessor(str(config.OSM_PBF_PATH)).with_locations().with_filter(osmium.filter.KeyFilter(*_KEYS))
    for obj in proc:
        tags = obj.tags
        if obj.is_way():
            try:
                pts = [(nd.ref, nd.location.lon, nd.location.lat) for nd in obj.nodes]
            except osmium.InvalidLocationError:
                continue
            if not any(w <= x <= e and s <= y <= n for _, x, y in pts):
                continue
            if _drivable(tags):
                ways.append((tags.get("highway"), tags.get("oneway", ""), tags.get("junction", ""), pts))
            lon, lat = float(np.mean([p[1] for p in pts])), float(np.mean([p[2] for p in pts]))
        elif obj.is_node():
            lon, lat = obj.location.lon, obj.location.lat
        else:
            continue
        if not (w <= lon <= e and s <= lat <= n):
            continue
        for kind, wanted in kinds:
            if _matches(tags, wanted):
                pois.append({"kind": kind, "name": tags.get("name", ""), "lon": lon, "lat": lat})
    log.info("PBF scan: %d drivable ways, %d POI features in the study-area bbox", len(ways), len(pois))
    return ways, pois


def build_graph(ways: list, study_area: BaseGeometry) -> nx.MultiDiGraph:
    """Directed drive graph from raw ways, clipped to the study area and reduced to its largest SCC."""
    G = nx.MultiDiGraph(crs="EPSG:4326", name="osm_pbf_drive")
    for hw, oneway, junction, pts in ways:
        forward_only = oneway in _ONEWAY_YES or junction == "roundabout" or hw == "motorway"
        reverse_only = oneway in _ONEWAY_REVERSE
        for (a, ax, ay), (b, bx, by) in zip(pts[:-1], pts[1:]):
            if a == b:
                continue
            length = _haversine_m(ax, ay, bx, by)
            G.add_node(a, x=ax, y=ay)
            G.add_node(b, x=bx, y=by)
            if not reverse_only:
                G.add_edge(a, b, length=length, highway=hw)
            if not forward_only:
                G.add_edge(b, a, length=length, highway=hw)
    keep_poly = study_area.buffer(config.OSM_CLIP_BUFFER_DEG)
    ids = list(G.nodes)
    inside = shapely.contains_xy(keep_poly, [G.nodes[i]["x"] for i in ids], [G.nodes[i]["y"] for i in ids])
    G.remove_nodes_from([i for i, ok in zip(ids, inside) if not ok])
    largest = max(nx.strongly_connected_components(G), key=len)
    G = G.subgraph(largest).copy()
    for node, data in G.nodes(data=True):
        data["street_count"] = G.degree(node)
    log.info("Drive network from PBF: %d nodes, %d edges (largest strongly connected component)",
             len(G.nodes), len(G.edges))
    return G


def build_pois(pois: list, study_area: BaseGeometry) -> dict[str, gpd.GeoDataFrame]:
    """Hospitals, shelters and named places inside the study area (hospital falls back to clinics)."""
    df = pd.DataFrame(pois)

    def frame(kind: str, out_kind: str) -> gpd.GeoDataFrame:
        sub = df[df["kind"] == kind] if not df.empty else df
        if sub.empty:
            return gpd.GeoDataFrame({"name": [], "kind": []}, geometry=[], crs="EPSG:4326")
        g = gpd.GeoDataFrame({"name": sub["name"].values, "kind": out_kind},
                             geometry=gpd.points_from_xy(sub["lon"], sub["lat"]), crs="EPSG:4326")
        g = g[g.within(study_area)].drop_duplicates(subset=["name", "geometry"]).reset_index(drop=True)
        return g

    hospitals = frame("hospital", "hospital")
    # mis-tagged dental clinics, pharmacies, labs ... are not emergency destinations
    hospitals = hospitals[~hospitals["name"].str.contains(config.HOSPITAL_NAME_EXCLUDE, case=False, regex=True,
                                                          na=False)].reset_index(drop=True)
    if hospitals.empty:
        log.warning("No OSM hospitals found -- falling back to clinics / doctors")
        hospitals = frame("hospital_fb", "hospital")
    shelters = frame("shelter", "shelter")
    places = frame("place", "place")
    places = places[places["name"] != ""].reset_index(drop=True)
    log.info("OSM POIs: %d hospitals, %d shelters, %d named places", len(hospitals), len(shelters), len(places))
    return {"hospital": hospitals, "shelter": shelters, "place": places}
