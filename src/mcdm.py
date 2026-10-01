"""Multi-criteria decision making: AHP, entropy, CRITIC, weighted overlay, reclassification.

Purpose : fuse the normalised flood, landslide, exposure and fire layers into one
          composite risk index and 5 risk classes.
Method  :
  (a) AHP -- principal eigenvector of a Saaty pairwise matrix, with the
      consistency ratio CR = CI / RI asserted < 0.1;
  (b) Shannon-entropy objective weights from the actual raster samples;
  (c) combined weights = mean(AHP, entropy);
  (d) CRITIC objective weights (contrast intensity + inter-criteria conflict)
      reported alongside as a sensitivity comparison;
  (e) weighted linear combination of the 0-1 layers -> composite index (0-1);
  (f) reclassification into 5 classes by quantiles or Fisher-Jenks natural
      breaks, with per-class area statistics (km2, % of study area).
References:
  * Saaty 1980, *The Analytic Hierarchy Process* -- AHP and consistency ratio.
  * Al-Aomar 2010, *Int. J. Production Economics* -- combined AHP-entropy.
  * Zhao et al. 2017, *Entropy* -- AHP + entropy gives more reliable
    susceptibility maps than either alone.
  * Mukhametzyanov 2021, *Decision Making: Applications in Management and
    Engineering* 4 -- CRITIC / objective weighting comparison.
  * Skilodimou et al. 2019 -- multi-hazard weighted overlay and
    class reclassification; Lyu & Yin 2023 -- interval-FAHP-GIS (upgrade path).
  * Jenks 1967 / Fisher 1958 -- natural-breaks optimal classification.
"""
from __future__ import annotations

import json
import logging

import ee
import numpy as np
import pandas as pd

import config
from src.utils import minmax_stats, normalize_minmax

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Weighting methods (pure numpy -- unit-testable without Earth Engine)
# --------------------------------------------------------------------------- #
def ahp_weights(matrix, max_cr: float = config.AHP_MAX_CR) -> dict:
    """AHP priority vector, lambda_max, CI and CR of a reciprocal pairwise matrix.

    Raises AssertionError if CR >= max_cr (Saaty's acceptance threshold is 0.1).
    """
    a = np.asarray(matrix, dtype=float)
    n = a.shape[0]
    if a.shape != (n, n) or not np.allclose(a * a.T, 1.0, atol=1e-6):
        raise ValueError("AHP matrix must be square and reciprocal (a_ij * a_ji = 1)")
    vals, vecs = np.linalg.eig(a)
    k = int(np.argmax(vals.real))
    lam = float(vals.real[k])
    w = np.abs(vecs[:, k].real)
    w = w / w.sum()
    ci = (lam - n) / (n - 1) if n > 1 else 0.0
    ri = config.AHP_RANDOM_INDEX[n]
    cr = ci / ri if ri > 0 else 0.0
    assert cr < max_cr, f"AHP consistency ratio {cr:.3f} >= {max_cr}: revise the pairwise judgements"
    return {"weights": w, "lambda_max": lam, "ci": float(ci), "cr": float(cr)}


def entropy_weights(x: np.ndarray) -> np.ndarray:
    """Shannon-entropy objective weights from an (n_samples, n_criteria) matrix of 0-1 values.

    A criterion whose values are nearly uniform carries little information
    (entropy -> 1, weight -> 0); a more dispersed one gets more weight.
    """
    x = np.clip(np.asarray(x, dtype=float), 0, None) + config.MIN_WEIGHT_FLOOR
    n = x.shape[0]
    p = x / x.sum(axis=0, keepdims=True)
    e = -(p * np.log(p)).sum(axis=0) / np.log(n)
    d = 1.0 - e
    return d / d.sum() if d.sum() > 0 else np.full(x.shape[1], 1.0 / x.shape[1])


