#!/usr/bin/env python3
"""Estimate missing LA County building heights with a GPU gradient-boosted model.

Input: the gzip GeoJSON pages written by science/pipeline/capture_county_buildings.py
(EPSG:26911, HEIGHT in feet). Features per footprint: area, perimeter,
compactness, vertex count, minimum-rectangle aspect, vintage year, centroid,
and neighbourhood statistics of *known* heights (median/mean/count within
50 m and 150 m, nearest-neighbour distance). Neighbour statistics always
exclude the building itself.

Validation holds out whole 2 km blocks (20%) so that a building's neighbours
can't leak its own height into the test. The model is compared with the
baseline the county tile builder uses today (median known height within
100 m, else 4 m). Output: predictions for null-height buildings plus a
metrics JSON; use the model only if it beats the baseline on held-out blocks.

Usage (on the PC, GPU):
  python building_height_model.py --pages D:\\quietla\\buildings\\pages --out D:\\quietla\\buildings\\model_v1 --device cuda
"""
from __future__ import annotations

import argparse
import gzip
import json
import math
import time
from pathlib import Path

import numpy as np
import shapely
import xgboost as xgb
from shapely.geometry import shape

FT = 0.3048


def load(pages: Path):
    ids, bld, height, year, geoms = [], [], [], [], []
    for p in sorted(pages.glob("page_*.geojson.gz")):
        for f in json.loads(gzip.decompress(p.read_bytes()))["features"]:
            g = f.get("geometry")
            if not g:
                continue
            props = f["properties"]
            h = props.get("HEIGHT")
            ids.append(props["OBJECTID"])
            bld.append(props.get("BLD_ID") or "")
            height.append(h * FT if isinstance(h, (int, float)) and h > 0 else np.nan)
            try:
                year.append(float(props.get("DATE_") or "nan"))
            except ValueError:
                year.append(np.nan)
            geoms.append(shape(g))
    return np.array(ids), np.array(bld), np.array(height, dtype=float), np.array(year, dtype=float), np.array(geoms, dtype=object)


