"""Exploratory data analysis for the landslide models (run BEFORE training).

    python -m ml.local eda            # after `table`
    python -m ml.eda.run [--smoke]

Reads the feature tables (DATA_DIR/tables) and the inventory; writes
``ml/eda/figures/*.png`` and ``ml/eda/eda_summary.json`` (numbers quoted in
.crew/research/ml-eda.md). Never uses TEST labels for any decision: effect
sizes, correlations, VIF and univariate AUC are computed on TRAIN rows only;
TEST appears only in the class-balance counts.

Sections: (1) inventory, (2) class balance per split / horizon, (3) positive vs
negative distributions + effect sizes (KS, SMD), (4) Spearman + |rho|>0.8 pairs
+ VIF, (5) univariate ROC-AUC + landslide rate vs binned rain and rain×soil,
(6) missingness, (7) spatial/temporal autocorrelation of events.
"""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats
from sklearn.metrics import roc_auc_score

from ml.local import settings as S
from ml.local.labels import chord_to_km, unit_xyz

log = logging.getLogger(__name__)
OUT = Path(__file__).resolve().parent
FIG = OUT / "figures"
NUMERIC_3A = S.TERRAIN + ["hand_m", "ndvi"] + S.CLIMATE + S.SOIL_TEXTURE
NUMERIC_3B = S.RAIN + S.SOIL_DYN + ["sm_rootzone_wetness"] + S.SOIL_TEXTURE + S.TERRAIN + ["hand_m", "ndvi"]


def _plt():
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        return plt
    except ImportError:
        log.warning("matplotlib not installed — EDA writes numbers only, no PNGs")
        return None


def _read(name: str) -> pd.DataFrame | None:
    p = S.DATA_DIR / "tables" / f"{name}.csv"
    if not p.exists():
        return None
    df = pd.read_csv(p)
    for c in ("date", "issue_date"):
        if c in df:
            df[c] = pd.to_datetime(df[c], errors="coerce")
    return df


def _cols(df, cols):
    return [c for c in cols if c in df.columns and pd.api.types.is_numeric_dtype(df[c])]


# ------------------------------------------------------------------ (1) inventory
def inventory_section(inv: pd.DataFrame, plt) -> dict:
    inv = inv.copy()
    inv["date"] = pd.to_datetime(inv["date"], errors="coerce")
    inv["year"] = inv["date"].dt.year
    inv["month"] = inv["date"].dt.month
    inv["dated"] = inv["date"].notna()
    acc = inv.loc[inv.source == "glc", "acc_km"].map(
        lambda v: "unknown" if pd.isna(v) else f"<= {v:g} km").value_counts().to_dict()
    ev = inv.groupby("event_id").agg(n=("point_id", "size"), date=("date", "min"),
                                     lon=("lon", "mean"), lat=("lat", "mean"),
                                     source=("source", "first")).sort_values("n", ascending=False)
    since = inv[(inv.date >= pd.Timestamp(S.SMAP_START)) & inv.acc_km.le(S.GLC_MAX_ACC_KM)]
    out = {
        "n_points": len(inv),
        "by_source": inv.source.value_counts().to_dict(),
        "dated": int(inv.dated.sum()), "undated": int((~inv.dated).sum()),
        "glc_location_accuracy": acc,
        "by_year": {int(k): int(v) for k, v in inv.year.value_counts().sort_index().items()},
        "by_month": {int(k): int(v) for k, v in inv.month.value_counts().sort_index().items()},
        "n_events": int(inv.event_id.nunique()),
        "top_events": ev.head(25).assign(date=lambda d: d.date.dt.date.astype(str)).reset_index().to_dict("records"),
        "events_since_smap_le5km": int(since.event_id.nunique()),
        "points_since_smap_le5km": len(since),
    }
    if plt:
        fig, ax = plt.subplots(figsize=(10, 5))
        for src, g in inv.groupby("source"):
            ax.scatter(g.lon, g.lat, s=2, label=f"{src} (n={len(g)})", alpha=0.6)
        ax.set_title("Landslide inventory points"); ax.legend(markerscale=5); ax.set_xlabel("lon"); ax.set_ylabel("lat")
        fig.savefig(FIG / "01_inventory_map.png", dpi=120, bbox_inches="tight"); plt.close(fig)
        fig, axs = plt.subplots(1, 2, figsize=(12, 4))
        inv.year.value_counts().sort_index().plot.bar(ax=axs[0], title="points per year")
        inv.month.value_counts().sort_index().plot.bar(ax=axs[1], title="points per month")
        fig.savefig(FIG / "01_inventory_time.png", dpi=120, bbox_inches="tight"); plt.close(fig)
    return out