def critic_weights(x: np.ndarray) -> np.ndarray:
    """CRITIC objective weights: C_j = sigma_j * sum_k (1 - r_jk), normalised."""
    x = np.asarray(x, dtype=float)
    sigma = x.std(axis=0, ddof=1)
    r = np.corrcoef(x, rowvar=False)
    r = np.nan_to_num(r, nan=0.0)
    c = sigma * (1.0 - r).sum(axis=0)
    c = np.clip(c, config.MIN_WEIGHT_FLOOR, None)
    return c / c.sum()


def combine_weights(*weight_sets: np.ndarray) -> np.ndarray:
    """Arithmetic mean of weight vectors, renormalised to sum to 1."""
    w = np.mean(np.vstack(weight_sets), axis=0)
    return w / w.sum()


def jenks_breaks(values: np.ndarray, k: int = 5, max_points: int = 1500) -> list[float]:
    """Fisher-Jenks natural-breaks lower bounds for classes 2..k (k-1 values).

    Large inputs are reduced to ``max_points`` evenly spaced order statistics, which
    preserves the distribution shape while keeping the O(k n^2) DP fast.
    """
    v = np.sort(np.asarray(values, dtype=float))
    v = v[np.isfinite(v)]
    if v.size > max_points:
        v = np.quantile(v, np.linspace(0, 1, max_points))
    n = v.size
    if n < k:
        raise ValueError("not enough values for natural breaks")
    s1 = np.concatenate([[0.0], np.cumsum(v)])
    s2 = np.concatenate([[0.0], np.cumsum(v ** 2)])

    def cost(i: np.ndarray, j: int) -> np.ndarray:        # SSD of v[i..j] (inclusive), vectorised in i
        cnt = j - i + 1
        s = s1[j + 1] - s1[i]
        return s2[j + 1] - s2[i] - s * s / cnt

    inf = np.inf
    dp = np.full((k, n), inf)
    arg = np.zeros((k, n), dtype=int)
    for j in range(n):
        dp[0, j] = cost(np.array([0]), j)[0]
    for c in range(1, k):
        for j in range(c, n):
            i = np.arange(c, j + 1)
            tot = dp[c - 1, i - 1] + cost(i, j)
            m = int(np.argmin(tot))
            dp[c, j] = tot[m]
            arg[c, j] = i[m]
    breaks, j = [], n - 1
    for c in range(k - 1, 0, -1):
        start = arg[c, j]
        breaks.append(float(v[start]))
        j = start - 1
    return sorted(breaks)


# --------------------------------------------------------------------------- #
# Raster-side operations (Earth Engine)
# --------------------------------------------------------------------------- #
def sample_layers(layers: dict[str, ee.Image], aoi: ee.Geometry) -> np.ndarray:
    """Randomly sample the criteria layers inside the AOI -> (n, n_criteria) array in config.CRITERIA order."""
    stack = ee.Image.cat([layers[c].rename(c) for c in config.CRITERIA])
    fc = stack.sample(region=aoi, scale=config.STATS_SCALE, numPixels=config.WEIGHT_SAMPLE_PIXELS,
                      seed=config.WEIGHT_SAMPLE_SEED, dropNulls=True, tileScale=config.EE_TILE_SCALE)
    cols = ee.Dictionary({c: fc.aggregate_array(c) for c in config.CRITERIA}).getInfo()
    arr = np.column_stack([np.asarray(cols[c], dtype=float) for c in config.CRITERIA])
    if arr.shape[0] < 100:
        raise RuntimeError(f"Only {arr.shape[0]} valid samples -- cannot derive objective weights")
    log.info("  sampled %d pixels for entropy/CRITIC weights", arr.shape[0])
    return arr


