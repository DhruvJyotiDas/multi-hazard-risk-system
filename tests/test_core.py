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
from src import hotspots, mcdm, routing, webmap  # noqa: E402
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
    webmap.build_map(stack, summary, result, hs, out_path=out)
    html = out.read_text(encoding="utf-8")
    assert config.MAP_TITLE in html and "RASTER_DATA" in html and "Route comparison" in html
