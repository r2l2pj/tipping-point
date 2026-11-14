"""GEE asset ingest for pipeline products not in the Earth Engine catalog.

Ruling L10: NO GCS bucket. Table/vector assets go through ingest_table,
which reads a local GeoJSON FeatureCollection, builds a client-side
ee.FeatureCollection per chunk of TABLE_CHUNK_FEATURES features, and
uploads each chunk through its own ee.batch.Export.table.toAsset task so
no single EE request exceeds chunk_size features. Catalogs larger than
one chunk land as partNN table assets INSIDE a FOLDER parent created at
projects/<GEE_PROJECT>/assets/tipping-point/<case>/<name>.

There is NO raster-ingest path: research confirmed no bucket-free raster
upload exists, so raster fetchers keep their COGs local and record them
with status 'local-only'. append_manifest upserts the row in
data/<case>/assets.json either way.

Ingestion is idempotent: when ee.data.getAsset already returns the asset it
is skipped (status "exists") unless force=True.

Env vars:
  GEE_PROJECT                     Earth Engine cloud project
                                  (default 'hermes-fesolucoes')
  GCP_SA_KEY                      service-account JSON (path or raw blob —
                                  repo convention, shared with gee.common)
  GEE_SERVICE_ACCOUNT_KEY         fallback SA key env
  GOOGLE_APPLICATION_CREDENTIALS  service-account JSON path (ADC)

Missing creds raise RuntimeError naming what to set. ee and gee.common are
imported lazily (importlib, so tests inject fakes through sys.modules) —
--dry-run and the test suite need no credentials, no SDK and no network. EE
is initialized through the shared gee.common.init helper
(ServiceAccountCredentials). NOTE: hermes-fesolucoes is not registered
with Earth Engine yet, so real ingestion is blocked until it is.
"""
from __future__ import annotations

import importlib
import json
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

from tp import events

DEFAULT_GEE_PROJECT = "hermes-fesolucoes"
ASSET_ROOT = "tipping-point"
MANIFEST_SCHEMA = "tipping-point/gee-assets@1"
# Features per client-side ee.FeatureCollection chunk (and per export
# task) in ingest_table; catalogs up to ~20k features must never land in
# a single inline object OR a single EE request.
TABLE_CHUNK_FEATURES = 5000
# D10 convention (gee/common.py): GCP_SA_KEY is canonical for SA creds.
BUCKET_ENV_VARS = ("NOW_GCS_BUCKET", "GCS_BUCKET")
SA_KEY_ENV_VARS = ("GCP_SA_KEY", "GEE_SERVICE_ACCOUNT_KEY")
_SEGMENT_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


def _import(module_name: str):
    """importlib.import_module wrapper: sys.modules fakes win in tests, and a
    genuinely missing SDK surfaces as a clean RuntimeError."""
    try:
        return importlib.import_module(module_name)
    except ImportError as exc:
        raise RuntimeError(
            f"{module_name} is required for ingest but is not installed: {exc}"
        ) from exc


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def gee_project() -> str:
    return os.environ.get("GEE_PROJECT") or DEFAULT_GEE_PROJECT


def staging_bucket() -> str:
    """Bucket name from NOW_GCS_BUCKET (GCS_BUCKET accepted alias).

    LEGACY — no ingest path in this module uses a bucket anymore (L10);
    kept only because out-of-scope callers (tp.fallback, geosfp/imerg/smap)
    still resolve it until their units are reworked.
    """
    for var in BUCKET_ENV_VARS:
        if value := os.environ.get(var):
            return value
    raise RuntimeError(
        "no staging bucket configured; set NOW_GCS_BUCKET "
        "(GCS_BUCKET is accepted as an alias)"
    )


def _require_creds() -> None:
    """RuntimeError naming what to set when no SA credentials are in env."""
    if not os.environ.get("GOOGLE_APPLICATION_CREDENTIALS") and not any(
        os.environ.get(v) for v in SA_KEY_ENV_VARS
    ):
        raise RuntimeError(
            "no service-account credentials in env; set "
            "GOOGLE_APPLICATION_CREDENTIALS or GCP_SA_KEY "
            "(GEE_SERVICE_ACCOUNT_KEY also works) to the SA JSON key path"
        )


def _init_ee() -> None:
    """Initialize Earth Engine through the shared gee.common.init helper
    (ServiceAccountCredentials from the SA key envs, else ADC). The repo
    root is appended to sys.path so `gee` resolves wherever ingest is
    imported from; everything is lazy so fake-ee tests never need it."""
    repo_root = str(Path(__file__).resolve().parents[2])
    if repo_root not in sys.path:
        sys.path.append(repo_root)
    _import("ee")  # resolves sys.modules fakes; errors early if missing
    common = _import("gee.common")
    common.init(project=gee_project())