def compute_weights(samples: np.ndarray) -> tuple[pd.DataFrame, dict]:
    """Compute AHP, entropy, CRITIC and combined weights; print the side-by-side table."""
    ahp = ahp_weights(config.AHP_PAIRWISE)
    log.info("AHP: lambda_max=%.4f  CI=%.4f  CR=%.4f  -> %s (< %.2f)", ahp["lambda_max"], ahp["ci"], ahp["cr"],
             "CONSISTENT" if ahp["cr"] < config.AHP_MAX_CR else "INCONSISTENT", config.AHP_MAX_CR)
    ent = entropy_weights(samples)
    crit = critic_weights(samples)
    combined = combine_weights(ahp["weights"], ent)
    table = pd.DataFrame({
        "criterion": config.CRITERIA,
        "target_AHP": [config.AHP_TARGET_WEIGHTS[c] for c in config.CRITERIA],
        "AHP": ahp["weights"], "Entropy": ent, "AHP+Entropy (used)": combined, "CRITIC": crit,
    }).set_index("criterion")
    log.info("MCDM weights (side by side):\n%s", table.round(4).to_string())
    config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    table.round(6).to_csv(config.WEIGHTS_CSV)
    return table, ahp


def weighted_overlay(layers: dict[str, ee.Image], weights: dict[str, float], aoi: ee.Geometry,
                     bounds: tuple[float, float] | None = None) -> tuple[ee.Image, tuple[float, float]]:
    """Weighted linear combination of 0-1 layers, min-max rescaled to the full 0-1 range (band 'risk').

    Returns (risk image, (lo, hi) used for the rescaling) so a resumed run can skip the statistics call.
    """
    total = None
    for c in config.CRITERIA:
        term = layers[c].unmask(0).multiply(weights[c])
        total = term if total is None else total.add(term)
    total = total.rename("risk_raw").clip(aoi)
    bounds = bounds or minmax_stats(total, aoi, "risk_raw")
    return normalize_minmax(total, aoi, "risk_raw", "risk", bounds=bounds), bounds


def risk_from_layers(arrays: dict, weights: dict[str, float], bounds: tuple[float, float]):
    """Client-side composite on exported rasters: identical to ``weighted_overlay`` (a linear map), but
    avoids exporting the deep Earth Engine composite graph. Returns the 0-1 risk array (NaN outside the AOI)."""
    import numpy as np
    total = sum(np.nan_to_num(arrays[c], nan=0.0) * weights[c] for c in config.CRITERIA)
    lo, hi = bounds
    risk = np.clip((total - lo) / (hi - lo), 0.0, 1.0).astype("float32")
    outside = np.isnan(arrays[config.CRITERIA[0]])
    return np.where(outside, np.nan, risk).astype("float32")


def class_breaks(risk: ee.Image, aoi: ee.Geometry) -> list[float]:
    """Four class boundaries (for 5 classes) by quantiles or natural breaks per config."""
    if config.RISK_CLASSIFICATION == "natural_breaks":
        vals = risk.sample(region=aoi, scale=config.STATS_SCALE, numPixels=config.WEIGHT_SAMPLE_PIXELS,
                           seed=config.WEIGHT_SAMPLE_SEED, dropNulls=True,
                           tileScale=config.EE_TILE_SCALE).aggregate_array("risk").getInfo()
        return jenks_breaks(np.asarray(vals, dtype=float), k=len(config.RISK_CLASS_NAMES))
    pct = [100 * i / len(config.RISK_CLASS_NAMES) for i in range(1, len(config.RISK_CLASS_NAMES))]
    res = risk.reduceRegion(ee.Reducer.percentile(pct), geometry=aoi, scale=config.STATS_SCALE,
                            maxPixels=config.EE_MAX_PIXELS, bestEffort=True, tileScale=config.EE_TILE_SCALE).getInfo()
    return [float(res[f"risk_p{int(p)}"]) for p in pct]


def classify(risk: ee.Image, breaks: list[float], aoi: ee.Geometry) -> ee.Image:
    """Classes 1 (Very Low) .. 5 (Very High) from class boundaries."""
    cls = ee.Image.constant(1)
    for b in breaks:
        cls = cls.add(risk.gte(b))
    return cls.rename("risk_class").clip(aoi).toInt8()


