"""L65 QA (read-only): every NISAR number the app/page shows == the published data.

Reads what the app reads (GitHub raw, fallback branch): data/manifest.json, data/metrics.json,
data/now/summary.json. Checks
  1. displayed numbers (table SHOWN) == the metrics key, rounded the way it is displayed;
     the manifest's per-case metrics copy (if it has the key) == metrics.json;
  2. every nisar_frame layer has bounds (w<e, s<n), stretch_db {db_min<db_max}, coverage_pct 0..100;
  3. layer ids unique (nisar_* ids per case; (id, date, issued, kind) for all layers);
  4. every NISAR PNG (ids with 'nisar', incl. tpi_flood_nisar / nisar_water_*) answers HTTP 200;
  5. (info) which submission copy files on origin/main contain each displayed string.
Exit 1 on any failure. No writes.

    python3 tools/qa_nisar_consistency.py [--base URL] [--no-png]
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import subprocess
import sys
import urllib.error
import urllib.request

BASE = "https://raw.githubusercontent.com/r2l2pj/tipping-point/fallback/data"
COPY = ["README.md", "app/README.md", "app/submission/NUMBERS.md", "app/submission/project-page.md"]

# (where shown, displayed text, source, key path, formatter)
R0 = lambda v: f"{round(v):,}"                      # noqa: E731
P0 = lambda v: f"{round(v)}"                        # noqa: E731
F1 = lambda v: f"{v:.1f}"                           # noqa: E731
I0 = lambda v: f"{int(v)}"                          # noqa: E731
SHOWN = [
    ("Uru NISAR card: water, 12-d composite (km²)", "1,106", "metrics", "rs_uruguai_2026.nisar_water_km2_case_box", R0),
    ("Uru NISAR card: raised on floodplain (km²)", "822", "metrics", "rs_uruguai_2026.nisar_raised_km2_floodplain", R0),
    ("Uru agreement base: Oct 12 single pass (km²)", "612", "metrics", "rs_uruguai_2026.nisar_agreement_water_km2_single_pass", R0),
    ("Uru Critical hit (%)", "9", "metrics", "rs_uruguai_2026.nisar_agreement_win_critical_hit_pct", P0),
    ("Uru Critical land flagged (%)", "4", "metrics", "rs_uruguai_2026.nisar_agreement_win_critical_area_pct", P0),
    ("Uru Critical lift (×)", "2.2", "metrics", "rs_uruguai_2026.nisar_agreement_win_critical_lift", F1),
    ("Uru Critical vs terrain (M1 ×)", "1.7", "metrics", "rs_uruguai_2026.nisar_agreement_win_critical_m1", F1),
    ("Uru k days before", "10", "metrics", "rs_uruguai_2026.nisar_agreement_win_critical_days_before", I0),
    ("Uru headline pass date", "2026-10-12", "metrics", "rs_uruguai_2026.nisar_agreement_pass_date", str),
    ("Uru High+ hit, same day (%)", "17", "sameday", "rs_uruguai_2026.alto_plus.hit_pct", P0),
    ("Uru High+ land, same day (%)", "18", "sameday", "rs_uruguai_2026.alto_plus.area_pct", P0),
    ("Uru off-floodplain share, single pass (%)", "15", "metrics", "rs_uruguai_2026.nisar_agreement_outside_floodplain_pct", P0),
    ("Uru pass-day hit (%)", "0", "metrics", "rs_uruguai_2026.nisar_agreement_obs_hit_pct", P0),
    ("WA NISAR water, single pass (km²)", "2.0", "metrics", "wa_2025.nisar_agreement_water_km2", F1),
    ("WA High+ hit, same day as Critical (%)", "79", "sameday", "wa_2025.alto_plus.hit_pct", P0),
    ("WA Critical hit (%)", "51", "metrics", "wa_2025.nisar_agreement_win_critical_hit_pct", P0),
    ("WA k days before (Critical best day)", "4", "metrics", "wa_2025.nisar_agreement_win_critical_days_before", I0),
    ("WA High+ land, same day (%)", "9", "sameday", "wa_2025.alto_plus.area_pct", P0),
    ("WA Critical land (%)", "6", "sameday", "wa_2025.critico.area_pct", P0),
    ("WA High+ lift, same day (×)", "8.7", "sameday", "wa_2025.alto_plus.lift", F1),
    ("WA Critical lift (×)", "8.1", "sameday", "wa_2025.critico.lift", F1),
    ("WA High+ vs terrain, same day", "0.86", "sameday", "wa_2025.alto_plus.ratio_vs_terrain", lambda v: f"{v:.2f}"),
    ("WA Critical vs terrain", "0.55", "sameday", "wa_2025.critico.ratio_vs_terrain", lambda v: f"{v:.2f}"),
    ("WA off-floodplain share (%)", "9.9", "metrics", "wa_2025.nisar_agreement_outside_floodplain_pct", F1),
    ("Now NISAR water (km²)", "227", "summary", "nisar_water.water_km2", R0),
    ("Now off-floodplain share (%)", "88.8", "summary", "nisar_water.outside_floodplain_pct", F1),
]


def get_json(url: str):
    with urllib.request.urlopen(url, timeout=60) as r:
        return json.load(r)


def dig(d, path: str):
    for k in path.split("."):
        if not isinstance(d, dict) or k not in d:
            return None
        d = d[k]
    return d


def sameday(met: dict, path: str):
    """<case>.<cls>.<field> on the Critical best day of window_12d (one best day for both cuts)."""
    case, cls, field = path.split(".")
    w = dig(met, f"{case}.nisar_agreement.window_12d") or {}
    day = dig(w, "best_day.critico.day")
    return dig((w.get("per_day") or {}).get(day) or {}, f"{cls}.{field}") if day else None


def status(url: str) -> int:
    req = urllib.request.Request(url, method="HEAD")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:  # noqa: BLE001
        return -1


def png_url(base: str, case: str, png: str) -> str:
    return f"{base}/now/{png}" if case == "now" else f"{base}/{png}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base", default=BASE)
    ap.add_argument("--no-png", action="store_true")
    ap.add_argument("--repo", default=".", help="git checkout for the copy grep (origin/main)")
    a = ap.parse_args(argv)
    man = get_json(f"{a.base}/manifest.json")
    met = get_json(f"{a.base}/metrics.json")["cases"]
    summ = get_json(f"{a.base}/now/summary.json")
    fails, infos = [], []

    # 1. displayed numbers
    for label, shown, src, path, fmt in SHOWN:
        v = sameday(met, path) if src == "sameday" else dig(met if src == "metrics" else summ, path)
        if v is None:
            fails.append(f"[numbers] {label}: shown {shown}, key {src}:{path} MISSING/null")
            continue
        got = fmt(v)
        if got != shown:
            fails.append(f"[numbers] {label}: shown {shown} but {src}:{path} = {v} → displays as {got}")
        else:
            infos.append(f"ok {label}: {shown} == {src}:{path} ({v})")
        if src == "metrics" and "." in path:
            case, key = path.split(".", 1)
            mv = dig((man["cases"].get(case) or {}).get("metrics") or {}, key)
            if mv is not None and mv != v:
                fails.append(f"[numbers] {label}: manifest cases.{case}.metrics.{key} = {mv} != metrics.json {v}")

    # 2-3. frames + ids
    pngs = []
    for case, c in man["cases"].items():
        layers = c.get("layers") or []
        seen, dup = set(), set()
        for e in layers:
            k = (e.get("id"), e.get("date"), e.get("issued"), e.get("kind"), e.get("datetime"), e.get("png"))
            (dup if k in seen else seen).add(k)
        if dup:
            fails.append(f"[ids] {case}: {len(dup)} duplicate (id,date,issued,kind,datetime,png): {sorted(map(str, dup))[:5]}")
        nid = [e["id"] for e in layers if "nisar" in str(e.get("id"))]
        nd = sorted({i for i in nid if nid.count(i) > 1 and not i.startswith("tpi_flood_nisar")})
        if nd:
            fails.append(f"[ids] {case}: repeated nisar ids {nd[:8]}")
        for e in layers:
            if str(e.get("kind", "")).startswith("nisar_frame"):   # incl. nisar_frame_uy (L67 box)
                fid = f"{case}:{e.get('id')}"
                b = e.get("bounds")
                if not (isinstance(b, list) and len(b) == 4 and b[0] < b[2] and b[1] < b[3]):
                    fails.append(f"[frame] {fid}: bad/missing bounds {b}")
                sd = e.get("stretch_db")
                if not (isinstance(sd, dict) and sd.get("db_min") is not None and sd.get("db_max") is not None
                        and sd["db_min"] < sd["db_max"]):
                    fails.append(f"[frame] {fid}: missing stretch_db (legend db {dig(e, 'legend.db_min')}..{dig(e, 'legend.db_max')})")
                cp = e.get("coverage_pct")
                if not (isinstance(cp, (int, float)) and 0 <= cp <= 100):
                    fails.append(f"[frame] {fid}: missing/bad coverage_pct {cp}")
            if "nisar" in str(e.get("id")) and e.get("png"):
                pngs.append((case, e["id"], png_url(a.base, case, e["png"])))

    # 4. PNG 200
    if not a.no_png:
        with cf.ThreadPoolExecutor(16) as ex:
            codes = list(ex.map(lambda p: status(p[2]), pngs))
        bad = [(p, s) for p, s in zip(pngs, codes) if s != 200]
        for (case, i, u), s in bad:
            fails.append(f"[png] {case}:{i} HTTP {s} {u}")
        infos.append(f"png: {len(pngs) - len(bad)}/{len(pngs)} NISAR PNGs answer 200")

    # 5. copy grep (info)
    for label, shown, *_ in SHOWN:
        try:
            out = subprocess.run(["git", "-C", a.repo, "grep", "-l", "-F", shown, "origin/main", "--", *COPY],
                                 capture_output=True, text=True).stdout.split()
        except Exception:  # noqa: BLE001
            out = []
        infos.append(f"copy {shown!r} ({label}): {', '.join(x.split(':', 1)[1] for x in out) or '—'}")

    print("\n".join(infos))
    print(f"\n{len(fails)} FAILURE(S)" if fails else "\nALL CHECKS PASS")
    print("\n".join(fails))
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