# ------------------------------------------------------------- (2) class balance
def balance_section(t3a, t3b, hz) -> dict:
    out = {}
    for name, t in (("3a", t3a), ("3b_daily", t3b)):
        if t is None:
            continue
        g = t.groupby("split")["label"].agg(["size", "sum"])
        out[name] = {sp: {"n": int(r["size"]), "pos": int(r["sum"]), "neg": int(r["size"] - r["sum"]),
                          "base_rate": float(r["sum"] / r["size"])} for sp, r in g.iterrows()}
    if hz is not None:
        out["3b_horizons"] = {}
        for h in S.HORIZONS:
            col = f"label_{h}"
            if col not in hz:
                continue
            for sp, g in hz.groupby("split"):
                out["3b_horizons"][f"{h}/{sp}"] = {"n": len(g), "pos": int(g[col].sum()),
                                                   "base_rate": float(g[col].mean())}
    return out


# --------------------------------------------------------- (3) distributions
def effect_sizes(t: pd.DataFrame, cols: list[str]) -> dict:
    out = {}
    for c in cols:
        a = t.loc[t.label == 1, c].dropna().to_numpy(float)
        b = t.loc[t.label == 0, c].dropna().to_numpy(float)
        if len(a) < 3 or len(b) < 3:
            out[c] = {"n_pos": len(a), "n_neg": len(b)}
            continue
        sd = np.sqrt((a.var(ddof=1) + b.var(ddof=1)) / 2) or np.nan
        ks = stats.ks_2samp(a, b)
        out[c] = {"n_pos": len(a), "n_neg": len(b), "median_pos": float(np.median(a)),
                  "median_neg": float(np.median(b)), "smd": float((a.mean() - b.mean()) / sd),
                  "ks": float(ks.statistic), "ks_p": float(ks.pvalue)}
    return out


def dist_plots(t: pd.DataFrame, cols: list[str], tag: str, plt) -> None:
    if not plt or not cols:
        return
    n = len(cols)
    ncol = 4
    fig, axs = plt.subplots(int(np.ceil(n / ncol)), ncol, figsize=(16, 3 * np.ceil(n / ncol)))
    for ax, c in zip(np.ravel(axs), cols, strict=False):
        for lab, color in ((0, "tab:blue"), (1, "tab:red")):
            v = t.loc[t.label == lab, c].dropna()
            if len(v):
                lo, hi = np.nanpercentile(t[c].dropna(), [1, 99]) if t[c].notna().sum() > 10 else (v.min(), v.max())
                ax.hist(v.clip(lo, hi), bins=40, density=True, alpha=0.5, color=color,
                        label="landslide" if lab else "no landslide")
        ax.set_title(c, fontsize=9)
    np.ravel(axs)[0].legend(fontsize=7)
    for ax in np.ravel(axs)[n:]:
        ax.axis("off")
    fig.tight_layout()
    fig.savefig(FIG / f"03_distributions_{tag}.png", dpi=110); plt.close(fig)


def landcover_rates(t: pd.DataFrame) -> dict:
    if "landcover" not in t:
        return {}
    g = t.groupby(t.landcover.round())["label"].agg(["size", "mean"])
    return {int(k): {"n": int(r["size"]), "rate": float(r["mean"])} for k, r in g.iterrows() if np.isfinite(k)}


