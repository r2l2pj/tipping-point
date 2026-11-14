from __future__ import annotations

import argparse
import json
from pathlib import Path

import ee
import geopandas as gpd

from gee.common import init
from ml import config as cfg
from ml import gee_features as gf
from ml import labels

ROOT = f"projects/{cfg.GEE_PROJECT}/assets/tipping-point/models"
STATE = Path(".herdr/route_a_tasks.json")


def save_task(name, task):
    task.start()
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    state[name] = {"task_id": task.id, "asset_id": f"{ROOT}/{name}"}
    STATE.write_text(json.dumps(state, indent=2))
    print(name, task.id, flush=True)


def catalog():
    raw = gpd.read_file("data/rs_2024/geojson/landslide_catalog.geojson")
    points = raw.explode(index_parts=False).reset_index(drop=True)
    points["source"] = "rs_2024_published_inventory"
    print("inventory_points", len(points), flush=True)
    return points, labels.dedupe_to_grid(points)


def prerequisites():
    points, deduped = catalog()
    try:
        ee.data.getAsset(ROOT)
    except ee.EEException:
        ee.data.createAsset({"type": "FOLDER"}, ROOT)
    fc = ee.FeatureCollection([
        ee.Feature(ee.Geometry.Point([g.x, g.y]), {"cell_id": f"pos_{i}", "label": 1, "set": "pos"})
        for i, g in enumerate(deduped.geometry)
    ])
    footprint = labels.mapped_footprint(points)
    geom = ee.Geometry(footprint.__geo_interface__)
    save_task("rs2024_slides", ee.batch.Export.table.toAsset(
        collection=fc, description="route_a_rs2024_slides", assetId=f"{ROOT}/rs2024_slides"))
    save_task("rs2024_footprint", ee.batch.Export.table.toAsset(
        collection=ee.FeatureCollection([ee.Feature(geom)]), description="route_a_rs2024_footprint",
        assetId=f"{ROOT}/rs2024_footprint"))
    save_task("rs2024_inventory_points", ee.batch.Export.table.toAsset(
        collection=ee.FeatureCollection([
            ee.Feature(ee.Geometry.Point([g.x, g.y])) for g in points.geometry
        ]), description="route_a_rs2024_inventory_points", assetId=f"{ROOT}/rs2024_inventory_points"))
    save_task("imerg_daily_p99", ee.batch.Export.image.toAsset(
        image=gf.imerg_daily_p99(ee), description="route_a_imerg_daily_p99",
        assetId=f"{ROOT}/imerg_daily_p99", region=gf.aoi_geometry(ee), scale=10000,
        crs="EPSG:4326", maxPixels=1e10))


def status():
    for name, item in json.loads(STATE.read_text()).items():
        print(name, ee.data.getTaskStatus(item["task_id"])[0], flush=True)


def fetch_rows(collection):
    rows = []
    request = {"expression": collection, "pageSize": 1000}
    while True:
        result = ee.data.computeFeatures(request)
        rows.extend(result["features"])
        if not result.get("nextPageToken"):
            return rows
        request["pageToken"] = result["nextPageToken"]


def cells():
    import pandas as pd

    from ml.dataset import block_ids

    points, deduped = catalog()
    slides = ee.FeatureCollection(f"{ROOT}/rs2024_slides")
    rain = {}
    for end in pd.date_range(*cfg.EVENT_WINDOW, freq="D", tz="UTC"):
        t = ee.Date(end.isoformat())
        image = gf._imerg_accum(ee, t.advance(-24, "hour"), t).rename("rain")
        rows = fetch_rows(image.sampleRegions(slides, scale=10000))
        rain[end] = {f["properties"]["cell_id"]: f["properties"]["rain"] for f in rows}
        print("peak_time", end.isoformat(), len(rows), flush=True)
    best = pd.DataFrame(rain).idxmax(axis=1)
    ids = [f"pos_{i}" for i in range(len(deduped))]
    if set(best.index) != set(ids):
        raise RuntimeError("peak-time extraction dropped inventory cells")
    positive = pd.DataFrame({"cell_id": ids, "t_utc": best.reindex(ids).to_numpy(),
                             "lon": deduped.geometry.x, "lat": deduped.geometry.y})
    positive["label"] = 1
    positive["set"] = "pos"
    positive.to_csv("data/rs_2024/positive_cells.csv", index=False)
    footprint = ee.Geometry(labels.mapped_footprint(points).__geo_interface__)
    negatives = gf.sample_n1(
        footprint, ee.FeatureCollection(f"{ROOT}/rs2024_inventory_points"), ee=ee)
    info = fetch_rows(negatives)
    negative = pd.DataFrame([
        {"lon": f["geometry"]["coordinates"][0], "lat": f["geometry"]["coordinates"][1],
         "cell_id": f"N1_{i}", "label": 0, "set": "N1",
         "t_utc": positive.iloc[i % len(positive)]["t_utc"]}
        for i, f in enumerate(info)
    ])
    base = pd.concat([positive, negative], ignore_index=True)
    base["block_id"] = block_ids(base.lon, base.lat)
    base.to_csv("data/rs_2024/matched_cells.csv", index=False)
    print("matched_cells", len(base), "N2_rows", len(base) * len(gf.calm_days()), flush=True)


