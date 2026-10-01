"""Offline unit tests: MCDM maths, Otsu, hotspots, routing and web-map generation.

Run with:  python -m pytest tests -q      (no Earth Engine account or internet needed)
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import config  # noqa: E402
import synthetic  # noqa: E402
from src import hotspots, mcdm, routing, webdata, webmap  # noqa: E402
from src.hazard_flood import otsu_threshold  # noqa: E402


def test_ahp_consistency_and_target_weights():
    res = mcdm.ahp_weights(config.AHP_PAIRWISE)
    assert res["cr"] < config.AHP_MAX_CR
    for w, crit in zip(res["weights"], config.CRITERIA):
        assert abs(w - config.AHP_TARGET_WEIGHTS[crit]) < 0.01
    assert res["weights"].sum() == pytest.approx(1.0)


def test_ahp_rejects_inconsistent_matrix():
    bad = [[1, 9, 1 / 9, 1], [1 / 9, 1, 9, 1], [9, 1 / 9, 1, 1], [1, 1, 1, 1]]
    with pytest.raises(AssertionError):
        mcdm.ahp_weights(bad)


def test_entropy_and_critic_weights_sum_to_one():
    rng = np.random.default_rng(0)
    x = rng.random((2000, 4)) ** np.array([1, 2, 4, 8])
    for w in (mcdm.entropy_weights(x), mcdm.critic_weights(x)):
        assert w.sum() == pytest.approx(1.0) and (w > 0).all()


def test_jenks_separates_modes():
    rng = np.random.default_rng(1)
    v = np.concatenate([rng.normal(0.2, 0.03, 500), rng.normal(0.5, 0.03, 500), rng.normal(0.8, 0.03, 500)])
    b = mcdm.jenks_breaks(v, 3)
    assert 0.3 < b[0] < 0.45 and 0.6 < b[1] < 0.75


def test_otsu_on_bimodal_backscatter():
    rng = np.random.default_rng(2)
    d = np.concatenate([rng.normal(-22, 1.5, 2000), rng.normal(-10, 2, 8000)])
    counts, edges = np.histogram(d, bins=200)
    thr = otsu_threshold(counts, (edges[:-1] + edges[1:]) / 2)
    assert -19 < thr < -13


def test_hotspots_and_stats():
    stack = synthetic.make_stack()
    gdf = hotspots.extract_hotspots(stack)
    assert len(gdf) >= 1 and (gdf["area_km2"] >= config.HOTSPOT_MIN_AREA_KM2).all()
    assert hotspots.hotspot_summary(gdf, 500.0)["total_area_km2"] > 0


def test_least_risk_route_trades_distance_for_lower_risk():
    stack = synthetic.make_stack()
    stack.arrays["risk"][:, 100:120] = 0.95          # impassable-ish band ...
    stack.arrays["risk"][60:70, 100:120] = 0.05      # ... with a low-risk gap
    g = synthetic.make_graph()
    routing.annotate_edges(g, stack)
    origin = int(routing.ox.distance.nearest_nodes(g, X=[76.03], Y=[11.72])[0])
    import pandas as pd
    origins = pd.DataFrame([{"origin_id": 1, "name": "T", "node": origin}])
    hosp, shel = synthetic.make_pois()
    routes, metrics = routing.compute_routes(g, origins, hosp, shel)
    assert len(metrics) == 2 and len(routes) == 4
    row = metrics.iloc[0]
    assert row["least_risk_mean_risk"] <= row["shortest_mean_risk"]
    assert row["least_risk_km"] >= row["shortest_km"] - 1e-9


def test_webmap_builds_standalone_html(tmp_path):
    import geopandas as gpd
    import pandas as pd
    stack = synthetic.make_stack()
    g = synthetic.make_graph()
    routing.annotate_edges(g, stack)
    clusters = routing.find_settlement_clusters(stack, n=2)
    clusters["node"] = [int(n) for n in routing.ox.distance.nearest_nodes(g, X=clusters["lon"].values,
                                                                          Y=clusters["lat"].values)]
    clusters["origin_id"] = range(1, len(clusters) + 1)
    clusters["name"] = [f"Village {i}" for i in clusters["origin_id"]]
    hosp, shel = synthetic.make_pois()
    routes, metrics = routing.compute_routes(g, clusters, hosp, shel)
    origins = gpd.GeoDataFrame(clusters, geometry=gpd.points_from_xy(clusters["lon"], clusters["lat"]), crs=4326)
    result = routing.RoutingResult(routes, metrics, origins, hosp, shel, routing.display_roads(g))
    result.graph = g
    facilities = [{"cat": "health", "sub": "hospital", "name": "Test Hospital", "lon": 76.05, "lat": 11.72},
                  {"cat": "place", "sub": "town", "name": "Testville", "lon": 76.1, "lat": 11.7}]
    hs = hotspots.extract_hotspots(stack)
    summary = {
        "study_area_km2": 500.0, "breaks": stack.breaks,
        "stats": [{"class_id": i + 1, "class_name": n, "index_min": 0.0, "index_max": 1.0, "area_km2": 1.0,
                   "percent_of_study_area": 20.0} for i, n in enumerate(config.RISK_CLASS_NAMES)],
        "weight_table": {c: {"AHP": .25, "Entropy": .25, "AHP+Entropy (used)": .25, "CRITIC": .25}
                         for c in config.CRITERIA},
        "ahp": {"cr": 0.019, "lambda_max": 4.05}, "hotspots": hotspots.hotspot_summary(hs, 500.0),
        "validation": None}
    out = tmp_path / "index.html"
    webmap.build_map(stack, summary, result, hs, out_path=out, facilities=facilities)
    html = out.read_text(encoding="utf-8")
    assert config.MAP_TITLE in html and "RASTER_DATA" in html and "Route comparison" in html
    assert "FACILITY_DATA" in html and "WEBGRAPH" in html and 'id="fac-master"' in html and 'id="srch-from"' in html
    assert "Test Hospital" in html


def test_facilities_payload_indexes_categories():
    facs = [{"cat": "health", "sub": "hospital", "name": "H", "lon": 76.0, "lat": 11.6},
            {"cat": "place", "sub": "town", "name": "T", "lon": 76.1, "lat": 11.7},
            {"cat": "unknown", "sub": "", "name": "X", "lon": 76.2, "lat": 11.8}]
    payload = webdata.facilities_payload(facs)
    keys = [c["key"] for c in payload["cats"]]
    assert keys[-1] == "place" and "health" in keys
    assert len(payload["pts"]) == 2                      # unknown categories are dropped
    assert payload["cats"][keys.index("health")]["count"] == 1


def test_webgraph_contracts_chains_and_preserves_length():
    import base64
    g = synthetic.make_graph(n=6)
    stack = synthetic.make_stack()
    routing.annotate_edges(g, stack)
    # turn one row into a long degree-2 chain by deleting the cross links around it
    wg = webdata.build_webgraph(g)
    dec = lambda key, dt: np.frombuffer(base64.b64decode(wg[key]), dtype=dt)
    assert wg["n"] <= len(g.nodes) and wg["e"] <= len(g.edges) // 2
    assert len(dec("nodes", "<i4")) == 2 * wg["n"] and len(dec("goff", "<u4")) == wg["e"] + 1
    lengths = dec("len", "<u4")
    undirected_total = sum(d["length"] for _, _, d in g.edges(data=True)) / 2
    assert lengths.sum() == pytest.approx(undirected_total, rel=0.01)   # contraction conserves road length
    assert dec("risk", "u1").max() <= 254


def test_webgraph_contraction_merges_a_pure_chain():
    import networkx as nx
    g = nx.MultiDiGraph(crs="EPSG:4326")
    for i in range(5):
        g.add_node(i, x=76.0 + i * 0.001, y=11.7)
    for i in range(4):
        for a, b in ((i, i + 1), (i + 1, i)):
            g.add_edge(a, b, length=100.0, highway="residential", risk=0.5 if i < 2 else 0.1)
    wg = webdata.build_webgraph(g)
    assert (wg["n"], wg["e"]) == (2, 1)                  # 5 nodes in a line collapse into one segment
    import base64
    assert np.frombuffer(base64.b64decode(wg["len"]), dtype="<u4")[0] == 400
    assert np.frombuffer(base64.b64decode(wg["risk"]), dtype="u1")[0] == round(0.3 * 254)  # length-weighted risk
