"""Shared Earth Engine plumbing for the gee/ Python modules.

Pinned public API (lead-analysis plan; U5 codes against it — do not rename):
module constants GEE_PROJECT, COL_IMERG, BAND_IMERG, COL_SMAP, BAND_SMAP,
IMG_NASADEM, BAND_DEM, CLIM_YEARS, ASSET_ROOT; functions init(), case_cfg(),
aoi_geom(), event_dates().

`import ee` at module level is fine, but every ee object is built inside a
function: importing this module never initializes Earth Engine, so
`--dry-run` paths work without credentials.
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import ee
from tp.config import REPO_ROOT
from tp.config import case as _tp_case
from tp.config import case_id as _tp_case_id
from tp.config import event_id as _tp_event_id

GEE_PROJECT = os.environ.get("GEE_PROJECT", "")
COL_IMERG = "NASA/GPM_L3/IMERG_V07"
BAND_IMERG = "precipitation"        # mm/h at 30-min steps -> sums multiply by 0.5
COL_SMAP = "NASA/SMAP/SPL4SMGP/008"  # /007 is discontinued
BAND_SMAP = "sm_rootzone_wetness"
IMG_NASADEM = "NASA/NASADEM_HGT/001"
BAND_DEM = "elevation"
CLIM_YEARS = (2001, 2023)
ASSET_ROOT = "projects/{GEE_PROJECT}/assets/tipping-point"

# D10: GCP_SA_KEY is canonical; GEE_SERVICE_ACCOUNT_KEY is the fallback.
_KEY_ENV_VARS = ("GCP_SA_KEY", "GEE_SERVICE_ACCOUNT_KEY")


def _sa_key_file() -> str | None:
    """Path to the service-account JSON key from env, or None.

    The env value is a key-file path (.env.example); a raw JSON blob is also
    accepted and written to a temp file (same convention as
    live/build_static.py).
    """
    for var in _KEY_ENV_VARS:
        value = os.environ.get(var, "").strip()
        if not value:
            continue
        if value.startswith("{"):
            with tempfile.NamedTemporaryFile(
                    "w", suffix=".json", prefix="gee-sa-",
                    delete=False) as tmp:
                json.dump(json.loads(value), tmp)
            return tmp.name
        if not Path(value).exists():
            raise RuntimeError(f"{var} points to a missing file: {value}")
        return value
    return None


def init(project: str | None = None) -> str:
    """ee.Initialize with service-account creds; return the resolved project.

    Credentials come only from env: GCP_SA_KEY first, then
    GEE_SERVICE_ACCOUNT_KEY (D10). With no key file, falls back to the
    `earthengine authenticate` credential store; either way the error names
    what is missing.
    """
    proj = project or GEE_PROJECT
    if not proj:
        raise RuntimeError(
            "GEE project unknown: pass --project or set GEE_PROJECT.")
    key_file = _sa_key_file()
    try:
        if key_file:
            creds = ee.ServiceAccountCredentials(None, key_file)
            ee.Initialize(credentials=creds, project=proj)
        else:
            ee.Initialize(project=proj)
    except Exception as e:
        raise RuntimeError(
            "Earth Engine init failed: set GCP_SA_KEY to the service-account "
            "JSON key path (GEE_SERVICE_ACCOUNT_KEY also works) plus "
            "GEE_PROJECT, or run `earthengine authenticate`. See .env.example."
        ) from e
    return proj


def case_cfg(case_id: str) -> dict:
    """{aoi, event_date, window_before_days, window_after_days} for a case."""
    return _tp_case(case_id)


def aoi_geom(case_id: str):
    """ee.Geometry.Rectangle of the case AOI ([w, s, e, n] from config.yaml)."""
    return ee.Geometry.Rectangle(list(case_cfg(case_id)["aoi"]))


def event_dates(case_id: str) -> list[str]:
    """YYYY-MM-DD strings, event day ± window days (inclusive, ordered)."""
    cfg = case_cfg(case_id)
    day0 = cfg["event_date"]
    if hasattr(day0, "date"):  # datetime -> date
        day0 = day0.date()
    lo, hi = -int(cfg["window_before_days"]), int(cfg["window_after_days"])
    return [(day0 + timedelta(days=d)).isoformat() for d in range(lo, hi + 1)]


def event_id(case_id: str) -> str:
    """config.yaml case key -> hyphen event id ('wa_2025' -> 'wa-2025-12')."""
    return _tp_event_id(case_id)


def case_dir(case_id: str) -> Path:
    """Repo-relative output dir data/<case-id>/ (delta L4: outputs keyed by
    canonical case id, resolved via tp.config.case_id — event ids accepted).
    """
    return Path("data") / _tp_case_id(case_id)


def data_dir(case_id: str) -> Path:
    """Absolute data/<case-id>/ path for writers."""
    return Path(REPO_ROOT) / case_dir(case_id)


def stamp(img, data_date: str, now: str | None = None):
    """Set the data_date + updated_at properties on an asset-exported image.

    `data_date` is the YYYY-MM-DD of the source data ('static' for statics);
    `updated_at` is the ISO-8601 UTC export time (injectable for tests).
    """
    ts = now or datetime.now(UTC).isoformat()
    return img.set("data_date", data_date).set("updated_at", ts)


def asset_path(name: str, project: str | None = None) -> str:
    """ASSET_ROOT/<name> with the project placeholder filled in."""
    proj = project or GEE_PROJECT
    if not proj:
        raise RuntimeError("GEE_PROJECT not set — needed for asset paths.")
    return f"{ASSET_ROOT.format(GEE_PROJECT=proj)}/{name}"
