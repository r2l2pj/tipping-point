"""ML pipeline CLI — one entry point per stage (see ml/README.md "Run locally").

    python -m ml.local labels        # GLC + RS 2024 inventory, RS peak-rain days
    python -m ml.local fetch-region  # 3a grids: NASADEM 90 m, MCD12Q1, NDVI, IMERG monthly, SMAP mean/texture
    python -m ml.local sample        # positives/negatives + TRAIN/VAL/TEST split files
    python -m ml.local fetch-points  # 3b points: POWER IMERG series, SMAP at (point, date), terrain, GFS (TEST)
    python -m ml.local table         # feature tables
    python -m ml.local db            # SQLite store data/ml/tipping_point_ml.sqlite (landslides, samples, features)
    python -m ml.local eda           # exploratory analysis -> ml/eda/figures + summary
    python -m ml.local train         # fit on TRAIN, calibrate + threshold on VAL
    python -m ml.local evaluate      # TEST metrics -> ml/models/metrics_ml.json
    python -m ml.local export        # data/cog/t_ml.tif, data/metrics.json, data/ml/*.json
    python -m ml.local all           # everything above, in order

``--smoke`` runs every stage on real data but tiny: SMOKE_BBOX for 3a, 16
GLC points worldwide + 30 RS points + the WA 2025 reports for 3b; ML_DATA_DIR should point at a
scratch directory (the README does this).
"""
from __future__ import annotations

import argparse
import datetime as dt
import logging
import time

import numpy as np

from . import settings as S

log = logging.getLogger("ml.local")
STAGES = ["labels", "fetch-region", "sample", "fetch-points", "table", "db", "eda",
          "train", "evaluate", "export"]


def _region(smoke: bool):
    return S.SMOKE_BBOX if smoke else S.REGION_TRAIN


def stage_labels(a):
    from .table import build_labels
    build_labels(smoke=a.smoke, force=a.force)


def stage_fetch_region(a):
    from . import sources_dynamic as D
    from . import sources_static as ST
    bbox = _region(a.smoke)
    ST.build_static(bbox)
    if a.smoke:
        D.fetch_imerg_monthly(years=(2023, 2023), bbox=bbox)
        D.build_soil_mean(years=(2023, 2023), every=60, bbox=bbox)
    else:
        D.fetch_imerg_monthly(bbox=bbox)
        D.build_soil_mean(bbox=bbox)
    D.fetch_smap_constants(bbox=bbox)
    if not a.smoke and not a.skip_serve_grids:
        # serving grids for t_ml export (Now domain is inside REGION_TRAIN)
        pass


def stage_sample(a):
    from .table import load_inventory, sample_3a, sample_3b
    inv = load_inventory()
    sample_3a(inv, smoke=a.smoke)
    sample_3b(inv, smoke=a.smoke)


def stage_fetch_points(a):
    from . import sources_dynamic as D
    from . import sources_static as ST
    from .table import point_needs
    need = point_needs()
    D.fetch_power_points(need["power_lon"], need["power_lat"])
    D.fetch_smap_points(need["smap_lon"], need["smap_lat"], need["smap_dates"])
    ST.sample_static_points(need["static_lon"], need["static_lat"])
    if not a.no_gfs:
        for job in need["gfs_jobs"]:  # one small box per TEST event (RS 2024, WA 2025)
            days = [d.astype(dt.date) for d in job["issue_dates"]
                    if d >= np.datetime64("2021-03-01")]
            if days:
                log.info("GFS %s: %d issue days, bbox %s", job["event_id"], len(days), job["bbox"])
                D.fetch_gfs(days, bbox=job["bbox"])


def stage_table(a):
    from .table import build_table_3a, build_table_3b
    build_table_3a()
    build_table_3b()


def stage_db(a):
    from .store import write_store
    write_store()


def stage_eda(a):
    from ml.eda import run as eda
    eda.main(smoke=a.smoke)


def stage_train(a):
    from .trainer import train_3a, train_3b, write_metrics
    write_metrics({"val_3a": train_3a(balanced=a.balanced),
                   "val_3b": train_3b(balanced=a.balanced)})


def stage_evaluate(a):
    from .trainer import evaluate_3a, evaluate_3b, write_metrics
    write_metrics({"3a": evaluate_3a(), "3b": evaluate_3b(gfs=not a.no_gfs)})


def stage_export(a):
    from .export import export_metrics, export_t_ml
    export_metrics()
    export_t_ml(bbox=S.SMOKE_BBOX if a.smoke else S.REGION_SERVE, write_90m=a.t_ml_90m)


RUN = {"labels": stage_labels, "fetch-region": stage_fetch_region, "sample": stage_sample,
       "fetch-points": stage_fetch_points, "table": stage_table, "db": stage_db, "eda": stage_eda,
       "train": stage_train, "evaluate": stage_evaluate, "export": stage_export}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m ml.local", description=__doc__.splitlines()[0])
    ap.add_argument("stage", choices=STAGES + ["all"])
    ap.add_argument("--smoke", action="store_true", help="tiny real-data run (< 2 min after caches)")
    ap.add_argument("--force", action="store_true", help="re-download label sources")
    ap.add_argument("--balanced", action="store_true", help="class-weight positives (scale_pos_weight / balanced RF)")
    ap.add_argument("--no-gfs", action="store_true", help="skip the GFS (real forecast) evaluation")
    ap.add_argument("--t-ml-90m", action="store_true", help="also write data/cog/t_ml_90m.tif")
    ap.add_argument("--skip-serve-grids", action="store_true")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    if a.smoke:
        # Smoke outputs never land in tracked files (ml/models/, data/metrics.json, data/cog/).
        from . import export
        S.MODEL_DIR = S.DATA_DIR / "smoke_models"
        S.MODEL_DIR.mkdir(parents=True, exist_ok=True)
        export.DATA_OUT = S.DATA_DIR / "smoke_export"
        log.info("smoke: models -> %s, exports -> %s", S.MODEL_DIR, export.DATA_OUT)
    log.info("ML_DATA_DIR=%s  SQLITE=%s", S.DATA_DIR, S.SQLITE_PATH)
    for st in (STAGES if a.stage == "all" else [a.stage]):
        t0 = time.time()
        log.info("== stage %s", st)
        RUN[st](a)
        log.info("== stage %s done in %.1f s", st, time.time() - t0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