def _validate_segment(value, field: str) -> None:
    """Reject path separators/traversal in a value interpolated into a GCS
    blob or GEE asset path segment. Dots are allowed (callers pass real
    filenames like 'wa_2025_nisar_gcov_20251228.tif'); '..' and a trailing
    '.' are not."""
    if (not isinstance(value, str) or not _SEGMENT_RE.fullmatch(value)
            or ".." in value or value.endswith(".")):
        raise ValueError(
            f"invalid {field} {value!r}; expected [A-Za-z0-9][A-Za-z0-9._-]* "
            "with no '..'"
        )


def _validate_path_safe(value, field: str) -> None:
    """Looser check for values placed inside a path (bucket, project,
    full asset ids): no separators, no traversal."""
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a non-empty string")
    if "/" in value or "\\" in value or ".." in value:
        raise ValueError(
            f"invalid {field} {value!r}: '/' and '..' are not allowed"
        )


def _validate_asset_id(value) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError("asset_id must be a non-empty string")
    if "\\" in value or any(
        p in ("", ".", "..") for p in value.split("/")
    ):
        raise ValueError(f"invalid GEE asset id {value!r}")


# Words that mean an auth/permission/service failure even when the message
# happens to contain 'not found' — they veto the text fallback below.
_AUTH_ERROR_MARKERS = (
    "permission", "denied", "unauthoriz", "forbidden",
    "unauthenticated", "credential",
)


def _is_not_found(exc: Exception) -> bool:
    """True only when a getAsset failure means 'asset missing'.

    An explicit status decides first: HTTP-style `status_code` /
    `resp.status` / `code()` of 404 (or a NOT_FOUND enum/string) means
    absent, any other status returns False so the caller re-raises it.
    Only when no status exists at all is the message text matched — and
    auth/denied markers always veto that match.
    """
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(getattr(exc, "resp", None), "status", None)
    if status is None:
        status = getattr(exc, "code", None)
        if callable(status):
            status = status()
    if status is not None:
        try:
            return int(status) == 404
        except (TypeError, ValueError):
            return "NOT_FOUND" in str(status).upper()
    text = str(exc).lower()
    if any(marker in text for marker in _AUTH_ERROR_MARKERS):
        return False
    return (
        "not_found" in text
        or "not found" in text
        or "does not exist" in text
    )


def asset_id(case: str, name: str, project: str | None = None) -> str:
    """projects/<proj>/assets/tipping-point/<case>/<name>."""
    _validate_segment(case, "case")
    _validate_segment(name, "name")
    project = project or gee_project()
    _validate_path_safe(project, "project")
    return f"projects/{project}/assets/{ASSET_ROOT}/{case}/{name}"


def _ensure_parent(ee, path: str) -> None:
    """Create `path` as a FOLDER asset if absent (holds table partNN
    children — ImageCollection only accepts images). Same getAsset +
    strict not-found semantics as the part idempotency check."""
    try:
        ee.data.getAsset(path)
    except Exception as exc:
        if not _is_not_found(exc):
            raise
        ee.data.createAsset({"type": "FOLDER"}, path)


def _export_table_chunk(ee, features: list, target_id: str,
                        description: str, force: bool):
    """Build one client-side ee.FeatureCollection from at most chunk_size
    GeoJSON feature dicts and start its Export.table.toAsset task."""
    collection = ee.FeatureCollection([ee.Feature(f) for f in features])
    task = ee.batch.Export.table.toAsset(
        collection,
        description,
        assetId=target_id,
        overwrite=bool(force),
    )
    task.start()
    return task