# ------------------------------------------------------- (4) correlation + VIF
def corr_vif(t: pd.DataFrame, cols: list[str], tag: str, plt) -> dict:
    cols = [c for c in cols if t[c].notna().sum() > 10 and t[c].nunique() > 1]
    if len(cols) < 2:
        return {}
    rho = t[cols].corr(method="spearman")
    pairs = [{"a": a, "b": b, "rho": float(rho.loc[a, b])}
             for i, a in enumerate(cols) for b in cols[i + 1:] if abs(rho.loc[a, b]) > 0.8]
    X = t[cols].dropna()
    vif = {}
    if len(X) > len(cols) + 5:
        Z = (X - X.mean()) / X.std(ddof=0).replace(0, np.nan)
        Z = Z.dropna(axis=1)
        for c in Z.columns:
            others = Z.drop(columns=c).to_numpy()
            y = Z[c].to_numpy()
            A = np.column_stack([np.ones(len(y)), others])
            beta, *_ = np.linalg.lstsq(A, y, rcond=None)
            r2 = 1 - np.sum((y - A @ beta) ** 2) / np.sum((y - y.mean()) ** 2)
            vif[c] = float(1 / max(1e-9, 1 - r2))
    if plt:
        fig, ax = plt.subplots(figsize=(0.5 * len(cols) + 3, 0.5 * len(cols) + 2))
        im = ax.imshow(rho.to_numpy(), vmin=-1, vmax=1, cmap="RdBu_r")
        ax.set_xticks(range(len(cols)), cols, rotation=90, fontsize=8)
        ax.set_yticks(range(len(cols)), cols, fontsize=8)
        fig.colorbar(im, ax=ax, shrink=0.7); ax.set_title(f"Spearman ({tag}, TRAIN)")
        fig.savefig(FIG / f"04_spearman_{tag}.png", dpi=110, bbox_inches="tight"); plt.close(fig)
    return {"high_pairs": pairs, "vif": vif}


# --------------------------------------------------------- (5) univariate signal
def univariate_auc(t: pd.DataFrame, cols: list[str]) -> dict:
    out = {}
    y = t.label.to_numpy()
    for c in cols:
        ok = t[c].notna().to_numpy()
        if ok.sum() < 10 or len(np.unique(y[ok])) < 2:
            continue
        a = roc_auc_score(y[ok], t[c].to_numpy(float)[ok])
        out[c] = {"auc": float(a), "auc_oriented": float(max(a, 1 - a)), "n": int(ok.sum())}
    return dict(sorted(out.items(), key=lambda kv: -kv[1]["auc_oriented"]))


