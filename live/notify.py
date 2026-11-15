"""Telegram subscriber alerts for Modo Agora — PLAN.md "Regras de envio".

Runs at the tail of .github/workflows/now.yml ("Notify Telegram
subscribers"), after data/now is published. For each consenting row in the
subscriber store (live.alerts — Neon HTTPS SQL or sqlite via DATABASE_URL)
it samples the four published TPI COGs (landslide/flood x today/D+1,
EPSG:4326, nodata=NaN) at the shared location and applies the send rules:

- alert only on an upgrade to Alto/Crítico (class >= 2 and > last_class);
- at most one message per 6 h per chat (last_alert_ts);
- never between 23h and 6h America/Sao_Paulo unless the class is Crítico;
- a drop to a lower class sends "risco reduzido" (never in quiet hours);
- upgrades/reductions held back (rate limit, quiet hours, failed send, no
  token) keep last_class unchanged so the next run still delivers them.

current_label/data_date are refreshed every run so the bot's /status stops
saying "sem dados". The manifest gates everything: alerts_paused or
verified != true -> nothing is sent and nothing in the store changes.

Message text (G2, conductor ruling): signed "Tipping Point (não oficial)",
never attributes authorship to Defesa Civil, says "sua região" (never
"sua casa"), and always ends with the exact line
"Siga as orientações da Defesa Civil. SMS 40199."

Every store/network/raster error is a single log line; main() always
returns 0 so the workflow step can never fail the run.
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import json
import os
import re
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import rasterio
import requests

from live import alerts, now_geojson, tpi
from live.config import ALERT_MIN_CLASS, LABELS, NOW_DIR, QUIET_HOURS

BRT = ZoneInfo("America/Sao_Paulo")
RATE_LIMIT = dt.timedelta(hours=6)

SIGNATURE = "Tipping Point (não oficial)"
FOOTER = "Siga as orientações da Defesa Civil. SMS 40199."
HAZARD_PT = {"landslide": "deslizamento", "flood": "enchente"}
WHEN_PT = {0: "hoje", 1: "amanhã"}

# (hazard, day, COG path under out_dir) — the alert-bot contract written by
# run_now --local (float TPI, nodata=NaN). day 0 = today, 1 = D+1.
FRAMES = (
    ("landslide", 0, "cog/tpi_slide_today.tif"),
    ("flood", 0, "cog/tpi_flood_today.tif"),
    ("landslide", 1, "cog/tpi_slide_d1.tif"),
    ("flood", 1, "cog/tpi_flood_d1.tif"),
)
LAYER_ID = {("landslide", 0): "tpi_slide_d0", ("flood", 0): "tpi_flood_d0",
            ("landslide", 1): "tpi_slide_d1", ("flood", 1): "tpi_flood_d1"}


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        prog="live.notify", description=__doc__.splitlines()[0])
    p.add_argument("--out-dir", type=Path, default=NOW_DIR,
                   help="published now bundle (default: data/now)")
    return p.parse_args(argv)


# ---------------------------------------------------------------------------
# dates / labels

def _ddmm(raw) -> str | None:
    """ISO date/datetime or a granule id carrying YYYYMMDD -> 'dd/mm'."""
    if not raw:
        return None
    s = str(raw).strip()
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", s) or re.search(
        r"(?<!\d)(\d{4})(\d{2})(\d{2})(?!\d)", s)
    return f"{m.group(3)}/{m.group(2)}" if m else None


def _gfs_stamp(raw) -> str | None:
    """'2026-11-15T00:00:00Z' -> '15/11 00:00' (for 'emitida ... UTC')."""
    if not raw:
        return None
    try:
        t = dt.datetime.fromisoformat(str(raw)[:16].replace("T", " "))
    except ValueError:
        return _ddmm(raw)
    return t.strftime("%d/%m %H:%M")


def _smap_day(data_dates: dict) -> str | None:
    """SMAP date as dd/mm: prefer data_dates.smap_label (written by the
    manifest rework); else parse data_dates.smap, which may be an ISO date
    or a granule id like SMAP_L4_SM_gph_20261110T223000_Vv8040_001."""
    label = data_dates.get("smap_label")
    if label:
        return _ddmm(label) or str(label)
    return _ddmm(data_dates.get("smap"))


def _data_date(manifest: dict) -> str:
    """The date /status shows: latest observed rain day, ISO YYYY-MM-DD."""
    dd = manifest.get("data_dates") or {}
    raw = (dd.get("imerg_obs_through") or dd.get("imerg")
           or dd.get("forecast_from") or manifest.get("updated_at") or "")
    return str(raw)[:10] or "-"


def _layer_for(manifest: dict, hazard: str | None, day: int | None) -> dict:
    lid = LAYER_ID.get((hazard, day))
    for layer in manifest.get("layers") or []:
        if isinstance(layer, dict) and layer.get("id") == lid:
            return layer
    return {}


def _current_label(cls: int, hazard: str | None, day: int | None) -> str:
    label = LABELS[cls]
    if cls == 0 or hazard is None or day is None:   # Baixo: no hazard/day
        return label
    return f"{label} — {HAZARD_PT.get(hazard, hazard)}, {WHEN_PT[day]}"


def _parse_ts(raw) -> dt.datetime | None:
    """last_alert_ts (ISO text or epoch seconds) -> aware UTC, or None."""
    if raw in (None, "", 0):
        return None
    s = str(raw).strip()
    try:
        return dt.datetime.fromtimestamp(float(s), dt.UTC)
    except ValueError:
        pass
    try:
        t = dt.datetime.fromisoformat(s)
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=dt.UTC)


# ---------------------------------------------------------------------------
# message text (G2)

def _place_text(place: dict | None) -> str:
    if place and place.get("municipality"):
        uf = f", {place['uf']}" if place.get("uf") else ""
        return f"{place['municipality']}{uf} (sua região, ~1 km)"
    return "sua região (~1 km)"


def build_alert_text(cls: int, hazard: str | None, day: int | None,
                     place: dict | None, manifest: dict,
                     app_url: str | None = None) -> str:
    """pt-BR upgrade alert. Complement to Defesa Civil, never its voice."""
    dd = manifest.get("data_dates") or {}
    layer = _layer_for(manifest, hazard, day)
    gfs = _gfs_stamp(layer.get("gfs_issued") or dd.get("gfs_issued"))
    obs = _ddmm(layer.get("obs_through") or dd.get("imerg_obs_through"))
    when_day = _ddmm(layer.get("date")) or _ddmm(
        (dd.get("forecast_from") or "")[:10])
    basis = layer.get("basis")
    if basis == "observed":
        basis_pt = f"observado (IMERG até {obs})" if obs else "observado"
    elif basis == "forecast":
        basis_pt = (f"previsão (NOAA GFS emitida {gfs} UTC)" if gfs
                    else "previsão (NOAA GFS)")
    else:  # observed+forecast — the today frame mixes both (L28b)
        basis_pt = (f"observado + previsão (NOAA GFS emitida {gfs} UTC)" if gfs
                    else "observado + previsão")
    smap = _smap_day(dd)
    dados = " · ".join(p for p in (
        f"chuva observada IMERG até {obs}" if obs else None,
        f"umidade do solo SMAP de {smap}" if smap else None,
    ) if p) or "dados NASA IMERG/SMAP"
    lines = [
        (f"⚠️ {SIGNATURE}: risco {LABELS[cls].upper()} de "
         f"{HAZARD_PT.get(hazard, 'deslizamento')}"),
        f"Região: {_place_text(place)}",
        f"Quando: {WHEN_PT.get(day, 'hoje')}, {when_day or '—'} — {basis_pt}",
        f"Dados: {dados}",
    ]
    if app_url:
        lines.append(f"Ver no mapa: {app_url}")
    lines.append(FOOTER)
    return "\n".join(lines)


def build_reduced_text(cls: int, place: dict | None, data_date: str) -> str:
    """pt-BR 'risco reduzido' notice — same signature and footer."""
    where = ""
    if place and place.get("municipality"):
        uf = f", {place['uf']}" if place.get("uf") else ""
        where = f" ({place['municipality']}{uf})"
    return (f"{SIGNATURE}: o risco na sua região{where} caiu para "
            f"{LABELS[cls]}. Dados de {_ddmm(data_date) or data_date}."
            f"\n{FOOTER}")


# ---------------------------------------------------------------------------
# rasters

def _open_frames(out_dir: Path) -> dict:
    """{(hazard, day): rasterio dataset} for every readable COG; each
    missing/unreadable file is one log line, never a failure."""
    frames = {}
    for hazard, day, rel in FRAMES:
        try:
            frames[(hazard, day)] = rasterio.open(out_dir / rel)
        except Exception as exc:  # noqa: BLE001 — must not fail the run
            print(f"[notify] {rel} unreadable ({exc}) — frame skipped")
    return frames


def _class_at(ds, lon: float, lat: float) -> int:
    """TPI class at a point; nodata/out-of-extent -> 0 (no risk signal)."""
    try:
        v = float(next(ds.sample([(lon, lat)]))[0])
    except Exception:  # noqa: BLE001 — outside the grid / masked
        return 0
    nodata = ds.nodata
    if nodata is not None and not np.isnan(float(nodata)) \
            and v == float(nodata):
        return 0
    return int(tpi.classify(np.asarray([v], dtype=np.float64))[0])


def evaluate(frames: dict, lon: float, lat: float):
    """-> (class, hazard, day): max TPI class over the readable frames.
    Ties prefer today (day 0); landslide wins only an exact tie."""
    best = None
    for (hazard, day), ds in frames.items():
        cls = _class_at(ds, lon, lat)
        key = (cls, day == 0, hazard == "landslide")
        if best is None or key > best[0]:
            best = (key, hazard, day, cls)
    if best is None:
        return 0, None, None
    return best[3], best[1], best[2]


# ---------------------------------------------------------------------------
# telegram

def _send(token: str, chat_id, text: str) -> bool:
    """POST sendMessage; True only on HTTP 2xx + {"ok": true}."""
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text}, timeout=30)
        if 200 <= resp.status_code < 300 and resp.json().get("ok") is True:
            return True
        print(f"[notify] telegram send failed (HTTP {resp.status_code})")
    except Exception:  # noqa: BLE001 — network errors never fail the run
        print("[notify] telegram send failed — skipping delivery")
    return False


def run(out_dir: Path | str = NOW_DIR, now: dt.datetime | None = None) -> dict:
    """One notify pass over consenting subscribers -> small summary dict.
    Errors are logged, never raised."""
    now = now or dt.datetime.now(dt.UTC)
    out_dir = Path(out_dir)
    summary = {"sent": 0, "updated": 0, "skipped": None}
    try:
        manifest = json.loads((out_dir / "manifest.json").read_text())
    except (OSError, ValueError) as exc:
        print(f"[notify] manifest unreadable ({exc}) — nothing sent")
        summary["skipped"] = "manifest"
        return summary
    if manifest.get("alerts_paused"):
        print("[notify] manifest alerts_paused — nothing sent")
        summary["skipped"] = "alerts_paused"
        return summary
    if manifest.get("verified") is not True:
        print("[notify] manifest not verified — nothing sent")
        summary["skipped"] = "unverified"
        return summary
    if alerts._backend() is None:
        print("[notify] unrecognized DATABASE_URL scheme — skipping")
        summary["skipped"] = "backend"
        return summary
    frames = _open_frames(out_dir)
    try:
        if not frames:
            print("[notify] no readable TPI COGs — nothing sent")
            summary["skipped"] = "no-cogs"
            return summary
        try:
            rows = alerts.subscribers()
        except Exception as exc:  # noqa: BLE001 — store errors never fail
            print(f"[notify] store unreachable ({exc}) — nothing sent")
            summary["skipped"] = "store"
            return summary
        where = now_geojson.municipality_lookup(out_dir / "geojson")
        quiet = now.astimezone(BRT).hour in QUIET_HOURS
        token = os.environ.get("TELEGRAM_TOKEN")
        app_url = os.environ.get("APP_URL")
        if not token:
            print("[notify] TELEGRAM_TOKEN unset — updating labels only")
        data_date = _data_date(manifest)
        now_iso = now.isoformat().replace("+00:00", "Z")
        for row in rows:
            chat_id = row.get("chat_id")
            if chat_id is None:
                continue
            try:
                lon, lat = float(row["lon"]), float(row["lat"])
            except (KeyError, TypeError, ValueError):
                continue
            cls, hazard, day = evaluate(frames, lon, lat)
            place = where(lon, lat)
            prev = int(row.get("last_class") or 0)
            last_ts = _parse_ts(row.get("last_alert_ts"))
            rate_limited = (last_ts is not None
                            and now - last_ts < RATE_LIMIT)
            text = None
            if cls >= ALERT_MIN_CLASS and cls > prev and not rate_limited \
                    and (not quiet or cls == 3):
                text = build_alert_text(cls, hazard, day, place, manifest,
                                        app_url=app_url)
            elif cls < prev and not rate_limited and not quiet:
                text = build_reduced_text(cls, place, data_date)
            # last_class = the class last communicated to the subscriber:
            # it advances only when a message actually left. Held-back
            # (rate limit/quiet/no token) or failed sends retry next run,
            # and silent rises below the alert threshold keep the baseline
            # so an unnoticed 0->1->0 never spams a "risco reduzido".
            new_class, sent_at = prev, None
            if text is not None and token and _send(token, chat_id, text):
                new_class, sent_at = cls, now_iso
                summary["sent"] += 1
            try:
                alerts.update_subscriber(chat_id, {
                    "last_class": new_class,
                    "last_alert_ts": sent_at,
                    "current_label": _current_label(cls, hazard, day),
                    "data_date": data_date,
                })
                summary["updated"] += 1
            except Exception as exc:  # noqa: BLE001 — per-row, never fatal
                print(f"[notify] subscriber update failed ({exc}) — skipped")
        return summary
    finally:
        for ds in frames.values():
            with contextlib.suppress(Exception):
                ds.close()


def main(argv=None, now: dt.datetime | None = None) -> int:
    args = parse_args(argv)
    try:
        run(args.out_dir, now=now)
    except Exception as exc:  # noqa: BLE001 — notify must never fail the job
        print(f"[notify] aborted ({exc}) — nothing sent")
    return 0


if __name__ == "__main__":
    sys.exit(main())
