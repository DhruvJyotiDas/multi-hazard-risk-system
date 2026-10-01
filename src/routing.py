"""Least-risk emergency routing on the OSM road network.

Purpose : find evacuation routes from the highest-risk settlements to the
          nearest hospital and shelter that minimise hazard exposure rather
          than distance alone, and quantify the trade-off.
Method  : (1) download the OSM drive network with OSMnx; (2) sample the
          composite risk raster along every edge (mean and max risk of points
          every ROUTE_EDGE_SAMPLE_STEP_M); (3) edge cost
          ``length * (1 + alpha * mean_risk)`` with alpha = config.ROUTE_ALPHA;
          (4) origins = the N highest-risk settlement clusters (connected
          components of Dynamic World built-area pixels ranked by mean
          composite risk); destinations = the nearest OSM hospital and the
          nearest shelter (nearest by network distance); (5) Dijkstra twice per
          origin-destination pair -- pure length (shortest) and risk-weighted
          cost (least-risk) -- and report distance, mean/max risk and the
          risk-reduction vs distance-increase trade-off.
References:
  * Alcada-Almeida et al. 2009, *Geographical Analysis* 41 -- multiobjective
    emergency/evacuation routing.
  * Shekhar et al. 2012, *Computing in Science & Engineering* -- evacuation
    route planning algorithms.
  * Dijkstra 1959; Boeing 2017, *Computers, Environment and Urban Systems* 65 -- OSMnx.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass

import geopandas as gpd
import networkx as nx
import numpy as np
import osmnx as ox
import pandas as pd
from scipy import ndimage
from shapely.geometry import LineString, Point
from shapely.geometry.base import BaseGeometry

import config
from src import osm_pbf
from src.exposure import highway_values
from src.rasters import RasterStack

log = logging.getLogger(__name__)

M_PER_DEG_LAT = 110_570.0


@dataclass
class RoutingResult:
    """All routing outputs consumed by the web map."""

    routes: gpd.GeoDataFrame
    metrics: pd.DataFrame
    origins: gpd.GeoDataFrame
    hospitals: gpd.GeoDataFrame
    shelters: gpd.GeoDataFrame
    roads: gpd.GeoDataFrame
    graph: nx.MultiDiGraph | None = None   # annotated (length, risk) graph, embedded compactly in the web map


# --------------------------------------------------------------------------- #
# Network + points of interest
# --------------------------------------------------------------------------- #
_PBF_SCAN: dict = {}


def _pbf_scan(study_area: BaseGeometry) -> tuple[list, list]:
    """Stream the regional extract once per process and reuse the result for graph and POIs."""
    if "scan" not in _PBF_SCAN:
        _PBF_SCAN["scan"] = osm_pbf.scan_extract(study_area)
    return _PBF_SCAN["scan"]


def _graph_from_overpass(study_area: BaseGeometry) -> nx.MultiDiGraph:
    log.info("Downloading OSM '%s' network from Overpass ...", config.ROUTE_NETWORK_TYPE)
    G = ox.graph_from_polygon(study_area, network_type=config.ROUTE_NETWORK_TYPE, simplify=True)
    return ox.truncate.largest_component(G, strongly=True)


def _graph_from_pbf(study_area: BaseGeometry) -> nx.MultiDiGraph:
    ways, _ = _pbf_scan(study_area)
    return osm_pbf.build_graph(ways, study_area)


def load_graph(study_area: BaseGeometry) -> nx.MultiDiGraph:
    """Drive network of the study area (cached as GraphML); largest strongly connected component only."""
    config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    if config.GRAPH_CACHE.exists():
        G = ox.load_graphml(config.GRAPH_CACHE)
        log.info("OSM network loaded from cache: %d nodes, %d edges", len(G.nodes), len(G.edges))
        return G
    loaders = {"pbf": _graph_from_pbf, "overpass": _graph_from_overpass}
    order = [config.OSM_SOURCE] + [k for k in loaders if k != config.OSM_SOURCE]
    G, last_err = None, None
    for src in order:
        try:
            G = loaders[src](study_area)
            break
        except Exception as err:  # noqa: BLE001 - network failures differ by source; try the next one
            last_err = err
            log.warning("OSM source '%s' failed (%s); trying next source", src, err)
    if G is None:
        raise RuntimeError(f"Could not obtain an OSM road network from any source: {last_err}")
    ox.save_graphml(G, config.GRAPH_CACHE)
    log.info("OSM network: %d nodes, %d edges", len(G.nodes), len(G.edges))
    return G


def graph_edges(G: nx.MultiDiGraph) -> gpd.GeoDataFrame:
    """Edge GeoDataFrame (EPSG:4326) with a 'highway' column."""
    edges = ox.convert.graph_to_gdfs(G, nodes=False)
    return edges.reset_index()


def _poi_points(study_area: BaseGeometry, tags: dict, kind: str) -> gpd.GeoDataFrame:
    """Download OSM features with ``tags`` and reduce them to named representative points."""
    try:
        feats = ox.features_from_polygon(study_area, tags)
    except Exception as err:  # noqa: BLE001 - OSMnx raises InsufficientResponseError when nothing matches
        log.warning("No OSM '%s' features found (%s)", kind, err)
        return gpd.GeoDataFrame({"name": [], "kind": []}, geometry=[], crs="EPSG:4326")
    feats = feats[feats.geometry.notna()].copy()
    pts = feats.geometry.representative_point()
    names = feats["name"] if "name" in feats.columns else pd.Series(index=feats.index, dtype=object)
    out = gpd.GeoDataFrame({"name": names.fillna("").astype(str).values, "kind": kind}, geometry=pts.values,
                           crs="EPSG:4326")
    return out.reset_index(drop=True)


def _pois_from_overpass(study_area: BaseGeometry) -> dict[str, gpd.GeoDataFrame]:
    hospitals = _poi_points(study_area, config.HOSPITAL_TAGS, "hospital")
    if hospitals.empty:
        log.warning("No OSM hospitals -- falling back to clinics / doctors")
        hospitals = _poi_points(study_area, config.HOSPITAL_FALLBACK_TAGS, "hospital")
    shelters = _poi_points(study_area, config.SHELTER_TAGS, "shelter")
    places = _poi_points(study_area, config.PLACE_TAGS, "place")
    places = places[places["name"] != ""].reset_index(drop=True)
    return {"hospital": hospitals, "shelter": shelters, "place": places}


def _pois_from_pbf(study_area: BaseGeometry) -> dict[str, gpd.GeoDataFrame]:
    _, pois = _pbf_scan(study_area)
    return osm_pbf.build_pois(pois, study_area)


def load_pois(study_area: BaseGeometry) -> dict[str, gpd.GeoDataFrame]:
    """Hospitals, shelters and place names from OSM (cached to one GeoJSON)."""
    if config.POI_CACHE.exists():
        allp = gpd.read_file(config.POI_CACHE)
        out = {k: allp[allp["kind"] == k].reset_index(drop=True) for k in ("hospital", "shelter", "place")}
        log.info("POIs from cache: %s", {k: len(v) for k, v in out.items()})
        return out
    loaders = {"pbf": _pois_from_pbf, "overpass": _pois_from_overpass}
    order = [config.OSM_SOURCE] + [k for k in loaders if k != config.OSM_SOURCE]
    pois, last_err = None, None
    for src in order:
        try:
            pois = loaders[src](study_area)
            break
        except Exception as err:  # noqa: BLE001
            last_err = err
            log.warning("POI source '%s' failed (%s); trying next source", src, err)
    if pois is None:
        raise RuntimeError(f"Could not obtain OSM points of interest: {last_err}")
    config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    allp = gpd.GeoDataFrame(pd.concat(list(pois.values()), ignore_index=True), geometry="geometry", crs="EPSG:4326")
    allp.to_file(config.POI_CACHE, driver="GeoJSON")
    log.info("OSM POIs: %d hospitals, %d shelters, %d named places", len(pois["hospital"]), len(pois["shelter"]),
             len(pois["place"]))
    return pois


# --------------------------------------------------------------------------- #
# Risk on edges
# --------------------------------------------------------------------------- #
def _edge_geometry(G: nx.MultiDiGraph, u, v, data: dict) -> LineString:
    geom = data.get("geometry")
    if geom is not None:
        return geom
    return LineString([(G.nodes[u]["x"], G.nodes[u]["y"]), (G.nodes[v]["x"], G.nodes[v]["y"])])


def annotate_edges(G: nx.MultiDiGraph, stack: RasterStack, alpha: float = config.ROUTE_ALPHA) -> dict:
    """Set edge attributes ``risk`` (mean), ``risk_max`` and ``risk_cost = length * (1 + alpha * risk)``.

    Risk is sampled from the composite raster at points spaced
    ROUTE_EDGE_SAMPLE_STEP_M apart along each edge geometry. Edges that fall
    entirely on nodata take the raster mean.
    """
    risk = stack.arrays["risk"]
    rows, cols = risk.shape
    inv = ~stack.transform
    fill = float(np.nanmean(risk))
    n_nodata = 0
    for u, v, k, data in G.edges(keys=True, data=True):
        geom = _edge_geometry(G, u, v, data)
        length = float(data.get("length", 0.0)) or 1.0
        n_pts = max(2, int(math.ceil(length / config.ROUTE_EDGE_SAMPLE_STEP_M)) + 1)
        fr = np.linspace(0.0, 1.0, n_pts)
        pts = [geom.interpolate(f, normalized=True) for f in fr]
        c = np.array([inv * (p.x, p.y) for p in pts])
        ci, ri = np.floor(c[:, 0]).astype(int), np.floor(c[:, 1]).astype(int)
        ok = (ri >= 0) & (ri < rows) & (ci >= 0) & (ci < cols)
        vals = np.full(n_pts, np.nan)
        vals[ok] = risk[ri[ok], ci[ok]]
        if np.isnan(vals).all():
            mean_r = max_r = fill
            n_nodata += 1
        else:
            mean_r, max_r = float(np.nanmean(vals)), float(np.nanmax(vals))
        data["risk"] = mean_r
        data["risk_max"] = max_r
        data["risk_cost"] = length * (1.0 + alpha * mean_r)
    log.info("Risk sampled on %d edges (alpha=%.1f); %d edges outside the raster used the raster mean (%.3f)",
             len(G.edges), alpha, n_nodata, fill)
    return {"edges": len(G.edges), "nodata_edges": n_nodata, "fill": fill}


# --------------------------------------------------------------------------- #
# Origins: highest-risk settlement clusters
# --------------------------------------------------------------------------- #
def _haversine_m(lon1, lat1, lon2, lat2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * 6_371_000 * math.asin(math.sqrt(a))


def find_settlement_clusters(stack: RasterStack, n: int = config.N_ORIGINS) -> pd.DataFrame:
    """Rank settlement clusters (connected built-area blobs) by mean composite risk.

    The built-area threshold is lowered stepwise until at least ``n`` clusters,
    mutually separated by SETTLEMENT_MIN_SEPARATION_M, are available.
    """
    built = np.nan_to_num(stack.arrays["built"], nan=0.0)
    risk = np.nan_to_num(stack.arrays["risk"], nan=0.0)
    rows, cols = built.shape
    jj, ii = np.meshgrid(np.arange(cols), np.arange(rows))
    lon = stack.transform.c + (jj + 0.5) * stack.transform.a
    lat = stack.transform.f + (ii + 0.5) * stack.transform.e
    thr = config.SETTLEMENT_BUILT_THRESHOLD
    while thr >= config.SETTLEMENT_MIN_THRESHOLD - 1e-9:
        mask = built >= thr
        merged = ndimage.binary_dilation(mask, iterations=config.SETTLEMENT_MERGE_DILATION_PX) \
            if config.SETTLEMENT_MERGE_DILATION_PX > 0 else mask
        labels, n_lab = ndimage.label(merged, structure=np.ones((3, 3)))
        records = []
        for lab in range(1, n_lab + 1):
            m = (labels == lab) & mask
            cnt = int(m.sum())
            if cnt < config.SETTLEMENT_MIN_PIXELS:
                continue
            w = built[m]
            records.append({"pixels": cnt, "mean_risk": float(risk[m].mean()),
                            "lon": float(np.average(lon[m], weights=w)), "lat": float(np.average(lat[m], weights=w)),
                            "built_threshold": round(thr, 3)})
        records.sort(key=lambda r: r["mean_risk"], reverse=True)
        chosen = []
        for r in records:
            if all(_haversine_m(r["lon"], r["lat"], c["lon"], c["lat"]) >= config.SETTLEMENT_MIN_SEPARATION_M
                   for c in chosen):
                chosen.append(r)
            if len(chosen) == n:
                break
        if len(chosen) >= n:
            log.info("Settlement clusters: %d candidates at built threshold %.2f; top %d chosen by mean risk",
                     len(records), thr, n)
            return pd.DataFrame(chosen)
        thr = round(thr - config.SETTLEMENT_THRESHOLD_STEP, 6)
    raise RuntimeError(f"Could not find {n} separated settlement clusters; relax the SETTLEMENT_* settings in config.py")


def _nearest_place_name(lon: float, lat: float, places: gpd.GeoDataFrame) -> str:
    if places.empty:
        return ""
    d = [_haversine_m(lon, lat, g.x, g.y) for g in places.geometry]
    i = int(np.argmin(d))
    return places.iloc[i]["name"] if d[i] <= config.PLACE_NAME_MAX_DIST_M else ""


# --------------------------------------------------------------------------- #
# Dual Dijkstra
# --------------------------------------------------------------------------- #
def _best_edge(G: nx.MultiDiGraph, u, v, weight: str) -> dict:
    """The parallel edge Dijkstra would use for u->v under ``weight``."""
    return min(G[u][v].values(), key=lambda d: d.get(weight, float("inf")))


def _path_geometry(G: nx.MultiDiGraph, path: list, weight: str) -> LineString:
    coords: list[tuple[float, float]] = []
    for u, v in zip(path[:-1], path[1:]):
        data = _best_edge(G, u, v, weight)
        seg = list(_edge_geometry(G, u, v, data).coords)
        start = (G.nodes[u]["x"], G.nodes[u]["y"])
        if math.dist(seg[0], start) > math.dist(seg[-1], start):    # make the segment run u -> v
            seg.reverse()
        coords.extend(seg if not coords else seg[1:])
    return LineString(coords)


def path_metrics(G: nx.MultiDiGraph, path: list, weight: str) -> dict:
    """Distance (km), length-weighted mean risk, max risk and cumulative exposure of a path."""
    lengths, risks, rmax = [], [], []
    for u, v in zip(path[:-1], path[1:]):
        d = _best_edge(G, u, v, weight)
        lengths.append(float(d["length"]))
        risks.append(float(d["risk"]))
        rmax.append(float(d["risk_max"]))
    lengths, risks = np.array(lengths), np.array(risks)
    total = float(lengths.sum())
    return {"distance_km": total / 1000.0,
            "mean_risk": float((lengths * risks).sum() / total) if total > 0 else 0.0,
            "max_risk": float(max(rmax)) if rmax else 0.0,
            "exposure": float((lengths * risks).sum()) / 1000.0}


def compute_routes(G: nx.MultiDiGraph, origins: pd.DataFrame, hospitals: gpd.GeoDataFrame,
                   shelters: gpd.GeoDataFrame) -> tuple[gpd.GeoDataFrame, pd.DataFrame]:
    """Shortest and least-risk Dijkstra routes from every origin to its nearest hospital and shelter."""
    dest_sets = {}
    for kind, gdf in (("Hospital", hospitals), ("Shelter", shelters)):
        if gdf.empty:
            log.warning("No %s destinations available -- skipped", kind)
            continue
        nodes = ox.distance.nearest_nodes(G, X=gdf.geometry.x.values, Y=gdf.geometry.y.values)
        dest_sets[kind] = {int(nd): gdf.iloc[i]["name"] or f"{kind} (unnamed)" for i, nd in enumerate(nodes)}
    if not dest_sets:
        raise RuntimeError("No hospital or shelter destinations found in OSM for the study area")

    route_rows, metric_rows = [], []
    for _, o in origins.iterrows():
        o_node = int(o["node"])
        dist = nx.single_source_dijkstra_path_length(G, o_node, weight="length")
        for kind, dests in dest_sets.items():
            cand = {n: dist[n] for n in dests if n in dist and n != o_node}
            if not cand:
                log.warning("Origin %s cannot reach any %s", o["name"], kind)
                continue
            d_node = min(cand, key=cand.get)
            p_short = nx.dijkstra_path(G, o_node, d_node, weight="length")
            p_risk = nx.dijkstra_path(G, o_node, d_node, weight="risk_cost")
            ms = path_metrics(G, p_short, "length")
            mr = path_metrics(G, p_risk, "risk_cost")
            risk_red = 100 * (ms["mean_risk"] - mr["mean_risk"]) / ms["mean_risk"] if ms["mean_risk"] > 0 else 0.0
            expo_red = 100 * (ms["exposure"] - mr["exposure"]) / ms["exposure"] if ms["exposure"] > 0 else 0.0
            dist_inc = 100 * (mr["distance_km"] - ms["distance_km"]) / ms["distance_km"] if ms["distance_km"] > 0 else 0.0
            dname = dests[d_node]
            metric_rows.append({
                "origin_id": int(o["origin_id"]), "origin": o["name"], "destination_type": kind, "destination": dname,
                "shortest_km": round(ms["distance_km"], 3), "least_risk_km": round(mr["distance_km"], 3),
                "distance_increase_pct": round(dist_inc, 2),
                "shortest_mean_risk": round(ms["mean_risk"], 4), "least_risk_mean_risk": round(mr["mean_risk"], 4),
                "shortest_max_risk": round(ms["max_risk"], 4), "least_risk_max_risk": round(mr["max_risk"], 4),
                "mean_risk_reduction_pct": round(risk_red, 2), "exposure_reduction_pct": round(expo_red, 2),
                "same_route": p_short == p_risk})
            for rtype, path, met, w in (("shortest", p_short, ms, "length"), ("least_risk", p_risk, mr, "risk_cost")):
                route_rows.append({"origin_id": int(o["origin_id"]), "origin": o["name"], "destination_type": kind,
                                   "destination": dname, "route_type": rtype,
                                   "distance_km": round(met["distance_km"], 3), "mean_risk": round(met["mean_risk"], 4),
                                   "max_risk": round(met["max_risk"], 4), "geometry": _path_geometry(G, path, w)})
            log.info("Route %s -> %s '%s': shortest %.2f km (mean risk %.3f) | least-risk %.2f km (mean risk %.3f) "
                     "=> risk -%.1f%% for +%.1f%% distance", o["name"], kind, dname, ms["distance_km"], ms["mean_risk"],
                     mr["distance_km"], mr["mean_risk"], risk_red, dist_inc)
    routes = gpd.GeoDataFrame(route_rows, geometry="geometry", crs="EPSG:4326")
    metrics = pd.DataFrame(metric_rows)
    return routes, metrics


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def display_roads(G: nx.MultiDiGraph) -> gpd.GeoDataFrame:
    """Major roads with their mean edge risk, simplified for the web map."""
    edges = graph_edges(G)
    keep = edges["highway"].apply(lambda v: bool(highway_values(v) & set(config.ROAD_DISPLAY_CLASSES)))
    roads = edges[keep][["highway", "risk", "geometry"]].copy()
    roads["highway"] = roads["highway"].apply(lambda v: sorted(highway_values(v))[0])
    roads["risk"] = roads["risk"].astype(float).round(3)
    roads["geometry"] = roads.geometry.simplify(config.ROAD_DISPLAY_SIMPLIFY_DEG)
    return gpd.GeoDataFrame(roads, geometry="geometry", crs="EPSG:4326").reset_index(drop=True)


def run_routing(G: nx.MultiDiGraph, stack: RasterStack, study_area: BaseGeometry) -> RoutingResult:
    """Full routing workflow: annotate edges, pick origins/destinations, compute and save routes."""
    annotate_edges(G, stack)
    pois = load_pois(study_area)
    clusters = find_settlement_clusters(stack)
    nodes = ox.distance.nearest_nodes(G, X=clusters["lon"].values, Y=clusters["lat"].values)
    clusters["node"] = [int(n) for n in nodes]
    clusters["origin_id"] = np.arange(1, len(clusters) + 1)
    clusters["name"] = [
        (_nearest_place_name(r.lon, r.lat, pois["place"]) or f"Settlement cluster {r.origin_id}")
        for r in clusters.itertuples()]
    log.info("Origins (highest-risk settlement clusters):\n%s",
             clusters[["origin_id", "name", "pixels", "mean_risk", "lon", "lat"]].round(4).to_string(index=False))
    origins = gpd.GeoDataFrame(clusters, geometry=gpd.points_from_xy(clusters["lon"], clusters["lat"]),
                               crs="EPSG:4326")
    routes, metrics = compute_routes(G, clusters, pois["hospital"], pois["shelter"])
    if len(metrics) < 5:
        log.warning("Only %d origin-destination pairs routed (target >= 5)", len(metrics))
    config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    routes.to_file(config.ROUTES_GEOJSON, driver="GeoJSON")
    metrics.to_csv(config.ROUTE_METRICS_CSV, index=False)
    log.info("Routing done: %d routes (%d pairs) saved to %s and %s", len(routes), len(metrics),
             config.ROUTES_GEOJSON.name, config.ROUTE_METRICS_CSV.name)
    return RoutingResult(routes=routes, metrics=metrics, origins=origins, hospitals=pois["hospital"],
                         shelters=pois["shelter"], roads=display_roads(G), graph=G)
