# t6f — case validation metrics via interactive GEE reads

## What changed

New `tools/validation_metrics.py` (run: `uv run python tools/validation_metrics.py [--case …]`,
needs `PYTHONPATH=.`, `.env` sourced, `GCP_SA_KEY` exported). Writes
`data/<case>/validation_metrics.json` = `{case, event_date, computed_at, metrics{flat},
_null_reasons, collections}` — deliberately not `data/metrics.json` (write races).
Read-only EE: reduceRegion/aggregate_array getInfo only, no Export tasks.

- Task A (wa_2025): `flood_nisar_polygons` table asset (125 features, ingest confirmed
  live via `ee.data.getAsset`) painted to 0/1; vs OPERA DSWx S1 (water=1,3) and HLS
  (water=1,2, cloud=253 excluded), tp/fp/fn at 30m over the AOI.
- Task B (rs_2024): HLS L30+S30 Fmask union over 2024-05-01..03 — a pixel counts
  cloud-free when ANY scene observed it with bits1-4 unset and ≠255.
- Task C (wa_2025): HAND proxy (NASADEM − focalMin(radius) < 5m) vs the NISAR mask at
  90m for radii 1000/1500/3000; emits `hand_validation` (IoU*100),
  `hand_validation_radii` {radius_m: iou}, `hand_best_radius_m`.

## Deviation from plan

**No OPERA DSWx granule exists on 2025-12-16 for either catalog** (verified by
listing collection dates: S1 has 12-15 and 12-17; HLS same). Used the ±1-day window
2025-12-15..17 (mosaic) and recorded the actual dates in `opera_dates`/`collections`.
Everything else per plan.

## Results

`data/wa_2025/validation_metrics.json`:
- iou = 0.00753, f1 = 0.01494 vs DSWx-S1 (5 scenes, 12-15+12-17);
  confusion tp=9287 fp=37576 fn=1186684 observed_px=16.25M. Low IoU is expected —
  OPERA marks all water incl. the whole flooded valley; NISAR mask is 125 polygons.
- hls_iou = 0.0133, hls_f1 = 0.0263 on cloud-free px only (6 scenes); cloud_pct = 92.9%
  (the "why radar" number for the December AR).
- hand_validation = 0.975 (IoU*100 = 0.975%), hand_best_radius_m = 1000,
  radii {1000:0.00975, 1500:0.00938, 3000:0.00757} — HAND<5m covers the whole river
  corridor so IoU vs the small NISAR flood extent is small at every radius.

`data/rs_2024/validation_metrics.json`:
- hls_scenes = 20, hls_observed_px = 535467, cloud_free_pct = 0.0, cloud_pct = 100.0 —
  zero AOI pixels Fmask-clear in 2024-05-01..03; optical saw 0% at the event peak.

## Commands run

- `source .env; export GCP_SA_KEY=~/code/r2l2pj/jovial-idiom-514715-j8-sa.json`
- `uv run python tools/validation_metrics.py --case rs_2024` → wrote file (above).
- `uv run python tools/validation_metrics.py --case wa_2025` → first run timed out on
  30m focalMin reduceRegion ("Computation timed out"); fixed with HAND_SCALE=90 +
  tileScale=4; second run → wrote file (above).
- `ee.data.getAsset(flood_nisar_polygons)` → TABLE, 125 features (ingest done).
- `uv run pytest gee/tests/test_opera.py gee/tests/test_impact_hand.py
  gee/tests/test_hls_gee.py pipeline/tests/test_validation.py -q` → **49 passed**.
- `uv run ruff check tools/validation_metrics.py` → clean.

No git add/commit/push. No new dependencies.
