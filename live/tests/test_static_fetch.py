"""static_fetch: source ordering + loud doy substitution (L26-static)."""
import datetime as dt

import numpy as np
import pytest

from live import static_fetch


def test_clim_for_day_nearest_substitution_is_recorded(capsys):
    clim = {318: {"p90": np.ones((2, 2)), "p95": np.ones((2, 2)),
                  "p99": np.ones((2, 2))}}
    static = {"clim": clim}
    day = dt.date(2026, 11, 16)          # doy 320, not fetched
    got = static_fetch.clim_for_day(static, day)
    assert got is clim[318]
    assert static["clim_doy_used"] == {320: 318}
    assert "CLIM SUBSTITUTION" in capsys.readouterr().err


def test_clim_for_day_exact_doy_records_and_stays_quiet(capsys):
    clim = {319: {"p90": np.zeros((1, 1)), "p95": np.zeros((1, 1)),
                  "p99": np.zeros((1, 1))}}
    static = {"clim": clim}
    static_fetch.clim_for_day(static, dt.date(2026, 11, 15))
    assert static["clim_doy_used"] == {319: 319}
    assert "SUBSTITUTION" not in capsys.readouterr().err


def test_read_cog_prefers_release_then_local_then_neon(monkeypatch):
    import rasterio

    seen = []

    class _FakeDS:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return np.ones((3, 2, 2), dtype=np.float32)

    def fake_open(src):
        seen.append(str(src))
        if "releases/download" in str(src):
            raise rasterio.errors.RasterioIOError("404")
        if str(src).startswith("http"):
            return _FakeDS()          # pretend only Neon serves
        raise rasterio.errors.RasterioIOError("missing")

    monkeypatch.setenv("NEON_S3_ENDPOINT", "https://neon.example")
    monkeypatch.setenv("NEON_S3_BUCKET", "b")
    monkeypatch.setattr(rasterio, "open", fake_open)
    arr = static_fetch._read_cog("clim/doy_318.tif")
    assert arr is not None
    assert "releases/download/static-v1/doy_318.tif" in seen[0]
    assert seen[-1].endswith("/data/static/south_brazil/clim/doy_318.tif")
