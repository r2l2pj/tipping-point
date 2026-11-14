"""gee.tpi — formula breakpoints, geomean weights, export plan, CLI."""
import re

from gee import tpi


def _pow_map(log):
    """{operand_name: exponent} from the recording fake's .pow() calls."""
    return {c[0].split(".")[0]: c[1][0] for c in log if c[0].endswith(".pow")}


def test_rain_score_breakpoints(fake_ee):
    log = fake_ee.log
    rain3 = fake_ee.node("rain3")
    p90, p95, p99 = (fake_ee.node(n) for n in ("p90", "p95", "p99"))
    tpi.rain_score(rain3, p90, p95, p99)
    gte = {c[1][0].name for c in log if c[0] == "rain3.gte"}
    assert gte == {"p90", "p95", "p99"}          # the three breakpoints
    # mid segment tops at 0.5 (*0.5), high segment adds 0.5, then clamp [0,1]
    assert any(c[0].endswith(".multiply") and c[1] == (0.5,) for c in log)
    assert any(c[0].endswith(".add") and c[1] == (0.5,) for c in log)
    assert any(c[0].endswith(".clamp") and c[1] == (0, 1) for c in log)


def test_geomean_config_weights(fake_ee):
    log = fake_ee.log
    r, s, t = (fake_ee.node(n) for n in ("r", "s", "t"))
    tpi.geomean(r, s, t)                          # defaults to config.yaml
    w = _pow_map(log)
    assert w == {"r": 0.3333, "s": 0.3333, "t": 0.3334}
    assert abs(sum(w.values()) - 1) < 1e-6


def test_geomean_explicit_and_uniform_weights(fake_ee):
    log = fake_ee.log
    r, s, t = (fake_ee.node(n) for n in ("r", "s", "t"))
    tpi.geomean(r, s, t, [0.5, 0.3, 0.2])
    assert _pow_map(log) == {"r": 0.5, "s": 0.3, "t": 0.2}
    log.clear()
    tpi.geomean(r, s, t, [1 / 3, 1 / 3, 1 / 3])   # uniform 1/3 (plain geomean)
    assert all(abs(v - 1 / 3) < 1e-12 for v in _pow_map(log).values())


def test_band_names_are_window_dates():
    names = tpi.band_names("wa_2025")
    assert names[0] == "20251130"
    assert names[10] == "20251210"                # event day
    assert len(names) == 21
    assert all(re.fullmatch(r"\d{8}", n) for n in names)


def test_export_plan_targets():
    plan = tpi.export_plan("wa_2025")
    assert plan["event_id"] == "wa-2025-12"
    assert len(plan["dates"]) == 21
    assert len(plan["exports"]) >= 3
    paths = {e.get("path") for e in plan["exports"]}
    # delta L4: outputs under data/<case-id>/, not data/<event-id>/
    assert "data/wa_2025/cog/wa_2025_tpi_slide_20251210.tif" in paths
    assert "data/wa_2025/cog/wa_2025_tpi_flood_20251210.tif" in paths
    assert "data/wa_2025/series/wa_2025_rain_soil_20251210.json" in paths
    # delta L5: R/S daily series + static T scores as float32 COGs
    assert "data/wa_2025/cog/wa_2025_r_series_20251210.tif" in paths
    assert "data/wa_2025/cog/wa_2025_s_series_20251210.tif" in paths
    assert "data/wa_2025/cog/wa_2025_t_slide_20251210.tif" in paths
    assert "data/wa_2025/cog/wa_2025_t_flood_20251210.tif" in paths
    cogs = [e for e in plan["exports"] if e["kind"] == "drive_cog"]
    assert {e["image"] for e in cogs} == {
        "TPI_slide", "TPI_flood", "R", "S", "T_slide", "T_flood"}
    assert {e["bands"] for e in cogs} == {21, 1}
    assets = {e.get("asset") for e in plan["exports"]}
    assert "wa_2025_tpi_slide_series" in assets
    assert "wa_2025_tpi_flood_series" in assets
    assert tpi.clim_asset_name("wa_2025") in assets


def test_tpi_at_carries_score_bands(fake_ee):
    """tpi_at emits R, S, TPI_slide, TPI_flood bands (delta L5 selects R/S)."""
    log = fake_ee.log
    t_slide = fake_ee.node("t_slide")
    t_flood = fake_ee.node("t_flood")
    clim = fake_ee.node("clim")
    tpi.tpi_at("2025-12-10", clim, t_slide, t_flood)
    renames = {c[1][0] for c in log
               if c[0].endswith(".rename") and c[1] and isinstance(c[1][0], str)}
    assert {"R", "S", "TPI_slide", "TPI_flood"} <= renames


def test_dry_run_wa_lists_21_dates_and_exports(capsys, monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("ee.Initialize must not run under --dry-run")

    monkeypatch.setattr(tpi.common, "init", _boom)
    assert tpi.main(["--case", "wa_2025", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert len(re.findall(r"\d{4}-\d{2}-\d{2}", out)) == 21
    assert out.count("export:") >= 3
    assert "2025-11-30" in out and "2025-12-20" in out


def test_tasks_prints_export_params(capsys, monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("ee.Initialize must not run under --tasks")

    monkeypatch.setattr(tpi.common, "init", _boom)
    assert tpi.main(["--case", "rs_2024", "--tasks"]) == 0
    out = capsys.readouterr().out
    assert '"description": "rs_2024_tpi_slide_20240502"' in out
    assert '"bands": 16' in out
