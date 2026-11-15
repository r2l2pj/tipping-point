# implement: WA 2025 OPERA validation map layer (t195)

Branch `wa-opera-layer`, commit `3045541`. Done — committed, not pushed.

## What changed

- `tools/wa_opera_layer.py` (new) — builds the 3-class like-for-like image
  server-side in GEE, downloads via `getDownloadURL`, recounts locally,
  writes COG + PNG + `opera_validation.json`, upserts `png/_index.json`.
- `tools/validation_metrics.py` — extracted `_opera_wtr` (collection→mosaic)
  and `_opera_ref_obs` (water/observed masks) out of `_opera_counts` verbatim;
  added `OPERA_PRE_WINDOW_WA = ("2025-12-01","2025-12-08")`. Same expressions
  imported by the layer tool — no behavior change (band list/reducer
  untouched).
- `live/config.py` — `OPERA_CONFUSION_LABELS` (index-order legend incl.
  "Sem dados" at 0) + `OPERA_CONFUSION_PALETTE` (1 `#1a9850`, 2 `#f46d43`,
  3 `#4575b4`).
- `live/render.py` — `palette_png_bytes(class_arr, valid, palette)` RGBA
  encoder (unlisted/invalid → transparent).
- `pipeline/tp/bundle.py` — `SINGLE_LAYERS["opera_validation"]` render kind
  `gray` → `confusion` (`_confusion_png_3857` = warp_to_3857 + palette);
  `record()` gained an `extra` param and the entry carries `legend`.
- `pipeline/tp/manifest.py` — `_layer_entry` forwards `legend` from the
  `_index.json` entry (one word added to the extras tuple); other layers
  keep the TPI legend.
- `data/SCHEMA.md` — `opera_validation` layer description, nodata=0
  exception note, writers-table row.
- `pipeline/tests/test_wa_opera_layer.py` (new, hermetic) — 11 tests on a
  synthetic grid: recount, per-row geodesic km2, confusion_metrics, palette
  RGBA bytes, EPSG:3857 PNG warp bounds, legend rows, index upsert
  (replace vs append), manifest legend forwarding, build_json schema, COG
  roundtrip.

## Commands + results

```sh
# creds: sourced from ~/code/r2l2pj/tipping-point-data/.env (grep'd
# GEE_PROJECT/GOOGLE_APPLICATION_CREDENTIALS/GCP_SA_KEY only, never printed)
PYTHONPATH=.:pipeline systemd-run --user --scope -p MemoryMax=2G \
  .venv/bin/python tools/wa_opera_layer.py --check-only
# server-side reduceRegion (native scale=30) — definition reproduces
# metrics.json EXACTLY:
#   tp 8777 / fp 38086 / fn 60704.3  (metrics: 8777/38086/60704)
#   iou 0.0816 vs 0.081596, f1 0.1509 vs 0.150880
#   scenes: post 5 granules 2025-12-15+17; pre 31 granules
#   (2025-12-02,03,05,07); observed_px 15,813,393

PYTHONPATH=.:pipeline systemd-run --user --scope -p MemoryMax=2G \
  .venv/bin/python tools/wa_opera_layer.py
# download: EPSG:4326, 4082x5572 px @ 0.0002694933 deg/px = "30 m nominal"
# (the EE scale lattice deg=m/111320 that reduceRegion(scale=30) and the
# metrics confusion were counted on — NOT ~900 m2 cells at 48N; an
# area-matched 894 m2 grid gave ratio 0.67, this gives ~1.00)
#   local recount tp 8718 / fp 37975 / fn 60936 vs metrics 8777/38086/60704
#   ratios: 0.9933 / 0.9971 / 1.0038; iou 0.0810 vs 0.0816, f1 0.1499 vs
#   0.1509 — within resampling (one download pass, no tiling needed)

PYTHONPATH=.:pipeline systemd-run --user --scope -p MemoryMax=2G \
  .venv/bin/python tools/build_manifest.py   # manifest.json +updated_at +layer
PYTHONPATH=.:pipeline systemd-run --user --scope -p MemoryMax=2G \
  .venv/bin/python -m pytest pipeline/tests/test_wa_opera_layer.py \
    test_bundle.py test_manifest.py test_metrics_consistency.py test_metrics.py
# 50 passed
```

## Files / sizes

- `data/wa_2025/cog/wa_2025_opera_validation_20251216.tif` — 194,350 B,
  uint8 COG (`IMAGE_STRUCTURE LAYOUT=COG`, overviews 2/4/8/16), EPSG:4326,
  nodata=0, 4082×5572 px.
- `data/wa_2025/png/opera_validation_20251216.png` — 184,525 B, 3052×6196
  RGBA, EPSG:3857; colors verified {TP 26,152,80 / FP 244,109,67 / FN
  69,117,180} + transparent; PNG bounds (lon/lat) `[-122.6, 46.999843,
  -121.499877, 48.501347]`.
- `data/wa_2025/opera_validation.json` — 2,075 B: legend
  [{class,label,color,px,km2}], definition (= `validation_method` wording),
  sources (OPERA DSWx-S1 granule dates post 12-15/17 + pre 12-02/03/05/07;
  NISAR GCOV post granule 2025-12-16 + flood-polygon GEE asset), bounds,
  iou/f1, confusion, metrics_json echo + ratios_vs_metrics.
- `data/wa_2025/png/_index.json` — one appended entry
  `{id: opera_validation, date: 2025-12-16, png, cog, bounds,
  forecast:false, kind:observed, legend:[4 class names]}`.
- `data/manifest.json` — rebuilt; `cases.wa_2025.layers[]` gained the same
  entry (legend forwarded).

## Notes / judgment calls

- Class ids follow the metrics bands: 1=tp, 2=fp, 3=fn, 0=nodata (task spec).
- "30 m" grid = EE's scale lattice (`deg = m/111320`); stated in
  opera_validation.json `grid.note`. A first 894 m² area-matched grid
  counted 0.67× the metrics — diagnosed via a second reduceRegion on the
  download lattice; the EE-lattice grid now lands within ±0.6%.
- `publish_case` not re-run (it would rewrite `_index.json` wholesale and
  drop the GFS-replay entries another producer owns); the layer is
  registered via in-place upsert — same mechanism `gee/impact.py` uses for
  `night_lights_*` and `gfs_layer_stats.py` for GFS frames. bundle.py's
  `confusion` kind makes a future full re-run reproduce this entry anyway.
- `data/events.json` untouched (runtime file).
- Local km2 per class: tp 5.2 / fp 22.7 / fn 36.4 (geodesic per-row).
