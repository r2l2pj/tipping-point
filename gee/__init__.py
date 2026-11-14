"""gee/ — Earth Engine work for Tipping Point.

The `gee/*.js` files are Code Editor scripts (this repo is their source of
truth). The Python package mirrors them so the replay pipeline runs headless:

- `gee.common` — shared EE plumbing (pinned API: init, case_cfg, aoi_geom,
  event_dates) that U5's gee/impact.py and gee/hand.py build on.
- `gee.tpi` — replay TPI series, port of `01_tpi_index.js` / `07_tpi_wa.js`.
- `gee.check_series` — Mode-1 done-when checks on a local series COG.
"""