def rate_curves(t: pd.DataFrame, plt) -> dict:
    out = {}
    for c in ("rain_24h", "rain_72h", "rain_7d", "rain_72h_rel", "sm_profile_wetness", "sm_rootzone"):
        if c not in t or t[c].notna().sum() < 20:
            continue
        q = pd.qcut(t[c], q=min(10, t[c].nunique()), duplicates="drop")
        g = t.groupby(q, observed=True)["label"].agg(["size", "mean"])
        out[c] = [{"bin": str(k), "n": int(r["size"]), "rate": float(r["mean"])} for k, r in g.iterrows()]
    soil = "sm_profile_wetness" if "sm_profile_wetness" in t else None
    if soil and "rain_72h_rel" in t and t[[soil, "rain_72h_rel"]].notna().all(axis=1).sum() > 50:
        d = t[[soil, "rain_72h_rel", "label"]].dropna()
        rb = pd.qcut(d.rain_72h_rel, 5, duplicates="drop")
        sb = pd.qcut(d[soil], 4, duplicates="drop")
        piv = d.groupby([rb, sb], observed=True)["label"].mean().unstack()
        cnt = d.groupby([rb, sb], observed=True)["label"].size().unstack()
        for f in (piv, cnt):   # Interval bin labels -> str (JSON keys)
            f.index = f.index.astype(str)
            f.columns = f.columns.astype(str)
        out["rain_x_soil"] = {"rate": piv.round(4).astype(object).where(piv.notna(), None).to_dict(),
                              "n": cnt.astype(object).where(cnt.notna(), None).to_dict()}
        out["rain_x_soil"] = json.loads(json.dumps(out["rain_x_soil"], default=str))
        if plt:
            fig, ax = plt.subplots(figsize=(7, 5))
            im = ax.imshow(piv.to_numpy(), origin="lower", cmap="magma", aspect="auto")
            ax.set_yticks(range(len(piv.index)), [str(i) for i in piv.index], fontsize=7)
            ax.set_xticks(range(len(piv.columns)), [str(i) for i in piv.columns], fontsize=7, rotation=30)
            ax.set_ylabel("72 h rain / local P99 (quintiles)"); ax.set_xlabel("SMAP profile wetness (quartiles)")
            for i in range(piv.shape[0]):
                for j in range(piv.shape[1]):
                    v, n = piv.iat[i, j], cnt.iat[i, j]
                    if np.isfinite(v):
                        ax.text(j, i, f"{v:.0%}\nn={int(n)}", ha="center", va="center", fontsize=7,
                                color="white" if v < 0.5 else "black")
            fig.colorbar(im, ax=ax, label="landslide rate (case-control sample)")
            ax.set_title("Rain × soil: landslide rate (TRAIN, 3b daily rows)")
            fig.savefig(FIG / "05_rain_x_soil.png", dpi=130, bbox_inches="tight"); plt.close(fig)
    if plt and out:
        keys = [k for k in out if k != "rain_x_soil"]
        fig, axs = plt.subplots(1, len(keys), figsize=(4 * len(keys), 3.5), squeeze=False)
        for ax, k in zip(axs[0], keys, strict=True):
            ax.plot(range(len(out[k])), [r["rate"] for r in out[k]], "o-")
            ax.set_title(k, fontsize=9); ax.set_xlabel("decile"); ax.set_ylabel("landslide rate")
        fig.tight_layout(); fig.savefig(FIG / "05_rate_vs_bins.png", dpi=110); plt.close(fig)
    return out


# --------------------------------------------------------------- (6) missingness
def missingness(t: pd.DataFrame, cols: list[str]) -> dict:
    m = {c: {"missing_frac": float(t[c].isna().mean()),
             "missing_pos": float(t.loc[t.label == 1, c].isna().mean()),
             "missing_neg": float(t.loc[t.label == 0, c].isna().mean())} for c in cols if c in t}
    return dict(sorted(m.items(), key=lambda kv: -kv[1]["missing_frac"]))


