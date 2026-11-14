# static/ — Modo Agora precomputed layers

Built once by `uv run python live/build_static.py` (needs `GCP_SA_KEY` +
`GEE_PROJECT` in env; GEE service account).

`sul_static.npz` covers the south of Brazil (bounds from `config.yaml` `domains.now`, ~1 km):

| key            | shape        | source |
|----------------|--------------|--------|
| `p90/p95/p99`  | (12, H, W)   | IMERG 3-day accumulation percentiles, per month, 2001-2023 — same windows as `gee/01_tpi_index.js` |
| `t_slide`      | (H, W)       | NASADEM slope rescaled 15-30 deg |
| `t_flood`      | (H, W)       | 1 - HAND proxy rescaled 5-15 m, 0 where slope > 5 deg |
| `slope_deg`, `hand_m` | (H, W) | intermediates |
| `pop`          | (H, W)       | GPWv4.11 population count, nearest epoch (NASA SEDAC/CIESIN `CIESIN/GPWv411/GPW_Population_Count`; people_exposed in summary.json) |
| `pop_year`, `pop_source` | scalar | GPW provenance: epoch year and collection id |
| `lats`, `lons` | (H,), (W,)   | grid coordinates |

The npz is not committed (large); each Actions runner downloads it from
`gs://tipping-point-data/static/` or rebuilds it. Regenerate when bounds or
resolution change.