def class_statistics(class_img: ee.Image, aoi: ee.Geometry, breaks: list[float]) -> pd.DataFrame:
    """Area (km2) and share (%) of the study area per risk class; exported to outputs/risk_stats.csv."""
    area = ee.Image.pixelArea().divide(1e6).rename("area")
    grouped = area.addBands(class_img).reduceRegion(
        reducer=ee.Reducer.sum().group(groupField=1, groupName="cls"), geometry=aoi,
        scale=config.ANALYSIS_SCALE, maxPixels=config.EE_MAX_PIXELS, bestEffort=True,
        tileScale=config.EE_TILE_SCALE).getInfo()
    sums = {int(g["cls"]): float(g["sum"]) for g in grouped.get("groups", [])}
    total = sum(sums.values())
    edges = [0.0] + list(breaks) + [1.0]
    rows = []
    for i, name in enumerate(config.RISK_CLASS_NAMES, start=1):
        a = sums.get(i, 0.0)
        rows.append({"class_id": i, "class_name": name, "index_min": round(edges[i - 1], 4),
                     "index_max": round(edges[i], 4), "area_km2": round(a, 3),
                     "percent_of_study_area": round(100 * a / total, 2) if total else 0.0})
    df = pd.DataFrame(rows)
    config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    df.to_csv(config.RISK_STATS_CSV, index=False)
    log.info("Risk-class statistics (total %.1f km2) saved to %s:\n%s", total, config.RISK_STATS_CSV,
             df.to_string(index=False))
    return df


CHECKPOINT_JSON = config.OUTPUT_DIR / "checkpoint_mcdm.json"


def build_composite(layers: dict[str, ee.Image], aoi: ee.Geometry, resume: bool = False) -> dict:
    """Run the full MCDM chain and return weights, composite index, classes, breaks and stats.

    The expensive statistics (weights, class breaks, class areas) are saved to
    ``outputs/checkpoint_mcdm.json``; with ``resume=True`` they are reloaded instead of recomputed.
    """
    if resume and CHECKPOINT_JSON.exists():
        ck = json.loads(CHECKPOINT_JSON.read_text(encoding="utf-8"))
        log.info("Resuming MCDM from %s (weights, breaks and statistics are not recomputed)", CHECKPOINT_JSON.name)
        weights, breaks = ck["weights"], ck["breaks"]
        bounds = tuple(ck["risk_bounds"]) if ck.get("risk_bounds") else None   # None -> recomputed (1 call)
        risk, bounds = weighted_overlay(layers, weights, aoi, bounds=bounds)
        if not ck.get("risk_bounds"):
            ck["risk_bounds"] = list(bounds)
            CHECKPOINT_JSON.write_text(json.dumps(ck, indent=2, default=float), encoding="utf-8")
        class_img = classify(risk, breaks, aoi)
        table = pd.DataFrame(ck["weight_table"]).T
        return {"risk": risk, "class_image": class_img, "breaks": breaks, "stats": pd.DataFrame(ck["stats"]),
                "weights": weights, "weight_table": table, "ahp": ck["ahp"], "risk_bounds": tuple(bounds)}
    samples = sample_layers(layers, aoi)
    table, ahp = compute_weights(samples)
    weights = {c: float(table.loc[c, "AHP+Entropy (used)"]) for c in config.CRITERIA}
    log.info("Combined weights used: %s", {k: round(v, 4) for k, v in weights.items()})
    risk, bounds = weighted_overlay(layers, weights, aoi)
    breaks = class_breaks(risk, aoi)
    log.info("Risk class breaks (%s): %s", config.RISK_CLASSIFICATION, [round(b, 4) for b in breaks])
    class_img = classify(risk, breaks, aoi)
    stats = class_statistics(class_img, aoi, breaks)
    ahp_out = {k: v for k, v in ahp.items() if k != "weights"}
    CHECKPOINT_JSON.write_text(json.dumps({
        "weights": weights, "breaks": breaks, "risk_bounds": list(bounds), "stats": stats.to_dict(orient="records"),
        "weight_table": table.to_dict(orient="index"), "ahp": ahp_out}, indent=2, default=float), encoding="utf-8")
    return {"risk": risk, "class_image": class_img, "breaks": breaks, "stats": stats,
            "weights": weights, "weight_table": table, "ahp": ahp, "risk_bounds": tuple(bounds)}
