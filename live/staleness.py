"""L13b stale-Now alarm for now-watchdog.yml.

Reads the PUBLISHED data/now/manifest.json (a file path argument, or stdin)
and exits 1 with a GitHub `::error::` annotation when `updated_at` is older
than --max-age-s (default $STALE_AFTER_S or 21600 = 6 h). A missing or
unparseable manifest/updated_at fails too — an unverifiable Now is not
fresh. GitHub emails the owner on failed workflow runs; that is the alarm.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path

DEFAULT_MAX_AGE_S = 21600   # 6 h


def _parse_ts(raw: str) -> dt.datetime:
    ts = dt.datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    return ts if ts.tzinfo else ts.replace(tzinfo=dt.UTC)


def stale_error(manifest: dict, now: dt.datetime, max_age_s: int) -> str | None:
    """None when the manifest is fresh; else the ::error:: message body."""
    raw = manifest.get("updated_at") if isinstance(manifest, dict) else None
    if not isinstance(raw, str) or not raw.strip():
        return f"Now manifest has no usable updated_at ({raw!r})"
    try:
        ts = _parse_ts(raw)
    except ValueError:
        return f"Now manifest updated_at {raw!r} is not a timestamp"
    age_s = (now - ts).total_seconds()
    if age_s > max_age_s:
        return (f"Now data stale: updated_at {raw} is {age_s / 3600:.1f} h old "
                f"(> {max_age_s / 3600:g} h, L13b)")
    return None


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="live.staleness", description=__doc__.splitlines()[0])
    p.add_argument("--max-age-s", type=int,
                   default=int(os.environ.get("STALE_AFTER_S",
                                              DEFAULT_MAX_AGE_S)))
    p.add_argument("manifest", nargs="?", type=Path,
                   help="data/now/manifest.json path (default: read stdin)")
    args = p.parse_args(argv)
    try:
        text = args.manifest.read_text() if args.manifest else sys.stdin.read()
        manifest = json.loads(text)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"::error::cannot read Now manifest: {exc}")
        return 1
    err = stale_error(manifest, dt.datetime.now(dt.UTC), args.max_age_s)
    if err:
        print(f"::error::{err}")
        return 1
    print(f"Now manifest updated_at {manifest['updated_at']} is within "
          f"{args.max_age_s / 3600:g} h — OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