# ------------------------------------------------------ (7) autocorrelation
def autocorrelation(inv: pd.DataFrame, plt) -> dict:
    from scipy.spatial import cKDTree
    d = inv.dropna(subset=["date"]).copy()
    d["date"] = pd.to_datetime(d["date"])
    out = {}
    if len(d) < 3:
        return out
    xyz = unit_xyz(d.lon, d.lat)
    tree = cKDTree(xyz)
    dist, _ = tree.query(xyz, k=2)
    nn = chord_to_km(dist[:, 1])
    out["nn_km_quantiles"] = {q: float(np.quantile(nn, q)) for q in (0.1, 0.25, 0.5, 0.75, 0.9)}
    # same-day pairs vs distance: share of point pairs within r km that fall within 3 days
    rng = np.random.default_rng(S.SEED)
    sub = d.sample(n=min(len(d), 3000), random_state=S.SEED)
    sx = unit_xyz(sub.lon, sub.lat)
    st = cKDTree(sx)
    days = sub.date.values.astype("datetime64[D]").astype(np.int64)
    curve = []
    for r in (1, 5, 10, 25, 50, 100, 200):
        pairs = np.array(list(st.query_pairs(2 * np.sin(r / (2 * 6371.0)))))
        if len(pairs) == 0:
            curve.append({"r_km": r, "pairs": 0, "same_3d_frac": None})
            continue
        same = np.abs(days[pairs[:, 0]] - days[pairs[:, 1]]) <= 3
        curve.append({"r_km": r, "pairs": len(pairs), "same_3d_frac": float(same.mean())})
    out["pairs_within_r_same_event_frac"] = curve
    # events: spatial extent and duration
    ev = d.groupby("event_id").agg(n=("lon", "size"), lon_span=("lon", np.ptp), lat_span=("lat", np.ptp),
                                   days=("date", lambda s: (s.max() - s.min()).days))
    multi = ev[ev.n > 1]
    out["event_extent_km_p90"] = float(np.quantile(np.hypot(multi.lon_span, multi.lat_span) * 111, 0.9)) if len(multi) else None
    out["event_duration_days_p90"] = float(np.quantile(multi.days, 0.9)) if len(multi) else None
    out["n_multi_point_events"] = len(multi)
    del rng
    if plt:
        fig, ax = plt.subplots(figsize=(6, 4))
        xs = [c["r_km"] for c in curve if c["same_3d_frac"] is not None]
        ys = [c["same_3d_frac"] for c in curve if c["same_3d_frac"] is not None]
        ax.semilogx(xs, ys, "o-"); ax.axvline(50, ls="--", c="grey", label="block 50 km")
        ax.set_xlabel("pair distance ≤ r (km)"); ax.set_ylabel("share of pairs within 3 days")
        ax.set_title("Space-time clustering of dated landslides"); ax.legend()
        fig.savefig(FIG / "07_space_time_clustering.png", dpi=120, bbox_inches="tight"); plt.close(fig)
    return out


# ------------------------------------------------------------------------ main
def main(smoke: bool = False) -> dict:
    FIG.mkdir(parents=True, exist_ok=True)
    plt = _plt()
    inv_p = S.DATA_DIR / "labels" / "inventory.csv"
    inv = pd.read_csv(inv_p) if inv_p.exists() else None
    t3a, t3b, hz = _read("table_3a"), _read("table_3b"), _read("horizon_3b_features")
    res: dict = {"smoke": smoke, "data_dir": str(S.DATA_DIR)}
    if inv is not None:
        res["1_inventory"] = inventory_section(inv, plt)
        res["7_autocorrelation"] = autocorrelation(inv, plt)
    res["2_balance"] = balance_section(t3a, t3b, hz)
    for tag, t, cols in (("3a", t3a, NUMERIC_3A), ("3b", t3b, NUMERIC_3B)):
        if t is None:
            continue
        tr = t[t.split == "train"]
        c = _cols(tr, cols)
        res[f"3_effect_sizes_{tag}"] = effect_sizes(tr, c)
        res[f"3_landcover_rates_{tag}"] = landcover_rates(tr)
        dist_plots(tr, c, tag, plt)
        res[f"4_corr_{tag}"] = corr_vif(tr, c, tag, plt)
        res[f"5_univariate_auc_{tag}"] = univariate_auc(tr, c)
        res[f"6_missingness_{tag}"] = missingness(t, c + ["landcover"])
    if t3b is not None:
        res["5_rate_curves_3b"] = rate_curves(t3b[t3b.split == "train"], plt)
        res["3b_negative_types"] = t3b.groupby("neg_type")["label"].size().to_dict()
        res["3b_region_counts"] = t3b[t3b.label == 1].groupby("region").size().to_dict()
    (OUT / "eda_summary.json").write_text(json.dumps(res, indent=2, default=str))
    log.info("EDA -> %s", OUT / "eda_summary.json")
    return res


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--smoke", action="store_true")
    logging.basicConfig(level=logging.INFO)
    main(smoke=ap.parse_args().smoke)
