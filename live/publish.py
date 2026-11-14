"""Write data/now/ artifacts — manifest.json is ALWAYS written last.

The app reads manifest.json first to discover the layer set; writing it last
guarantees it never points at a half-updated set of files. The manifest dict
is built by live/run_now.build_manifest and follows the v4 `now` node shape
(data/SCHEMA.md): {mode: "live", bounds, data_dates, layers:[{id, date, png,
cog, legend, bounds}]}.
"""
from __future__ import annotations

import json
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

