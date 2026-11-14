import pytest
from tp import config


def test_load_config_reads_repo_config():
    cfg = config.load_config()
    assert set(cfg) >= {"cases", "thresholds", "seed"}
    assert cfg["cases"]["rs_2024"]["event_date"].isoformat() == "2024-05-02"


def test_load_config_cached():
    assert config.load_config() is config.load_config()


def test_thresholds_accessors():
    assert config.thresholds()["flood_db_drop"] == -3
    assert config.threshold("water_db_max") == -15
    assert config.threshold("hand_range_m") == [5, 15]
    assert config.threshold("slope_range_deg") == [15, 30]
    assert config.threshold("coherence_drop") == 0.3
    assert config.threshold("missing_key", 42) == 42
    with pytest.raises(KeyError):
        config.thresholds()["missing_key"]


def test_case_accessor():
    wa = config.case("wa_2025")
    assert wa["aoi"] == [-122.6, 47.0, -121.5, 48.5]
    assert wa["window_before_days"] == 10
    with pytest.raises(KeyError, match="unknown case"):
        config.case("xx_1999")


def test_case_id_resolution():
    assert config.case_id("rs_2024") == "rs_2024"
    assert config.case_id("wa_2025") == "wa_2025"
    assert config.case_id("rs-2024-05") == "rs_2024"
    assert config.case_id("wa-2025-12") == "wa_2025"
    with pytest.raises(KeyError, match="unknown"):
        config.case_id("xx-1999-01")


def test_event_id_resolution():
    assert config.event_id("rs_2024") == "rs-2024-05"
    assert config.event_id("wa_2025") == "wa-2025-12"
    assert config.event_id("rs-2024-05") == "rs-2024-05"


def test_seed_accessor(monkeypatch):
    assert config.seed() == 42
    monkeypatch.setattr(config, "load_config", lambda *a, **k: {})
    assert config.seed() == 42


def test_climatology_accessor():
    clim = config.climatology()
    assert clim["years"] == [2001, 2023]
    assert clim["window_days"] == 3
    assert clim["doy_offset_days"] == 30
    assert clim["doy_offset_step"] == 3


def test_migrated_threshold_keys():
    assert config.threshold("slope_flood_max_deg") == 5
    assert config.threshold("bright_rise_db") == 3
    assert config.threshold("mmu_ha") == 3
    assert config.threshold("coh_max_elev_m") == 600
    assert config.threshold("hv_drop_db") == 2


def test_load_config_custom_path(tmp_path):
    p = tmp_path / "other.yaml"
    p.write_text("thresholds:\n  flood_db_drop: -7\ncases: {}\n")
    assert config.load_config(p)["thresholds"]["flood_db_drop"] == -7
    # repo default unaffected by a cached custom path
    assert config.threshold("flood_db_drop") == -3
