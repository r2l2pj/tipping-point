# implement — kerala_2018 TEST event (t18c, unit/ml-ev-kerala)

## What changed

- `ml/local/test_events.d/kerala_2018.yaml` (new): one event `kerala_2018`,
  `enabled: false`. Kerala Aug 2018 monsoon landslides; inventory = van Westen
  2020, DANS Data Station doi:10.17026/dans-x6c-y7x2 (Hao et al. 2020, ESSD 12,
  2899–2918). `dating: fixed`, `date: 2018-08-16`, `trigger_window:
  [2018-08-14, 2018-08-19]` (paper: peak rain + most failures 14–19 Aug 2018 —
  date from the paper, never rain). `format: vector`, `max_points: 3000`,
  licence CC-BY-4.0.
- `ml/local/labels.py` `_resolve_source`: when a download URL's basename has no
  file extension (Dataverse `api/access/dataset/:persistentId/` → `:persistentId`),
  name the cached file from the response `Content-Disposition` filename. This is
  what keeps the `.zip` suffix so `_read_vector`'s existing `zip://` prefix kicks
  in and pyogrio reads the shapefile inside the dataset zip. No new dependencies,
  no archive/RAR tools needed.
- `ml/tests/test_local_pipeline.py`: `test_resolve_source_uses_content_disposition_name`
  (offline, fake `get`) covering header-name use, keep-URL-name-when-it-has-an-ext,
  and no-header fallback.

## Commands run

- `curl .../api/access/dataset/:persistentId/?persistentId=doi:10.17026/dans-x6c-y7x2`
  → dataset zip, 735,446 B, 13 entries (`Kerela landslide.shp/.dbf/.shx/.prj/...`
  + mif/mid + metadata.pdf). No login wall.
- `pytest -q ml/tests/test_local_pipeline.py` → 55 passed.
- Proof snippet (`ML_DATA_DIR=$HOME/.cache/tipping-point-ml`):
  - download via `_resolve_source` →
    `events/kerala_2018/doi-10.17026-dans-x6c-y7x2_2.0.zip`
  - 4728 raw features → 4728 valid → **3000 points** (dedupe 90 m, cap)
  - lon 75.0154–77.3963, lat 8.7310–12.6550 (Kerala)
  - dates: all 2018-08-16 (fixed)
  - `leak_mask(glc, df, {kerala_2018: inventory_window})[0].sum()` → **54**
    GLC rows flagged (window 2018-08-07..26 ±3 d, ≤100 km); all 29 current-VAL
    GLC positives near the event are inside that set (task estimate was 17).
- `ML_TEST_EVENTS=kerala_2018` → joins TRIGGER_TEST_EVENTS /
  EVENT_WINDOWS / LABEL_WINDOWS / EVENT_DAYS correctly.

## Uncertain / notes

- The DANS zip contains both a shapefile and a MapInfo `.mif/.mid`; pyogrio's
  `zip://` picks the shapefile (verified: 4728 Point geometries, EPSG:4326).
- Raw count is 4728 exactly as the paper states; cap drops it to a seeded
  sample of 3000.
- GLC leak count over the full GLC inventory is 54 rows (includes reports with
  accuracy >5 km that never enter 3b rows); 29 of the currently-VAL GLC
  positives are this event and are all flagged.
