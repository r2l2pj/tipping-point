"""gee.common pinned API — pure-python parts and fake-ee construction."""
import importlib
import json
import sys
from types import SimpleNamespace

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


@pytest.mark.parametrize("module_name", ["common", "tpi"])
def test_fake_ee_patches_duplicate_modules(monkeypatch, request, module_name):
    import gee

    importlib.import_module("gee.tpi")
    original = importlib.import_module(f"gee.{module_name}")
    monkeypatch.setattr(gee, module_name, getattr(gee, module_name))
    stub_ee = SimpleNamespace()
    with monkeypatch.context() as reimport:
        reimport.delitem(sys.modules, f"gee.{module_name}")
        reimport.setitem(sys.modules, "ee", stub_ee)
        duplicate = importlib.import_module(f"gee.{module_name}")
    assert sys.modules[f"gee.{module_name}"] is original
    assert getattr(gee, module_name) is duplicate
    assert duplicate is not original
    fake = request.getfixturevalue("fake_ee")
    assert original.ee is fake
    assert duplicate.ee is fake
    assert common.ee is fake
    common.aoi_geom("wa_2025")
    assert fake.log[-1][0] == "ee.Geometry.Rectangle"


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


@pytest.mark.parametrize("file_env,json_env", [(True, False), (False, True), (True, True)])
def test_ee_init_credentials(fake_ee, monkeypatch, tmp_path, file_env, json_env):
    key_data = json.dumps({"client_email": "fake@example.invalid", "private_key": "fake-key"})
    key_file = tmp_path / "fake-sa.json"
    key_file.write_text(key_data)
    monkeypatch.setenv("GEE_PROJECT", "env-project")
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    monkeypatch.delenv("GCP_SA_KEY", raising=False)
    if file_env:
        monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", str(key_file))
    if json_env:
        monkeypatch.setenv("GCP_SA_KEY", key_data)
    assert common.ee_init() == "env-project"
    cred_call, init_call = fake_ee.log
    assert cred_call == (
        "ee.ServiceAccountCredentials", ("fake@example.invalid",),
        {"key_file": str(key_file)} if file_env else {"key_data": key_data},
    )
    assert init_call[0] == "ee.Initialize"
    assert init_call[2]["project"] == "env-project"
    assert init_call[2]["credentials"].name == "ee.ServiceAccountCredentials()"


@pytest.mark.parametrize("failure", ["missing", "invalid", "credentials", "initialize"])
def test_ee_init_falls_back_to_json(fake_ee, monkeypatch, tmp_path, failure):
    key_data = json.dumps({"client_email": "fake@example.invalid", "private_key": "fake-key"})
    key_file = tmp_path / "stale.json"
    if failure != "missing":
        key_file.write_text("invalid-json" if failure == "invalid" else key_data)
    monkeypatch.setenv("GEE_PROJECT", "env-project")
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", str(key_file))
    monkeypatch.setenv("GCP_SA_KEY", key_data)
    attempts = []
    initialized = []

    def credentials(email, **kwargs):
        attempts.append(kwargs)
        if failure == "credentials" and "key_file" in kwargs:
            raise ValueError("invalid file key")
        return kwargs

    def initialize(**kwargs):
        initialized.append(kwargs)
        if failure == "initialize" and "key_file" in kwargs["credentials"]:
            raise RuntimeError("permission denied")

    monkeypatch.setattr(fake_ee, "ServiceAccountCredentials", credentials)
    monkeypatch.setattr(fake_ee, "Initialize", initialize)
    assert common.ee_init() == "env-project"
    assert attempts[-1] == {"key_data": key_data}
    assert initialized[-1] == {"credentials": {"key_data": key_data}, "project": "env-project"}
    assert len(attempts) == (2 if failure in ("credentials", "initialize") else 1)


def test_ee_init_reports_all_failed_sources(fake_ee, monkeypatch, tmp_path):
    monkeypatch.setenv("GEE_PROJECT", "env-project")
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", str(tmp_path / "missing.json"))
    monkeypatch.setenv("GCP_SA_KEY", "invalid-json")
    with pytest.raises(RuntimeError, match="GOOGLE_APPLICATION_CREDENTIALS.*GCP_SA_KEY"):
        common.ee_init()
    assert not fake_ee.log


@pytest.mark.parametrize("project", ["", "env-project"])
def test_ee_init_missing_env(fake_ee, monkeypatch, project):
    monkeypatch.setenv("GEE_PROJECT", project)
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    monkeypatch.delenv("GCP_SA_KEY", raising=False)
    missing = "GOOGLE_APPLICATION_CREDENTIALS.*GCP_SA_KEY" if project else "GEE_PROJECT"
    with pytest.raises(RuntimeError, match=missing):
        common.ee_init()
    assert not fake_ee.log


@pytest.mark.parametrize("key_data", ["not-json", "{}", '{"client_email": "fake@example.invalid"}'])
def test_ee_init_invalid_json_env(fake_ee, monkeypatch, key_data):
    monkeypatch.setenv("GEE_PROJECT", "env-project")
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    monkeypatch.setenv("GCP_SA_KEY", key_data)
    with pytest.raises(RuntimeError, match="Invalid GCP_SA_KEY"):
        common.ee_init()
    assert not fake_ee.log


def test_ee_init_missing_file(fake_ee, monkeypatch, tmp_path):
    monkeypatch.delenv("GCP_SA_KEY", raising=False)
    monkeypatch.setenv("GEE_PROJECT", "env-project")
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", str(tmp_path / "missing.json"))
    with pytest.raises(RuntimeError, match="Invalid GOOGLE_APPLICATION_CREDENTIALS"):
        common.ee_init()
    assert not fake_ee.log


def test_ee_init_role_error(fake_ee, monkeypatch):
    monkeypatch.setenv("GEE_PROJECT", "env-project")
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    monkeypatch.setenv("GCP_SA_KEY", json.dumps({
        "client_email": "fake@example.invalid", "private_key": "fake-key",
    }))

    def denied(**kwargs):
        raise RuntimeError("permission denied")

    monkeypatch.setattr(fake_ee, "Initialize", denied)
    with pytest.raises(RuntimeError, match="GEE_PROJECT.*GCP_SA_KEY.*roles"):
        common.ee_init()


def test_stamp_reaches_hls_export(monkeypatch):
    from gee import hls_scar

    class Image:
        def __init__(self, props=None):
            self.props = props or {}

        def set(self, key, value):
            return Image({**self.props, key: value})

    exports = []
    monkeypatch.setattr(hls_scar, "ee", SimpleNamespace(batch=SimpleNamespace(
        Export=SimpleNamespace(image=SimpleNamespace(toAsset=lambda **kw: exports.append(kw)))
    )))
    monkeypatch.setattr(hls_scar, "aoi_geom", lambda case_id: "aoi")
    image = Image()
    hls_scar.export_task("wa_2025", image, project="proj")
    exported = exports[0]["image"]
    assert exported is not image
    assert exported.props["data_date"] == "2025-12-20"
    assert exported.props["updated_at"]
    assert not image.props


def test_stamp_sets_data_date_and_updated_at(fake_ee):
    img = fake_ee.node("img")
    common.stamp(img, "2025-12-20", now="2026-01-01T00:00:00+00:00")
    calls = [(n, a) for n, a, _ in fake_ee.log]
    assert ("img.set", ("data_date", "2025-12-20")) in calls
    assert any(a == ("updated_at", "2026-01-01T00:00:00+00:00")
               for n, a in calls)
