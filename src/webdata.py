"""Compact data payloads embedded in index.html for the facilities layer and the in-browser route search.

Purpose : let the standalone map (no server, no API) show every facility and road, and answer "what is between
          place A and place B?" entirely client-side.
Method  :
  * Facilities -- the categorised OSM features from ``osm_pbf.scan_facilities`` packed as short arrays.
  * Road graph -- the annotated drive network (edge ``length`` and sampled composite ``risk``) is reduced to an
    undirected junction-to-junction graph: parallel edges keep the shorter one, degree-2 nodes are contracted
    into polylines (length summed, risk length-weighted, best road class kept), and geometry is simplified.
    The result is encoded as base64 typed arrays (int32 coordinates x 1e5, uint32 lengths, uint8 risk/class)
    that the browser decodes into CSR adjacency for Dijkstra with nearest-road snapping.
  Directions (one-way streets) are ignored in the browser router: it answers "which facilities lie along a
  plausible road route", not turn-by-turn navigation.
Reference: Dijkstra 1959; graph contraction of degree-2 nodes as in OSMnx ``simplify_graph``
          (Boeing 2017, *Computers, Environment and Urban Systems* 65).
"""
from __future__ import annotations

import base64
import logging
from collections import deque

import networkx as nx
import numpy as np
from shapely.geometry import LineString

import config
from src.exposure import highway_values

log = logging.getLogger(__name__)

# smaller rank = more important road; classes: 0 major, 1 minor, 2 local (used for display styling)
_RANK = {"motorway": 0, "trunk": 0, "primary": 0, "secondary": 1, "tertiary": 2, "unclassified": 3,
         "residential": 3, "living_street": 3, "service": 4, "road": 4}
_COORD_SCALE = 1e5


def _road_rank(value) -> int:
    ranks = [_RANK.get(h.replace("_link", ""), 4) for h in highway_values(value)]
    return min(ranks) if ranks else 4


def _class_code(rank: int) -> int:
    return 0 if rank <= 0 else 1 if rank <= 2 else 2


def _b64(arr: np.ndarray, dtype: str) -> str:
    return base64.b64encode(np.ascontiguousarray(arr, dtype=dtype).tobytes()).decode("ascii")


def facilities_payload(facilities: list[dict]) -> dict:
    """{cats: [{key,label,color,count}], pts: [[lon, lat, catIndex, name, sub], ...]} for the browser."""
    cats = [{"key": k, "label": v["label"], "color": v["color"]} for k, v in config.FACILITY_CATEGORIES.items()]
    cats.append({"key": "place", **config.PLACE_CATEGORY})
    index = {c["key"]: i for i, c in enumerate(cats)}
    pts = [[f["lon"], f["lat"], index[f["cat"]], f["name"], f["sub"]] for f in facilities if f["cat"] in index]
    for i, c in enumerate(cats):
        c["count"] = sum(1 for p in pts if p[2] == i)
    return {"cats": cats, "pts": pts}


def _undirected(G: nx.MultiDiGraph) -> nx.Graph:
    """Undirected simple graph keeping, per node pair, the shorter edge (with its risk and road rank)."""
    H = nx.Graph()
    for u, v, d in G.edges(data=True):
        if u == v:
            continue
        length = float(d.get("length", 0.0))
        if length <= 0:
            continue
        if H.has_edge(u, v) and H[u][v]["length"] <= length:
            continue
        for n in (u, v):
            if n not in H:
                H.add_node(n, x=float(G.nodes[n]["x"]), y=float(G.nodes[n]["y"]))
        H.add_edge(u, v, length=length, risk=float(d.get("risk", 0.0)), rank=_road_rank(d.get("highway")),
                   a=u, pts=[(H.nodes[u]["x"], H.nodes[u]["y"]), (H.nodes[v]["x"], H.nodes[v]["y"])])
    return H


def _oriented(edge: dict, start) -> list[tuple[float, float]]:
    return edge["pts"] if edge["a"] == start else edge["pts"][::-1]


def _contract(H: nx.Graph) -> nx.Graph:
    """Contract every degree-2 node into its neighbours' edge (bounded: each step removes a node)."""
    queue = deque(n for n in H.nodes if H.degree(n) == 2)
    while queue:
        n = queue.popleft()
        if n not in H or H.degree(n) != 2:
            continue
        a, b = list(H[n])
        if a == b:
            continue
        e1, e2 = H[a][n], H[n][b]
        length = e1["length"] + e2["length"]
        merged = {"length": length, "risk": (e1["length"] * e1["risk"] + e2["length"] * e2["risk"]) / length,
                  "rank": min(e1["rank"], e2["rank"]), "a": a, "pts": _oriented(e1, a) + _oriented(e2, n)[1:]}
        H.remove_node(n)
        if not (H.has_edge(a, b) and H[a][b]["length"] <= length):
            H.add_edge(a, b, **merged)
        queue.append(a)
        queue.append(b)
    return H


def build_webgraph(G: nx.MultiDiGraph) -> dict:
    """Encode the annotated drive graph as the compact payload described in the module docstring."""
    H = _contract(_undirected(G))
    if H.number_of_nodes() == 0:
        raise ValueError("road graph is empty")
    largest = max(nx.connected_components(H), key=len)
    H = H.subgraph(largest).copy()
    ids = {n: i for i, n in enumerate(H.nodes)}
    nodes = np.zeros((len(ids), 2), dtype=np.int64)
    for n, i in ids.items():
        nodes[i] = (round(H.nodes[n]["x"] * _COORD_SCALE), round(H.nodes[n]["y"] * _COORD_SCALE))
    eu, ev, elen, erisk, ecls, goff, gxy = [], [], [], [], [], [0], []
    for u, v, d in H.edges(data=True):
        pts = _oriented(d, u)
        simple = list(LineString(pts).simplify(config.WEBGRAPH_SIMPLIFY_DEG, preserve_topology=False).coords)
        if len(simple) < 2:
            simple = [pts[0], pts[-1]]
        eu.append(ids[u])
        ev.append(ids[v])
        elen.append(round(d["length"]))
        erisk.append(min(254, max(0, round(d["risk"] * 254))))
        ecls.append(_class_code(d["rank"]))
        gxy.extend((round(x * _COORD_SCALE), round(y * _COORD_SCALE)) for x, y in simple)
        goff.append(len(gxy))
    log.info("Web road graph: %d junctions, %d road segments, %d geometry vertices (from %d nodes / %d edges)",
             len(ids), len(eu), len(gxy), len(G.nodes), len(G.edges))
    return {"n": len(ids), "e": len(eu), "nodes": _b64(nodes.reshape(-1), "<i4"), "eu": _b64(np.array(eu), "<u4"),
            "ev": _b64(np.array(ev), "<u4"), "len": _b64(np.array(elen), "<u4"), "risk": _b64(np.array(erisk), "u1"),
            "cls": _b64(np.array(ecls), "u1"), "goff": _b64(np.array(goff), "<u4"),
            "gxy": _b64(np.array(gxy, dtype=np.int64).reshape(-1), "<i4"), "scale": _COORD_SCALE}