def neighbour_stats(cx, cy, height, radius):
    """median / mean / count of known heights within radius, excluding self."""
    pts = shapely.points(cx, cy)
    tree = shapely.STRtree(pts)
    known = ~np.isnan(height)
    med = np.full(len(cx), np.nan)
    mean = np.full(len(cx), np.nan)
    count = np.zeros(len(cx))
    left, right = tree.query(shapely.buffer(pts, radius), predicate="intersects")
    keep = (left != right) & known[right]
    left, right = left[keep], right[keep]
    order = np.argsort(left, kind="stable")
    left, right = left[order], right[order]
    bounds = np.searchsorted(left, np.arange(len(cx) + 1))
    vals = height[right]
    for i in range(len(cx)):
        a, b = bounds[i], bounds[i + 1]
        if b > a:
            v = vals[a:b]
            med[i], mean[i], count[i] = np.median(v), v.mean(), b - a
    return med, mean, count


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--pages", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--device", default="cuda")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    ids, bld, height, year, geoms = load(args.pages)
    print(f"loaded {len(ids)} footprints, {np.isnan(height).sum()} without height ({time.time() - t0:.0f}s)", flush=True)

    area = shapely.area(geoms)
    perim = shapely.length(geoms)
    compact = 4 * math.pi * area / np.maximum(perim, 1e-6) ** 2
    nverts = shapely.get_num_coordinates(geoms).astype(float)
    rect = shapely.minimum_rotated_rectangle(geoms)
    coords = [np.asarray(r.exterior.coords) if r.geom_type == "Polygon" else np.zeros((5, 2)) for r in rect]
    sides = np.array([[np.hypot(*(c[1] - c[0])), np.hypot(*(c[2] - c[1]))] for c in coords])
    aspect = sides.max(1) / np.maximum(sides.min(1), 1e-6)
    cent = shapely.centroid(geoms)
    cx, cy = shapely.get_x(cent), shapely.get_y(cent)
    med50, mean50, n50 = neighbour_stats(cx, cy, height, 50.0)
    med150, mean150, n150 = neighbour_stats(cx, cy, height, 150.0)
    med100, _, _ = neighbour_stats(cx, cy, height, 100.0)
    nn_tree = shapely.STRtree(cent)
    _, nn_dist = nn_tree.query_nearest(cent, exclusive=True, return_distance=True, all_matches=False)
    print(f"features built ({time.time() - t0:.0f}s)", flush=True)

    X = np.column_stack([np.log1p(area), np.log1p(perim), compact, nverts, aspect, year, cx, cy,
                         med50, mean50, n50, med150, mean150, n150, np.log1p(nn_dist)])
    names = ["log_area", "log_perimeter", "compactness", "vertices", "aspect", "year", "x", "y",
             "med50", "mean50", "n50", "med150", "mean150", "n150", "log_nn_dist"]
    known = ~np.isnan(height)
    block = (np.floor(cx / 2000).astype(np.int64) * 100003 + np.floor(cy / 2000).astype(np.int64))
    rng = np.random.default_rng(20261004)
    blocks = np.unique(block[known])
    test_blocks = set(rng.choice(blocks, size=max(1, len(blocks) // 5), replace=False).tolist())
    is_test = np.array([b in test_blocks for b in block]) & known
    is_train = known & ~is_test

    y = np.log(height)
    params = {"objective": "reg:squarederror", "tree_method": "hist", "device": args.device, "max_depth": 9,
              "learning_rate": 0.05, "subsample": 0.8, "colsample_bytree": 0.8, "min_child_weight": 5, "eval_metric": "mae"}
    dtrain = xgb.DMatrix(X[is_train], label=y[is_train], feature_names=names)
    dtest = xgb.DMatrix(X[is_test], label=y[is_test], feature_names=names)
    t1 = time.time()
    booster = xgb.train(params, dtrain, num_boost_round=2000, evals=[(dtest, "heldout")], early_stopping_rounds=50, verbose_eval=200)
    train_s = time.time() - t1
    pred_test = np.exp(booster.predict(dtest, iteration_range=(0, booster.best_iteration + 1)))
    base_test = np.where(np.isnan(med100[is_test]), 4.0, med100[is_test])
    truth = height[is_test]

    def stats(p):
        err = p - truth
        return {"mae_m": float(np.mean(np.abs(err))), "rmse_m": float(np.sqrt(np.mean(err ** 2))),
                "median_abs_m": float(np.median(np.abs(err))), "bias_m": float(np.mean(err)),
                "within_1_storey_3m": float(np.mean(np.abs(err) <= 3.0))}
    metrics = {"buildings": int(len(ids)), "known_heights": int(known.sum()), "missing_heights": int((~known).sum()),
               "heldout_blocks": len(test_blocks), "heldout_buildings": int(is_test.sum()), "train_buildings": int(is_train.sum()),
               "device": args.device, "train_seconds": round(train_s, 1), "best_iteration": int(booster.best_iteration),
               "model": stats(pred_test), "baseline_median100": stats(base_test),
               "feature_importance_gain": booster.get_score(importance_type="gain")}
    metrics["use_model"] = metrics["model"]["mae_m"] < metrics["baseline_median100"]["mae_m"]
    missing = ~known
    pred_missing = np.exp(booster.predict(xgb.DMatrix(X[missing], feature_names=names), iteration_range=(0, booster.best_iteration + 1)))
    base_missing = np.where(np.isnan(med100[missing]), 4.0, med100[missing])
    with (args.out / "predicted_heights.csv").open("w") as f:
        f.write("OBJECTID,BLD_ID,height_ml_m,height_baseline_m\n")
        for oid, b, h, hb in zip(ids[missing], bld[missing], pred_missing, base_missing):
            f.write(f"{oid},{b},{h:.2f},{hb:.2f}\n")
    booster.save_model(str(args.out / "height_model.json"))
    (args.out / "metrics.json").write_text(json.dumps(metrics, indent=1) + "\n")
    print(json.dumps({k: v for k, v in metrics.items() if k != "feature_importance_gain"}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
