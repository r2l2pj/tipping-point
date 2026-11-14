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


def test_load_config_custom_path(tmp_path):
    p = tmp_path / "other.yaml"
    p.write_text("thresholds:\n  flood_db_drop: -7\ncases: {}\n")
    assert config.load_config(p)["thresholds"]["flood_db_drop"] == -7
    # repo default unaffected by a cached custom path
    assert config.threshold("flood_db_drop") == -3
