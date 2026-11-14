# gee/ — Earth Engine scripts

Paste each file into the GEE Code Editor (https://code.earthengine.google.com).
This repo is the source of truth — copy changes back here.

- Asset namespace: `projects/<GEE_PROJECT>/assets/tipping-point/...` (NOT legacy `users/` paths)
- Run order: `01` TPI → `02` lead time → `03` RF susceptibility → `04` HAND validation
- The climatology in `01` is heavy: run once, export as an asset, reference it after.
- AOIs/dates must stay in sync with `../events/*.json` (JS can't read files — keep the
  pasted bbox in sync by hand, comment above each one points to the event file).