def features():
    import pandas as pd

    from ml.dataset import iter_block_folds

    ee.data.getAsset(f"{ROOT}/imerg_daily_p99")
    base = pd.read_csv("data/rs_2024/matched_cells.csv")
    base["fold"] = -1
    for fold, (_, test) in enumerate(iter_block_folds(base)):
        base.loc[test, "fold"] = fold
    base.to_csv("data/rs_2024/matched_cells.csv", index=False)
    cfg.ASSET_IMERG_P99 = "models/imerg_daily_p99"
    static = gf.static_stack(ee)
    parts = []
    groups = [(str(t), rows.copy()) for t, rows in base.groupby("t_utc")]
    for day in gf.calm_days():
        rows = base.copy()
        rows["t_utc"] = day.isoformat()
        rows["label"] = 0
        rows["set"] = "N2"
        groups.append((day.isoformat(), rows))
    for part, (t, rows) in enumerate(groups):
        name = f"rs2024_features_part_{part:03d}"
        state = json.loads(STATE.read_text())
        if name in state:
            parts.append(f"{ROOT}/{name}")
            continue
        fc = ee.FeatureCollection([
            ee.Feature(ee.Geometry.Point([r.lon, r.lat]), {
                **{k: getattr(r, k) for k in ["cell_id", "label", "set", "block_id", "fold"]},
                "lon": r.lon, "lat": r.lat, "t_utc": t,
            }) for r in rows.itertuples()
        ])
        table = static.addBands(gf.dynamic_stack(t, ee=ee)).sampleRegions(
            collection=fc, scale=cfg.CELL_SIZE_M, geometries=True, tileScale=4)
        save_task(name, ee.batch.Export.table.toAsset(
            collection=table, description=name, assetId=f"{ROOT}/{name}"))
        parts.append(f"{ROOT}/{name}")
    Path(".herdr/route_a_parts.json").write_text(json.dumps(parts, indent=2))


def download():
    import pandas as pd

    from ml.dataset import iter_block_folds, load_features

    paths = []
    for asset in json.loads(Path(".herdr/route_a_parts.json").read_text()):
        path = Path("data/rs_2024") / (asset.rsplit("/", 1)[-1] + ".csv")
        if not path.exists():
            rows = []
            request = {"expression": ee.FeatureCollection(asset), "pageSize": 10000}
            while True:
                result = ee.data.computeFeatures(request)
                rows.extend(f["properties"] for f in result["features"])
                if not result.get("nextPageToken"):
                    break
                request["pageToken"] = result["nextPageToken"]
            pd.DataFrame(rows).to_csv(path, index=False)
        paths.append(path)
    out = Path("data/rs_2024/features.csv")
    frames = [pd.read_csv(p) for p in paths]
    pd.concat(frames, ignore_index=True).to_csv(out, index=False)
    df = load_features(out)
    expected = pd.Series(-1, index=df.index)
    for fold, (_, test) in enumerate(iter_block_folds(df)):
        expected.iloc[test] = fold
    df["fold"] = expected
    df.to_csv(out, index=False)
    print("table", len(df), "positive", int(df.label.sum()), "sets", df["set"].value_counts().to_dict())
    mapping = df[["block_id", "fold"]].drop_duplicates()
    folds = {str(int(r.block_id)): int(r.fold) for r in mapping.itertuples()}
    Path(".herdr/route_a_folds.json").write_text(json.dumps(folds, indent=2))
    table = ee.FeatureCollection([
        ee.FeatureCollection(asset) for asset in json.loads(Path(".herdr/route_a_parts.json").read_text())
    ]).flatten()
    lookup = ee.Dictionary(folds)
    table = table.map(lambda f: f.set("fold", lookup.get(ee.Number(f.get("block_id")).format("%.0f"))))
    save_task("rs2024_features", ee.batch.Export.table.toAsset(
        collection=table, description="route_a_shared_features", assetId=f"{ROOT}/rs2024_features"))


def train():
    table = ee.FeatureCollection(f"{ROOT}/rs2024_features")
    training = table.filter(ee.Filter.neq("fold", 0))
    estimators = {
        "rf": ee.Classifier.smileRandomForest(numberOfTrees=600, minLeafPopulation=5, seed=cfg.SEED),
        "gtb": ee.Classifier.smileGradientTreeBoost(
            numberOfTrees=600, shrinkage=0.05, samplingRate=0.5, maxNodes=8, seed=cfg.SEED),
    }
    for name, estimator in estimators.items():
        classifier = estimator.setOutputMode("MULTIPROBABILITY").train(
            features=training, classProperty="label", inputProperties=cfg.FEATURES)
        if name == "rf":
            save_task("rs2024_rf_fold0", ee.batch.Export.classifier.toAsset(
                classifier=classifier, description="route_a_rf_fold0", assetId=f"{ROOT}/rs2024_rf_fold0"))
        predictions = table.classify(classifier, "probabilities").map(
            lambda f: f.set("p1", ee.Array(f.get("probabilities")).get([1])))
        save_task(f"rs2024_{name}_predictions", ee.batch.Export.table.toAsset(
            collection=predictions.select(["cell_id", "t_utc", "label", "set", "fold", "p1"]),
            description=f"route_a_{name}_predictions", assetId=f"{ROOT}/rs2024_{name}_predictions"))


