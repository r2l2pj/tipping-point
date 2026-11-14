"""Offline tests for fetch.edl — EARTHDATA_TOKEN-first wiring, no network."""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fetch import edl

CRED_VARS = (
    "EARTHDATA_TOKEN",
    "EDL_USER",
    "EDL_PASS",
    "EARTHDATA_USERNAME",
    "EARTHDATA_PASSWORD",
    "NETRC",
)


def _clear_creds(monkeypatch, tmp_path):
    """No EDL env vars and no ~/.netrc."""
    for var in CRED_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))


class _FakeEarthaccess:
    def __init__(self, err=None):
        self.calls = []
        self.err = err

    def login(self, **kw):
        self.calls.append(kw)
        if self.err:
            raise self.err
        return "AUTH"


def test_edl_token_and_headers(monkeypatch):
    monkeypatch.delenv("EARTHDATA_TOKEN", raising=False)
    assert edl.edl_token() is None
    assert edl.edl_auth_headers() == {}
    monkeypatch.setenv("EARTHDATA_TOKEN", "faketoken")
    assert edl.edl_token() == "faketoken"
    assert edl.edl_auth_headers() == {"Authorization": "Bearer faketoken"}


def test_ensure_earthdata_prefers_token(monkeypatch, tmp_path):
    _clear_creds(monkeypatch, tmp_path)
    monkeypatch.setenv("EARTHDATA_TOKEN", "faketoken")
    monkeypatch.setenv("EDL_USER", "u")
    monkeypatch.setenv("EDL_PASS", "p")
    assert edl.ensure_earthdata() == {"token": "faketoken"}


def test_ensure_earthdata_user_fallback(monkeypatch, tmp_path):
    _clear_creds(monkeypatch, tmp_path)
    monkeypatch.setenv("EDL_USER", "u")
    monkeypatch.setenv("EDL_PASS", "p")
    assert edl.ensure_earthdata() == {"user": "u", "password": "p"}
    monkeypatch.delenv("EDL_USER")
    monkeypatch.delenv("EDL_PASS")
    monkeypatch.setenv("EARTHDATA_USERNAME", "eu")
    monkeypatch.setenv("EARTHDATA_PASSWORD", "ep")
    assert edl.ensure_earthdata() == {"user": "eu", "password": "ep"}


def test_ensure_earthdata_netrc_fallback(monkeypatch, tmp_path):
    _clear_creds(monkeypatch, tmp_path)
    netrc = tmp_path / "auth.netrc"
    netrc.write_text(
        "machine urs.earthdata.nasa.gov login u password p\n"
    )
    monkeypatch.setenv("NETRC", str(netrc))
    assert edl.ensure_earthdata() == {"netrc": netrc}
    # a netrc without the EDL host is not credentials
    netrc.write_text("machine example.com login u password p\n")
    with pytest.raises(RuntimeError, match="Earthdata credentials required"):
        edl.ensure_earthdata()


def test_ensure_earthdata_missing(monkeypatch, tmp_path):
    _clear_creds(monkeypatch, tmp_path)
    monkeypatch.setenv("EDL_USER", "u")  # half a pair
    with pytest.raises(RuntimeError, match="EDL_PASS"):
        edl.ensure_earthdata()
    monkeypatch.delenv("EDL_USER")
    with pytest.raises(RuntimeError, match="EARTHDATA_TOKEN"):
        edl.ensure_earthdata()


def test_earthaccess_login_token(monkeypatch, tmp_path):
    """EARTHDATA_TOKEN -> environment strategy; earthaccess reads it itself."""
    _clear_creds(monkeypatch, tmp_path)
    monkeypatch.setenv("EARTHDATA_TOKEN", "faketoken")
    fake = _FakeEarthaccess()
    monkeypatch.setitem(sys.modules, "earthaccess", fake)
    assert edl.earthaccess_login() == "AUTH"
    assert fake.calls == [{"strategy": "environment"}]
    assert "EARTHDATA_USERNAME" not in os.environ


def test_earthaccess_login_maps_edl_user(monkeypatch, tmp_path):
    _clear_creds(monkeypatch, tmp_path)
    monkeypatch.setenv("EDL_USER", "u")
    monkeypatch.setenv("EDL_PASS", "p")
    fake = _FakeEarthaccess()
    monkeypatch.setitem(sys.modules, "earthaccess", fake)
    assert edl.earthaccess_login() == "AUTH"
    assert fake.calls == [{"strategy": "environment"}]
    assert os.environ["EARTHDATA_USERNAME"] == "u"
    assert os.environ["EARTHDATA_PASSWORD"] == "p"


def test_earthaccess_login_netrc(monkeypatch, tmp_path):
    _clear_creds(monkeypatch, tmp_path)
    netrc = tmp_path / "auth.netrc"
    netrc.write_text("machine urs.earthdata.nasa.gov login u password p\n")
    monkeypatch.setenv("NETRC", str(netrc))
    fake = _FakeEarthaccess()
    monkeypatch.setitem(sys.modules, "earthaccess", fake)
    edl.earthaccess_login()
    assert fake.calls == [{"strategy": "netrc"}]


def test_earthaccess_login_failure_clean(monkeypatch, tmp_path):
    _clear_creds(monkeypatch, tmp_path)
    monkeypatch.setenv("EARTHDATA_TOKEN", "faketoken")
    monkeypatch.setitem(
        sys.modules, "earthaccess", _FakeEarthaccess(err=ValueError("nope"))
    )
    with pytest.raises(RuntimeError, match="Earthdata login failed"):
        edl.earthaccess_login()


def test_gdal_auth_env_token_bearer(monkeypatch, tmp_path):
    _clear_creds(monkeypatch, tmp_path)
    monkeypatch.setenv("EARTHDATA_TOKEN", "faketoken")
    env = edl.gdal_auth_env()
    assert env["GDAL_HTTP_HEADERS"] == "Authorization: Bearer faketoken"
    assert "GDAL_HTTP_USERPWD" not in env
    assert "GDAL_HTTP_NETRC" not in env


def test_gdal_auth_env_fallbacks(monkeypatch, tmp_path):
    _clear_creds(monkeypatch, tmp_path)
    env = edl.gdal_auth_env({"user": "u", "password": "p"})
    assert env["GDAL_HTTP_USERPWD"] == "u:p"
    assert "GDAL_HTTP_HEADERS" not in env
    env = edl.gdal_auth_env({"netrc": tmp_path / "n"})
    assert env["GDAL_HTTP_NETRC"] == "YES"
    assert "GDAL_HTTP_HEADERS" not in env


def test_asf_session_uses_bearer(monkeypatch, tmp_path):
    """auth_with_token is called with the EARTHDATA_TOKEN value (offline)."""
    _clear_creds(monkeypatch, tmp_path)
    monkeypatch.setenv("EARTHDATA_TOKEN", "faketoken")
    import asf_search as asf

    seen = {}

    def _auth(self, token):
        seen["token"] = token
        return self

    monkeypatch.setattr(asf.ASFSession, "auth_with_token", _auth)
    session = edl.asf_session()
    assert isinstance(session, asf.ASFSession)
    assert seen["token"] == "faketoken"


def test_asf_session_no_token(monkeypatch, tmp_path):
    _clear_creds(monkeypatch, tmp_path)
    monkeypatch.setenv("EDL_USER", "u")
    monkeypatch.setenv("EDL_PASS", "p")
    assert edl.asf_session() is None
