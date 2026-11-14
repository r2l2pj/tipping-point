"""Perfect forecast: the observed TPI band stands in as the D+k forecast.

Replay forecast for the two cases (DECIDED, plan-delta): the layer issued on
day D for day D+k is the observed IMERG-driven TPI band of D+k read from the
multiband series COG — every consumer labels it
``forecast_source: "perfect-forecast"``. Modo Agora keeps live GEOS-FP; this
module is replay-only.

Outputs go to ``data/<case-id>/cog/`` (case-id dirs, spec delta L4): both
``rs-2024-05`` and ``rs_2024`` resolve to case ``rs_2024``.

    uv run python pipeline/scripts/07_forecast.py --event rs-2024-05 \
        --series data/rs_2024/cog/rs_2024_tpi_slide_20240502.tif
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from pathlib import Path

import rasterio

from . import events
from .metrics import merge_metrics

log = logging.getLogger(__name__)

FORECAST_SOURCE = "perfect-forecast"


def case_id(any_id: str) -> str:
    """Canonical case key: 'rs-2024-05' or 'rs_2024' → 'rs_2024' (L4)."""
    return "_".join(any_id.split("-")[:2])


def resolve_event_id(any_id: str) -> str:
    """Event id for either form: 'rs-2024-05' stays; 'rs_2024' → its event id."""
    if (events.EVENTS_DIR / f"{any_id}.json").exists():
        return any_id
    cid = case_id(any_id)
    matches = [e for e in events.list_events() if case_id(e) == cid]
    if not matches:
        raise KeyError(f"no event for {any_id!r}; events/: {events.list_events()}")
    return matches[0]


def _band_dates(src) -> dict[str, int]:
    """Map 'YYYYMMDD' band descriptions → 1-based band index."""
    out = {}
    for i, desc in enumerate(src.descriptions, start=1):
        if desc and desc.strip():
            out[desc.strip().replace("-", "")] = i
    return out


def perfect_forecast(
    series_cog: str | Path,
    event_id: str,
    issue_date: str | date,
    days: tuple[int, ...] = (1, 2, 3),
    out_dir: str | Path | None = None,
    metrics_path: str | Path | None = None,
) -> dict:
    """Write ``<caso>_tpi_forecast_d<k>_<AAAAMMDD>.tif`` for each k in ``days``.

    The d<k> image is the observed TPI band of ``issue_date + k`` from
    ``series_cog`` (float TPI series, bands named YYYYMMDD); ``AAAAMMDD`` in
    the filename is the *issue* date. ``event_id`` accepts either the event
    id (``rs-2024-05``) or the case id (``rs_2024``); outputs default to
    ``data/<case-id>/cog/`` (L4). A date with no band writes nothing and is
    reported under ``"missing"``. ``forecast_source`` is merged via
    ``tp.metrics.merge_metrics`` (event id — the helper routes it).
    """
    event_id = resolve_event_id(event_id)
    issue = date.fromisoformat(str(issue_date))
    out_dir = Path(out_dir) if out_dir else events.event_data_dir(case_id(event_id)) / "cog"
    out_dir.mkdir(parents=True, exist_ok=True)

    written: list[str] = []
    missing: list[dict] = []
    with rasterio.open(series_cog) as src:
        band_dates = _band_dates(src)
        profile = {
            "driver": "COG", "height": src.height, "width": src.width,
            "count": 1, "dtype": src.dtypes[0], "crs": src.crs,
            "transform": src.transform, "nodata": src.nodata,
        }
        for k in days:
            target = (issue + timedelta(days=k)).strftime("%Y%m%d")
            idx = band_dates.get(target)
            if idx is None:
                missing.append({"day": k, "date": target})
                log.warning("no band %s in %s — skipping d%d", target, series_cog, k)
                continue
            band = src.read(idx)
            out_path = out_dir / f"{events.artifact_stem(event_id, f'tpi_forecast_d{k}', issue.isoformat())}.tif"
            with rasterio.open(out_path, "w", **profile) as dst:
                dst.write(band, 1)
                dst.set_band_description(1, target)
            written.append(str(out_path))
            log.info("wrote %s (observed band %s as d%d)", out_path.name, target, k)

    merge_metrics(event_id, flat={"forecast_source": FORECAST_SOURCE}, path=metrics_path)
    return {"issue": issue.isoformat(), "source": FORECAST_SOURCE,
            "written": written, "missing": missing}