def ingest_table(geojson_path, *, asset_id, force=False,
                 chunk_size=TABLE_CHUNK_FEATURES, description=None) -> dict:
    """Ingest a local GeoJSON FeatureCollection as GEE table asset(s).

    No GCS bucket (ruling L10). Every client request is bounded: features
    are split into `chunk_size` chunks, each becomes one client-side
    ee.FeatureCollection uploaded by its own
    ee.batch.Export.table.toAsset task. A catalog fitting one chunk lands
    at `asset_id` as a plain table asset; larger ones get a FOLDER parent
    created at `asset_id` and land inside it as `<asset_id>/part00`,
    `part01`, … (the manifest entry lists them under 'parts'). Idempotent
    via getAsset per target (the parent folder included) — only
    a 'not found' (404) error means absence; force=True re-exports
    (overwrite). Returns the manifest entry.
    """
    _validate_asset_id(asset_id)
    local = Path(geojson_path)
    if not local.is_file():
        raise FileNotFoundError(local)
    doc = json.loads(local.read_text())
    features = doc.get("features") if isinstance(doc, dict) else None
    if not isinstance(features, list):
        raise TypeError(
            f"{local}: expected a GeoJSON FeatureCollection "
            "('features' list)")
    _require_creds()
    _init_ee()
    ee = _import("ee")

    name = asset_id.rsplit("/", 1)[-1]
    entry = {
        "name": name,
        "gee_id": asset_id,
        "kind": "table",
        "features": len(features),
        "source": str(local),
    }
    chunk_size = int(chunk_size)
    if chunk_size < 1:
        raise ValueError("chunk_size must be >= 1")
    chunks = [
        features[i:i + chunk_size]
        for i in range(0, len(features), chunk_size)
    ] or [[]]
    single = len(chunks) == 1
    targets = (
        [asset_id] if single
        else [f"{asset_id}/part{k:02d}" for k in range(len(chunks))]
    )
    if not single:
        _ensure_parent(ee, asset_id)
    desc = description or f"ingest_{name}"

    parts = []
    for k, (target, chunk) in enumerate(zip(targets, chunks)):
        part = {"gee_id": target, "features": len(chunk)}
        if not force:
            try:
                ee.data.getAsset(target)
            except Exception as exc:
                if not _is_not_found(exc):
                    raise
            else:
                part["status"] = "exists"
                parts.append(part)
                continue
        task = _export_table_chunk(
            ee, chunk, target,
            desc if single else f"{desc}_part{k:02d}", force)
        part["status"] = "ingesting"
        if task_id := getattr(task, "id", None):
            part["operation"] = task_id
        parts.append(part)

    entry["status"] = (
        "ingesting" if any(p["status"] == "ingesting" for p in parts)
        else "exists")
    entry["ingested_at"] = _now()
    if single:
        if op := parts[0].get("operation"):
            entry["operation"] = op
    else:
        entry["parts"] = parts
    return entry


def _is_already_exists(exc: Exception) -> bool:
    """True when a createAsset failure means 'asset already exists' — the
    get-then-create race outcome. An explicit status decides first (409 /
    ALREADY_EXISTS); message text is only matched when no status exists,
    with auth markers vetoing (same rule as _is_not_found)."""
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(getattr(exc, "resp", None), "status", None)
    if status is None:
        status = getattr(exc, "code", None)
        if callable(status):
            status = status()
    if status is not None:
        try:
            return int(status) == 409
        except (TypeError, ValueError):
            return "ALREADY_EXISTS" in str(status).upper()
    text = str(exc).lower()
    if any(marker in text for marker in _AUTH_ERROR_MARKERS):
        return False
    return "already_exists" in text or "already exists" in text


def ensure_collection(asset_parent_id, ee_data=None) -> dict:
    """Create an IMAGE_COLLECTION container at asset_parent_id if absent.

    Idempotent: when getAsset finds the container already, a status-"exists"
    entry is returned and no createAsset call is made. `ee_data` may be
    injected (tests); otherwise credentials + _init_ee() resolve ee.data
    lazily, keeping this module import-safe without creds.
    """
    _validate_asset_id(asset_parent_id)
    if ee_data is None:
        _require_creds()
        _init_ee()
        ee_data = _import("ee").data
    try:
        asset = ee_data.getAsset(asset_parent_id)
    except Exception as exc:
        if not _is_not_found(exc):
            raise
        asset = None
    if asset is not None:
        return {"gee_id": asset_parent_id, "type": "IMAGE_COLLECTION",
                "status": "exists"}
    try:
        ee_data.createAsset({"type": "IMAGE_COLLECTION"},
                            path=asset_parent_id)
    except Exception as exc:
        # get-then-create race: another run created it after our getAsset —
        # the container now exists, which is all this function guarantees
        if not _is_already_exists(exc):
            raise
        return {"gee_id": asset_parent_id, "type": "IMAGE_COLLECTION",
                "status": "exists"}
    return {"gee_id": asset_parent_id, "type": "IMAGE_COLLECTION",
            "status": "created"}


def append_manifest(case, entry) -> Path:
    """Upsert `entry` into data/<case>/assets.json; return its path.

    Rows are keyed by 'name', so re-ingesting an asset replaces its row
    instead of appending a duplicate.
    """
    _validate_segment(case, "case")
    path = events.DATA_DIR / case / "assets.json"
    doc = {"schema": MANIFEST_SCHEMA, "assets": []}
    if path.is_file():
        doc = json.loads(path.read_text())
    assets = [
        a for a in doc.get("assets", []) if a.get("name") != entry.get("name")
    ]
    assets.append(entry)
    doc["schema"] = doc.get("schema") or MANIFEST_SCHEMA
    doc["assets"] = assets
    doc["updated_at"] = _now()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2) + "\n")
    return path
