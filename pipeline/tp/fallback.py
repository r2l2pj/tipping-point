"""Copy existing producer outputs to a static fallback prefix, manifests last."""
from __future__ import annotations

import json
from pathlib import Path, PurePosixPath

CASES = ("rs_2024", "wa_2025")


def _file(path: Path) -> Path:
    if not path.is_file():
        raise FileNotFoundError(f"missing expected input: {path}")
    return path


def _references(node: dict):
    for layer in node.get("layers", []):
        for field in ("png", "cog"):
            if value := layer.get(field):
                yield value
    yield from (value for value in node.get("products", {}).values() if value)


def _reference(base: Path, value: str) -> Path:
    rel = PurePosixPath(value.split("#", 1)[0])
    if rel.is_absolute() or ".." in rel.parts or "\\" in value:
        raise ValueError("manifest reference must stay within its data directory")
    return _file(base / rel)


def plan(root: str | Path, *, now: bool = False, case: str | None = None,
         prefix: str = "fallback/") -> list[tuple[Path, str]]:
    if now == (case is not None) or (case is not None and case not in CASES):
        raise ValueError("select either now or a canonical case")
    prefix = prefix.strip("/")
    if prefix and ("\\" in prefix or any(p in (".", "..", "") for p in prefix.split("/"))):
        raise ValueError("invalid fallback prefix")
    root = Path(root)
    group = root / ("now" if now else case)
    manifest = _file(root / "manifest.json")
    doc = json.loads(manifest.read_text())
    if now:
        local_manifest = _file(group / "manifest.json")
        node = json.loads(local_manifest.read_text())
        for rel in ("summary.json", "alerts.json", "geojson/critical_areas.geojson"):
            _file(group / rel)
        for value in _references(node):
            _reference(group, value)
    else:
        node = doc["cases"][case]
        for value in _references(node):
            _reference(root, value)
        _file(root / "metrics.json")
    if not group.is_dir():
        raise FileNotFoundError(f"missing expected input directory: {group}")
    files = sorted(p for p in group.rglob("*") if p.is_file())
    if not files:
        raise FileNotFoundError(f"no producer outputs under {group}")
    for path in files:
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            raise ValueError("fallback inputs must be regular files within data root")
    manifests = [p for p in files if p.name == "manifest.json"]
    files = [p for p in files if p not in manifests]
    if not now:
        files.append(root / "metrics.json")
    files.extend(manifests)
    files.append(manifest)
    return [(p, "/".join(filter(None, (prefix, p.relative_to(root).as_posix()))))
            for p in files]


def upload(entries: list[tuple[Path, str]], *, bucket: str | None = None, client=None) -> None:
    from fetch import ingest

    bucket = (bucket or ingest.staging_bucket()).removeprefix("gs://").rstrip("/")
    ingest._validate_path_safe(bucket, "bucket")
    for path, _ in entries:
        _file(path)
    if client is None:
        ingest._require_creds()
        storage = ingest._import("google.cloud.storage")
        try:
            client = storage.Client()
        except Exception as exc:
            raise RuntimeError("GCS client initialization failed; check credentials and permissions") from exc
    try:
        destination = client.bucket(bucket)
        for path, key in entries:
            destination.blob(key).upload_from_filename(str(path))
    except Exception as exc:
        raise RuntimeError("GCS upload failed; check credentials, permissions and connectivity") from exc
