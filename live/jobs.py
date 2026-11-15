"""L8 scheduled-job orchestrator — GEE assets are the single source of truth.

Each subcommand sequences the CLIs owned by the other leads
(pipeline/fetch/*, tools/*) via subprocess, in the locked order. The local
numpy path survives only as `run_now.py --local`. Manifests/updated_at are
refreshed before the final fallback export in `now`. The export renders
last-good GEE assets without waiting for asynchronous asset tasks. Any
step's nonzero exit aborts the remaining steps loudly.

Usage:
    uv run python live/jobs.py now [--dry-run]
    uv run python live/jobs.py nisar [--dry-run]
    uv run python live/jobs.py validation [--dry-run]
    uv run python live/jobs.py reference [--dry-run]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from live.config import (
    FORECAST_DAYS,
    IMERG_LOOKBACK_DAYS,
    NISAR_LOOKBACK_DAYS,
    NOW_BOUNDS,
    NOW_DIR,
    QUIET_HOURS,
)

DEFAULT_CASES = ["rs_2024", "wa_2025"]
EVENTS_DIR = Path(__file__).resolve().parents[1] / "events"
EVENTS_INDEX = Path(__file__).resolve().parents[1] / "data" / "events.json"
ROOT_MANIFEST = Path(__file__).resolve().parents[1] / "data" / "manifest.json"
ALERTS_GEOJSON = NOW_DIR / "alerts.json"
ACTIVE_BEFORE = dt.timedelta(days=2)
ACTIVE_AFTER = dt.timedelta(days=45)

SUBPROCESS_ENV_KEYS = (
    "EARTHDATA_TOKEN",
    "GCP_SA_KEY",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "GEE_PROJECT",
)

_DATE_LINE = re.compile(r"^\s*(\S+)\s+(\d{4}-\d{2}-\d{2})\s*$")


def _job_env() -> dict:
    env = dict(os.environ)
    for key in SUBPROCESS_ENV_KEYS:
        if key in os.environ:
            env[key] = os.environ[key]
    return env


def _run_step(name: str, cmd: list[str], env: dict, dry_run: bool = False):
    """Run one subprocess step; abort the whole job on nonzero exit."""
    print(f"[{name}] {' '.join(cmd)}", flush=True)
    if dry_run:
        return None
    timeout = int(os.environ.get("STEP_TIMEOUT", "1200"))
    started = dt.datetime.now(dt.UTC)
    try:
        proc = subprocess.run(cmd, check=False, env=env, text=True,
                              capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise SystemExit(
            f"[{name}] step timed out after {timeout}s — aborting; "
            "last-good data/ is preserved")
    elapsed = (dt.datetime.now(dt.UTC) - started).total_seconds()
    print(f"[{name}] finished in {elapsed:.0f}s", flush=True)
    reported = False
    for line in (proc.stdout or "").splitlines():
        m = _DATE_LINE.match(line)
        if m:
            print(f"[{name}] {m.group(1)} -> {m.group(2)}", flush=True)
            reported = True
    if not reported and proc.stdout and proc.stdout.strip():
        print(f"[{name}] {proc.stdout.strip().splitlines()[-1]}", flush=True)
    if proc.returncode != 0:
        if proc.stdout:
            sys.stderr.write(proc.stdout)
        if proc.stderr:
            sys.stderr.write(proc.stderr)
        raise SystemExit(
            f"[{name}] step failed (exit {proc.returncode}) — aborting; "
            "last-good data/ is preserved")
    return proc


def _refresh_alerts(now_dir: Path = NOW_DIR) -> Path:
    """Rewrite data/now/alerts.json from the current GEE TPI asset regions."""
    from live import alerts

    regions = alerts.critical_areas_from_assets()
    now = dt.datetime.now(dt.UTC)
    payload = {
        "generated_at": now.isoformat().replace("+00:00", "Z"),
        "quiet_hours": now.astimezone(alerts.ZoneInfo("America/Sao_Paulo")).hour in QUIET_HOURS,
        "source": "gee-assets",
        "candidates": regions,
    }
    path = Path(now_dir) / "alerts.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    print(f"[alerts] wrote {path} ({len(regions)} regions)", flush=True)
    return path


# --------------------------------------------------------------------------
# jobs


def now(dry_run: bool = False) -> None:
    """fetch IMERG/SMAP/GEOS-FP -> derived TPI -> alerts -> manifest -> fallback export.

    NOW_LOCAL=1 skips the GEE asset path entirely (no asset writes — use
    while the project asset root is unprovisioned): run_now --local writes
    data/now directly, then alerts -> manifest; export_fallback is skipped
    since there are no derived assets to render from.
    """
    if os.environ.get("NOW_LOCAL") == "1":
        env = _job_env()
        _run_step("now.local",
                  [sys.executable, "live/run_now.py", "--local",
                   "--out-dir", str(NOW_DIR)], env, dry_run)
        # run_now --local already wrote alerts.json; no GEE assets to re-read
        _run_step("now.build_manifest",
                  [sys.executable, "tools/build_manifest.py"], env, dry_run)
        return
    w, s, e, n = (str(v) for v in NOW_BOUNDS)
    steps = [
        ("now.fetch.imerg",
         [sys.executable, "-m", "pipeline.fetch.imerg",
          "--bounds", w, s, e, n, "--days", str(IMERG_LOOKBACK_DAYS)]),
        ("now.fetch.smap",
         [sys.executable, "-m", "pipeline.fetch.smap",
          "--bounds", w, s, e, n]),
        ("now.fetch.geosfp",
         [sys.executable, "-m", "pipeline.fetch.geosfp",
          "--days", str(FORECAST_DAYS)]),
        # best-effort: the CLI writes [] on failure, never exits nonzero
        ("now.nisar_passes",
         [sys.executable, "-m", "live.fetch", "nisar-passes",
          "--bounds", w, s, e, n, "--days", str(NISAR_LOOKBACK_DAYS),
          "--out", str(NOW_DIR / "nisar_passes.json"),
          "--manifest", str(NOW_DIR / "manifest.json")]),
        ("now.tpi",
         [sys.executable, "tools/now_tpi.py"]),
    ]
    env = _job_env()
    for name, cmd in steps:
        _run_step(name, cmd, env, dry_run)
    if dry_run:
        print("[alerts] (skipped in dry-run)", flush=True)
    else:
        _refresh_alerts()
    _run_step("now.build_manifest",
              [sys.executable, "tools/build_manifest.py"], env, dry_run)
    _run_step("now.export_fallback",
              [sys.executable, "tools/export_fallback.py", "--now"], env, dry_run)


def _case_ids() -> list[str]:
    """Canonical case keys from the root manifest, else the hardcoded pair."""
    if ROOT_MANIFEST.exists():
        try:
            cases = json.loads(ROOT_MANIFEST.read_text()).get("cases", {})
        except json.JSONDecodeError:
            cases = {}
        ids = [k for k in cases if k != "now"]
        if ids:
            return ids
    return list(DEFAULT_CASES)


def nisar(dry_run: bool = False) -> None:
    """Daily: ingest new NISAR GCOV scenes for each canonical case."""
    env = _job_env()
    alerts_arg = (["--alerts-geojson", str(ALERTS_GEOJSON)]
                  if ALERTS_GEOJSON.exists() else [])
    for cid in _case_ids():
        _run_step(f"nisar[{cid}]",
                  [sys.executable, "-m", "pipeline.fetch.nisar",
                   "--case", cid, *alerts_arg],
                  env, dry_run)


def _event_dates() -> dict[str, dt.date]:
    """event_id -> event date, from events/*.json else data/events.json."""
    out: dict[str, dt.date] = {}
    files = sorted(EVENTS_DIR.glob("*.json")) if EVENTS_DIR.is_dir() else []
    if files:
        for f in files:
            d = json.loads(f.read_text())
            raw = d.get("reference_date") or d.get("start_date")
            if raw:
                out[d.get("id") or f.stem] = dt.date.fromisoformat(str(raw)[:10])
        return out
    if EVENTS_INDEX.exists():
        for ev in json.loads(EVENTS_INDEX.read_text()).get("events", []):
            raw = ev.get("reference_date") or ev.get("event_date")
            if raw and ev.get("id"):
                out[ev["id"]] = dt.date.fromisoformat(str(raw)[:10])
    return out


def _event_to_case() -> dict[str, str]:
    """event_id -> canonical case key via the root manifest when present."""
    if not ROOT_MANIFEST.exists():
        return {}
    try:
        cases = json.loads(ROOT_MANIFEST.read_text()).get("cases", {})
    except json.JSONDecodeError:
        return {}
    return {v["event_id"]: k for k, v in cases.items()
            if isinstance(v, dict) and v.get("event_id")}


def _canonical_case_id(event_id: str, mapping: dict[str, str]) -> str:
    if event_id in mapping:
        return mapping[event_id]
    parts = re.split(r"[-_]", event_id)
    return f"{parts[0]}_{parts[1]}" if len(parts) >= 2 else event_id


def active_cases(today: dt.date | None = None) -> list[str]:
    """Cases whose event window covers today: [event_date-2d, +45d]."""
    today = today or dt.datetime.now(dt.UTC).date()
    mapping = _event_to_case()
    return [_canonical_case_id(eid, mapping)
            for eid, edate in _event_dates().items()
            if edate - ACTIVE_BEFORE <= today <= edate + ACTIVE_AFTER]


def validation(today: dt.date | None = None, dry_run: bool = False) -> None:
    """Daily while a case is active: GEE OPERA validation + SWOT Hydrocron."""
    active = active_cases(today)
    if not active:
        print("no active case", flush=True)
        return
    env = _job_env()
    for cid in active:
        _run_step(f"opera-gee[{cid}]",
                  [sys.executable, "-m", "gee.opera", "--case", cid],
                  env, dry_run)
        _run_step(f"swot[{cid}]",
                  [sys.executable, "-m", "pipeline.fetch.swot", "--case", cid],
                  env, dry_run)


def reference(dry_run: bool = False) -> None:
    """Weekly/manual: landslide catalog, inventories, municipal boundaries."""
    env = _job_env()
    steps = [
        ("reference.glc",
         [sys.executable, "-m", "pipeline.fetch.reference",
          "--kind", "glc", "--case", "wa_2025"]),
        ("reference.inventory",
         [sys.executable, "-m", "pipeline.fetch.reference",
          "--kind", "inventory", "--case", "rs_2024"]),
        ("reference.boundaries",
         [sys.executable, "-m", "pipeline.fetch.reference",
          "--kind", "boundaries"]),
    ]
    for name, cmd in steps:
        _run_step(name, cmd, env, dry_run)


# --------------------------------------------------------------------------
# CLI


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="jobs", description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="job", required=True)
    for name, fn in (("now", now), ("nisar", nisar),
                     ("validation", validation), ("reference", reference)):
        sp = sub.add_parser(name, help=(fn.__doc__ or "").splitlines()[0])
        sp.add_argument("--dry-run", action="store_true",
                        help="print the step commands without running them")
    return p


def main(argv=None) -> None:
    args = build_parser().parse_args(argv)
    fn = {"now": now, "nisar": nisar,
          "validation": validation, "reference": reference}[args.job]
    fn(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
