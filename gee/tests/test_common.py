"""gee.common pinned API — pure-python parts and fake-ee construction."""
import pytest

from gee import common


def test_event_dates_wa_is_21_days():
    dates = common.event_dates("wa_2025")
    assert len(dates) == 21                     # 10 before + event + 10 after
    assert dates[0] == "2025-11-30"
    assert dates[10] == "2025-12-10"            # event day
    assert dates[-1] == "2025-12-20"


def test_event_dates_rs_is_16_days():
    dates = common.event_dates("rs_2024")
    assert len(dates) == 16                     # 10 before + event + 5 after
    assert dates[0] == "2024-04-22"
    assert dates[10] == "2024-05-02"
    assert dates[-1] == "2024-05-07"


def test_case_cfg_reads_config_yaml():
    cfg = common.case_cfg("wa_2025")
    assert set(cfg) == {"aoi", "event_date",
                        "window_before_days", "window_after_days"}
    assert cfg["aoi"] == [-122.6, 47.0, -121.5, 48.5]
    assert cfg["event_date"].isoformat() == "2025-12-10"
    with pytest.raises(KeyError, match="unknown case"):
        common.case_cfg("xx_1999")


def test_event_id():
    assert common.event_id("rs_2024") == "rs-2024-05"
    assert common.event_id("wa_2025") == "wa-2025-12"


def test_case_dir_is_data_case_key():
    # delta L4: outputs under data/<case-id>/ (tp.config.case_id when it lands)
    assert str(common.case_dir("wa_2025")) == "data/wa_2025"
    assert common.data_dir("rs_2024").name == "rs_2024"
    assert common.data_dir("rs_2024").parent.name == "data"
    with pytest.raises(KeyError, match="unknown case"):
        common.case_dir("xx_1999")


def test_aoi_geom(fake_ee):
    common.aoi_geom("wa_2025")
    call = [c for c in fake_ee.log if c[0] == "ee.Geometry.Rectangle"][-1]
    assert call[1] == ([-122.6, 47.0, -121.5, 48.5],)


def test_asset_path_uses_project(monkeypatch):
    assert (common.asset_path("x", project="proj")
            == "projects/proj/assets/tipping-point/x")
    monkeypatch.setattr(common, "GEE_PROJECT", "")
    with pytest.raises(RuntimeError, match="GEE_PROJECT"):
        common.asset_path("x", project="")


def test_init_with_sa_key_path(fake_ee, monkeypatch, tmp_path):
    key = tmp_path / "sa.json"
    key.write_text('{"client_email": "sa@proj.iam.gserviceaccount.com"}')
    monkeypatch.setenv("GCP_SA_KEY", str(key))
    assert common.init("proj") == "proj"
    names = {c[0] for c in fake_ee.log}
    assert "ee.ServiceAccountCredentials" in names
    assert "ee.Initialize" in names


def test_init_fallback_env_var(fake_ee, monkeypatch, tmp_path):
    key = tmp_path / "sa.json"
    key.write_text("{}")
    monkeypatch.delenv("GCP_SA_KEY", raising=False)
    monkeypatch.setenv("GEE_SERVICE_ACCOUNT_KEY", str(key))
    assert common.init("p") == "p"
    assert "ee.ServiceAccountCredentials" in {c[0] for c in fake_ee.log}


def test_init_no_key_uses_default_creds(fake_ee, monkeypatch):
    monkeypatch.delenv("GCP_SA_KEY", raising=False)
    monkeypatch.delenv("GEE_SERVICE_ACCOUNT_KEY", raising=False)
    assert common.init("p") == "p"
    names = {c[0] for c in fake_ee.log}
    assert "ee.ServiceAccountCredentials" not in names
    assert "ee.Initialize" in names


def test_init_missing_key_file_is_a_clear_error(fake_ee, monkeypatch, tmp_path):
    monkeypatch.setenv("GCP_SA_KEY", str(tmp_path / "nope.json"))
    with pytest.raises(RuntimeError, match="missing file"):
        common.init("p")


def test_init_no_project_is_a_clear_error(fake_ee, monkeypatch):
    monkeypatch.delenv("GEE_PROJECT", raising=False)
    monkeypatch.setattr(common, "GEE_PROJECT", "")
    with pytest.raises(RuntimeError, match="GEE project"):
        common.init()


def test_stamp_sets_data_date_and_updated_at(fake_ee):
    img = fake_ee.node("img")
    common.stamp(img, "2025-12-20", now="2026-01-01T00:00:00+00:00")
    calls = [(n, a) for n, a, _ in fake_ee.log]
    assert ("img.set", ("data_date", "2025-12-20")) in calls
    assert any(a == ("updated_at", "2026-01-01T00:00:00+00:00")
               for n, a in calls)
