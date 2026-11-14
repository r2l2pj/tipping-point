"""GEE asset ingest for pipeline products not in the Earth Engine catalog.

stage_to_gcs uploads a local file to gs://<bucket>/staging/<case>/<name><ext>
via google.cloud.storage; ingest_asset then starts an Earth Engine
ingestion — ee.data.startIngestion for rasters/COGs (kind "image") or
ee.data.startTableIngestion for GeoJSON points/polygons (kind "table") —
under projects/<GEE_PROJECT>/assets/tipping-point/<case>/<name>, and
append_manifest upserts the row in data/<case>/assets.json.

Ingestion is idempotent: when ee.data.getAsset already returns the asset it
is skipped (status "exists") unless force=True.

Env vars:
  GEE_PROJECT                     Earth Engine cloud project
                                  (default 'hermes-fesolucoes')
  NOW_GCS_BUCKET                  staging bucket (GCS_BUCKET accepted alias)
  GOOGLE_APPLICATION_CREDENTIALS  service-account JSON path

Missing creds raise RuntimeError naming the missing env var. ee and
google.cloud.storage are imported lazily (importlib, so tests inject fakes
through sys.modules) — --dry-run and the test suite need no credentials, no
SDK and no network. NOTE: hermes-fesolucoes is not registered with Earth
Engine yet, so real ingestion is blocked until it is.
"""
from __future__ import annotations

import importlib
import json
import os
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path

from tp import events

DEFAULT_GEE_PROJECT = "hermes-fesolucoes"
ASSET_ROOT = "tipping-point"
MANIFEST_SCHEMA = "tipping-point/gee-assets@1"
BUCKET_ENV_VARS = ("NOW_GCS_BUCKET", "GCS_BUCKET")
_SEGMENT_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")


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
    """Bucket name from NOW_GCS_BUCKET (GCS_BUCKET accepted alias)."""
    for var in BUCKET_ENV_VARS:
        if value := os.environ.get(var):
            return value
    raise RuntimeError(
        "no staging bucket configured; set NOW_GCS_BUCKET "
        "(GCS_BUCKET is accepted as an alias)"
    )


def _require_creds() -> None:
    if not os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
        raise RuntimeError(
            "GOOGLE_APPLICATION_CREDENTIALS is not set; it must point at a "
            "service-account JSON before staging to GCS or ingesting to GEE"
        )


def _validate_segment(value, field: str) -> None:
    """Reject path separators/traversal in a value interpolated into a GCS
    blob or GEE asset path segment."""
    if not isinstance(value, str) or not _SEGMENT_RE.fullmatch(value):
        raise ValueError(
            f"invalid {field} {value!r}; expected [A-Za-z0-9][A-Za-z0-9_-]*"
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


def _is_not_found(exc: Exception) -> bool:
    """True only when a getAsset failure means 'asset missing' (HTTP 404 /
    NOT_FOUND). Auth, permission and service errors return False so the
    caller can re-raise them — they must not be mistaken for absence."""
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(getattr(exc, "resp", None), "status", None)
    if status is not None:
        try:
            return int(status) == 404
        except (TypeError, ValueError):
            return False
    text = str(exc).lower()
    return "not_found" in text or "not found" in text


def asset_id(case: str, name: str, project: str | None = None) -> str:
    """projects/<proj>/assets/tipping-point/<case>/<name>."""
    _validate_segment(case, "case")
    _validate_segment(name, "name")
    project = project or gee_project()
    _validate_path_safe(project, "project")
    return f"projects/{project}/assets/{ASSET_ROOT}/{case}/{name}"


def staging_uri(bucket: str, case: str, name: str, suffix: str = "") -> str:
    _validate_path_safe(bucket, "bucket")
    _validate_segment(case, "case")
    _validate_segment(name, "name")
    if "/" in suffix or "\\" in suffix or ".." in suffix:
        raise ValueError(f"invalid suffix {suffix!r}")
    return f"gs://{bucket}/staging/{case}/{name}{suffix}"


def stage_to_gcs(local_path, *, bucket, case, name) -> str:
    """Upload local_path to gs://<bucket>/staging/<case>/<name><ext>.

    Returns the staged object's gs:// URI.
    """
    local = Path(local_path)
    if not local.is_file():
        raise FileNotFoundError(local)
    uri = staging_uri(bucket, case, name, local.suffix)
    _require_creds()
    storage = _import("google.cloud.storage")
    blob_name = uri.removeprefix(f"gs://{bucket}/")
    client = storage.Client()
    client.bucket(bucket).blob(blob_name).upload_from_filename(str(local))
    return uri


def ingest_asset(gcs_uri, *, asset_id, kind, force=False) -> dict:
    """Start a GEE ingestion for a staged file; return the manifest entry.

    kind "image" -> ee.data.startIngestion (COG/raster); kind "table" ->
    ee.data.startTableIngestion (GeoJSON). Idempotent: when getAsset finds
    the asset already, a status-"exists" entry is returned and no ingestion
    is started, unless force=True. Only a 'not found' (404) getAsset error
    means absence — auth/permission/service errors propagate.
    """
    if kind not in ("image", "table"):
        raise ValueError(f"unknown asset kind {kind!r}; expected image|table")
    _validate_asset_id(asset_id)
    _require_creds()
    ee = _import("ee")
    init = getattr(ee, "Initialize", None)
    if callable(init):
        init(project=gee_project())

    entry = {
        "name": asset_id.rsplit("/", 1)[-1],
        "gee_id": asset_id,
        "gcs_uri": gcs_uri,
        "kind": kind,
    }
    if not force:
        try:
            ee.data.getAsset(asset_id)
        except Exception as exc:
            if not _is_not_found(exc):
                raise
        else:
            return entry | {"status": "exists", "ingested_at": _now()}

    params = {"name": asset_id}
    if kind == "image":
        params["tilesets"] = [{"sources": [{"uris": [gcs_uri]}]}]
        op = ee.data.startIngestion(uuid.uuid4().hex, params)
    else:
        params["sources"] = [{"uris": [gcs_uri]}]
        op = ee.data.startTableIngestion(uuid.uuid4().hex, params)

    entry["status"] = "ingesting"
    entry["ingested_at"] = _now()
    if isinstance(op, dict) and op.get("name"):
        entry["operation"] = op["name"]
    return entry


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