def evaluate(name="rf"):
    import numpy as np
    import pandas as pd
    from sklearn.metrics import accuracy_score, confusion_matrix, f1_score, precision_score, recall_score

    from ml.dataset import load_features
    from ml.evaluate import metric_bundle, threshold_at_fpr

    rows = fetch_rows(ee.FeatureCollection(f"{ROOT}/rs2024_{name}_predictions"))
    scored = pd.DataFrame(f["properties"] for f in rows)
    scored.to_csv(f"data/rs_2024/gee_{name}_predictions.csv", index=False)
    train = scored[scored.fold != 0]
    test = scored[scored.fold == 0]
    threshold = threshold_at_fpr(train.label, train.p1)
    predicted = (test.p1 >= threshold).astype(int)
    tn, fp, fn, tp = confusion_matrix(test.label, predicted, labels=[0, 1]).ravel()
    metrics = {
        "route": "A", "status": "evaluated", "evaluated": True,
        "asset_id": f"{ROOT}/rs2024_rf_fold0" if name == "rf" else None,
        "model": "ee.Classifier.smileRandomForest" if name == "rf" else "ee.Classifier.smileGradientTreeBoost",
        "deployment": "exported" if name == "rf" else "not deployable (EE cannot export GTB)",
        "held_out_fold": 0,
        "split": "ml.dataset.iter_block_folds five-fold GroupKFold on 0.25-degree blocks",
        "threshold": threshold, "threshold_source": "training-fold fitted scores at FPR <= 0.05, not test",
        "n_train": len(train), "n_test": len(test), "n_positive_test": int(test.label.sum()),
        "base_rate": float(test.label.mean()), "precision": float(precision_score(test.label, predicted, zero_division=0)),
        "recall": float(recall_score(test.label, predicted)), "f1": float(f1_score(test.label, predicted)),
        "accuracy": float(accuracy_score(test.label, predicted)),
        "tn": int(tn), "fp": int(fp), "fn": int(fn), "tp": int(tp),
        **metric_bundle(test.label, test.p1), "wa_inventory_positive_n": 15,
        "wa_caveat": "Only 15 WA inventory points; no strong WA generalization claims; RS spatial holdout only.",
    }
    df = load_features("data/rs_2024/features.csv")
    heldout = df[df.fold == 0].head(100).copy()
    heldout["rowid"] = np.arange(len(heldout))
    cfg.ASSET_IMERG_P99 = "models/imerg_daily_p99"
    compared = []
    for t, group in heldout.groupby("t_utc"):
        fc = ee.FeatureCollection([
            ee.Feature(ee.Geometry.Point([r.lon, r.lat]), {"rowid": int(r.rowid)})
            for r in group.itertuples()
        ])
        sampled = gf.features_at(fc, t.isoformat(), ee=ee)
        compared.extend(f["properties"] for f in fetch_rows(sampled))
    actual = pd.DataFrame(compared).set_index("rowid").sort_index()
    expected = heldout.set_index("rowid").sort_index()
    if set(actual.index) != set(expected.index):
        raise RuntimeError("parity sample dropped held-out rows")
    for feature in cfg.FEATURES:
        metrics[f"feature_max_abs_diff__{feature}"] = float(np.abs(actual[feature] - expected[feature]).max())
    metrics["feature_parity_n"] = len(actual)
    path = Path("data/rs_2024/ml_metrics_gee.json")
    combined = json.loads(path.read_text()) if path.exists() else {}
    combined.update({("gtb_" + k if name == "gtb" else k): v for k, v in metrics.items()})
    path.write_text(json.dumps(combined, indent=2, allow_nan=False))
    report = Path("../.crew/research/model-metrics.md")
    text = f"\n\n### Evaluated route A: {name}\n\n"
    text += f"{metrics['deployment']}. Same fold 0 holdout; threshold from training folds only.\n\n"
    text += "| Metric | Value |\n|---|---|\n"
    text += "\n".join(f"| {k} | {v} |" for k, v in metrics.items()) + "\n"
    report.write_text(report.read_text() + text)
    print(json.dumps(metrics, indent=2), flush=True)


def evaluate_gtb():
    evaluate("gtb")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=["prerequisites", "status", "cells", "features", "download", "train", "evaluate", "evaluate_gtb"])
    args = ap.parse_args()
    init()
    globals()[args.stage]()


if __name__ == "__main__":
    main()
