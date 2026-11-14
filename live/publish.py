"""Write data/now/ artifacts — manifest.json is ALWAYS written last.

The app reads manifest.json first to discover the layer set; writing it last
guarantees it never points at a half-updated set of files. The manifest dict
is built by live/run_now.build_manifest and follows the v4 `now` node shape
(data/SCHEMA.md): {mode: "live", bounds, data_dates, layers:[{id, date, png,
cog, legend, bounds}]}. The same ordering is kept for the optional GCS
upload (env-gated via NOW_GCS_BUCKET): rsync everything except manifest.json,
then copy manifest.json.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


def write_outputs(out_dir: str | Path, files: dict[str, bytes | str | dict],
                  manifest: dict) -> list[Path]:
    """Write each entry of `files` (ordered), then manifest.json LAST.

    Returns the written paths in write order; the last element is manifest.json.
    """
    out = Path(out_dir)
    written: list[Path] = []
    for rel, content in files.items():
        path = out / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, (dict, list)):
            path.write_text(json.dumps(content, ensure_ascii=False, indent=2))
        elif isinstance(content, str):
            path.write_text(content)
        else:
            path.write_bytes(content)
        written.append(path)
    mpath = out / "manifest.json"
    mpath.parent.mkdir(parents=True, exist_ok=True)
    mpath.write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    written.append(mpath)
    return written


def upload_gcs(now_dir: str | Path, bucket: str, prefix: str = "now") -> None:
    """Optional upload to gs://<bucket>/<prefix>/ with manifest last.

    Requires GCP_SA_KEY (service-account JSON) in env and the gcloud CLI.
    Skipped silently when NOW_GCS_BUCKET is unset, so a plain git commit of
    data/now/ is a fully supported publish path.
    """
    key_json = os.environ.get("GCP_SA_KEY")
    if not key_json:
        raise RuntimeError("GCP_SA_KEY not set; cannot upload to GCS")
    import tempfile

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        f.write(key_json)
        key_path = f.name
    try:
        subprocess.run(
            ["gcloud", "auth", "activate-service-account", f"--key-file={key_path}"],
            check=True, capture_output=True,
        )
        dst = f"gs://{bucket}/{prefix}/"
        src = str(Path(now_dir).resolve()) + "/"
        subprocess.run(
            ["gcloud", "storage", "rsync", src, dst, "--recursive",
             "--exclude=manifest.json", "--delete-unmatched-destination-objects"],
            check=True,
        )
        subprocess.run(
            ["gcloud", "storage", "cp",
             str(Path(now_dir) / "manifest.json"), dst + "manifest.json"],
            check=True,
        )
    finally:
        os.unlink(key_path)
