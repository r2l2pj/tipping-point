# emilia_2023 TEST event — implementation notes (t18b)

## What changed

- `ml/local/test_events.d/emilia_2023.yaml` (new): RER2023 inventory event.
  `dating: fixed`, D = 2023-05-16, trigger_window [2023-05-15, 2023-05-17]
  (main rain episode 16-17 May; earlier episode 1-3 May folded in),
  inventory_window auto = [2023-05-08, 2023-05-24]. `format: vector`,
  source = Zenodo `/files/RER2023_V1.rar?download=1` (URL path must end in
  `.rar` — the `/api/.../content` URL saves a file named `content`).
  max_points 3000, enabled: false, licence CC-BY-4.0, citation noted.
- `ml/local/labels.py`: `_resolve_source` now returns `p = _extract_rar(p, ev)`
  for any resolved path ending `.rar` (local or downloaded). New `_extract_rar`:
  extracts once under `DATA_DIR/events/<id>/rar_x/` using the first working
  system tool of unrar / bsdtar / 7z (`shutil.which` + rc check), then returns
  the single `.shp` found inside; clear RuntimeError if no tool / != 1 shp.
  `out.mkdir()` added because `S.path()` only creates the parent.
- `ml/tests/test_local_pipeline.py`: `test_resolve_source_rar_extracts_shp` —
  offline test; a tar named `.rar` exercises the loop (unrar fails on it,
  bsdtar/libarchive sniffs content). Skips when bsdtar absent.

## Inventory facts

- Zenodo doi:10.5281/zenodo.13742643, "RER2023 v1", CC-BY-4.0.
- RER2023_V1.rar = RAR5, 43.6 MB -> ESRI shapefile, 80,997 polygon features.
- CRS EPSG:7791 (RDN2008/UTM 32N) — `_read_vector` reprojects to 4326.
- Fields: OBJECTID IDC Lito GLI NFS ClassMan ClassNew — NO date/episode
  attribute -> fixed dating (as instructed).

## Commands run + results

- `curl https://zenodo.org/api/records/13742643` -> file RER2023_V1.rar,
  license cc-by-4.0.
- `curl -sL -o $ML_DATA_DIR/events/emilia_2023/RER2023_V1.rar <files-url>`
  (43,630,642 B); `unrar lb/x` -> shapefile set.
- pyogrio.read_info: 80,997 feats, EPSG:7791, fields as above.
- Proof script (ML_DATA_DIR=~/.cache/tipping-point-ml, PYTHONPATH=pipeline:.):
  n raw = 80,997 -> n after load = 3,000 (dedupe 90 m + cap);
  lon 10.3174–12.7057, lat 43.7528–44.5384; dates {2023-05-16};
  GLC leak count = 0.
- `pytest -q ml/tests/test_local_pipeline.py` -> 55 passed (incl. new test).

## Uncertain / notes

- dating=fixed per instructions (no per-point date exists); D covers only the
  main episode — slides triggered 1-3 May also get 2023-05-16.
- Download URL choice: `records/.../files/RER2023_V1.rar?download=1` keeps the
  `.rar` suffix so extraction triggers.
