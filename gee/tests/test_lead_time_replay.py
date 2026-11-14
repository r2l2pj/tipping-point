"""Offline latency-plan and graph-construction checks."""
from datetime import timedelta
from types import SimpleNamespace

import pandas as pd
import pytest

from gee import lead_time


def test_offline_plan(monkeypatch, capsys):
    monkeypatch.setattr(lead_time.common, "init", lambda *_: pytest.fail("must stay offline"))
    assert lead_time.main(["--case", "rs_2024", "--dry-run"]) == 0
    assert '"step_h": 3' in capsys.readouterr().out
    plan = lead_time.replay_plan("rs_2024")
    assert "system:time_start <= t-75h" in plan["lead_time_assumptions"]["smap"]
    assert "interval end <= t-72h" in plan["lead_time_assumptions"]["smap"]
    assert len(plan["frames"]) == 16 * 8
    first, second, last = plan["frames"][0], plan["frames"][1], plan["frames"][-1]
    assert first["band"] == "20240422T0000"
    assert last["band"] == "20240507T2100"
    assert pd.Timestamp(second["at"]) - pd.Timestamp(first["at"]) == timedelta(hours=3)
    assert pd.Timestamp(first["at"]) - pd.Timestamp(first["imerg_interval_end_lte"]) == timedelta(hours=4)
    assert pd.Timestamp(first["at"]) - pd.Timestamp(first["smap_interval_end_lte"]) == timedelta(hours=72)
    calls = []
    monkeypatch.setattr(lead_time, "run_local", lambda *args: calls.append(args))
    assert lead_time.main(["--case", "rs_2024"]) == 0
    assert calls == [("rs_2024", None)]


def test_replay_graph_cutoffs_and_no_geos(monkeypatch):
    calls = []

    class Image:
        def __init__(self, name):
            self.name = name

        def __getattr__(self, method):
            def call(*args):
                calls.append((self.name, method, args))
                return self
            return call

    class Date:
        def __init__(self, at):
            self.at = pd.Timestamp(at)

        def advance(self, amount, unit):
            return Date(self.at + pd.Timedelta(**{unit + "s": amount}))

        def millis(self):
            return self.at.value // 1_000_000

    monkeypatch.setattr(lead_time, "ee", SimpleNamespace(
        Date=Date, ImageCollection=lambda name: Image(name),
        Filter=SimpleNamespace(lte=lambda field, value: (field, value))))
    monkeypatch.setattr(lead_time.tpi, "rain_score", lambda *args: Image("rain"))
    monkeypatch.setattr(lead_time.tpi, "geomean", lambda *args: Image("tpi"))
    lead_time.replay_at("2024-05-01T12:00Z", Image("early"), Image("clim"), (Image("terrain"),) * 2)
    filters = [args for name, method, args in calls if name == "early" and method == "filterDate"]
    assert filters[0][0].at == pd.Timestamp("2024-04-28T08:00Z")
    assert filters[0][1].at == pd.Timestamp("2024-05-01T08:00Z")
    assert ("early", "eq", (144,)) in calls
    smap = lead_time.common.COL_SMAP
    cutoff = next(args[0] for name, method, args in calls if name == smap and method == "filter")
    assert cutoff == ("system:time_start", pd.Timestamp("2024-04-28T09:00Z").value // 1_000_000)
    assert (smap, "sort", ("system:time_start", False)) in calls
    assert (smap, "limit", (1,)) in calls
    assert not any("GEOS" in name for name, _, _ in calls)


def test_local_cube_latency_and_missing_interval():
    import numpy as np

    starts = pd.date_range("2024-04-28T08:00Z", periods=145, freq="30min")
    rates = np.ones((145, 1, 1))
    rates[-1] = 1e6
    kwargs = {"frames": ["2024-05-01T12:00Z"],
              "percentiles": np.array([0, 36, 72]).reshape(3, 1, 1),
              "soil": np.full((1, 1, 1), 0.9), "terrain": np.ones((2, 1, 1))}
    cube = lead_time.local_cube(rates, starts, **kwargs)
    np.testing.assert_allclose(cube, 1)
    missing = lead_time.local_cube(rates[1:], starts[1:], **kwargs)
    assert np.isnan(missing).all()
    rates[0] = np.nan
    assert np.isnan(lead_time.local_cube(rates, starts, **kwargs)).all()


def test_local_archive_writes_dated_rasters(tmp_path, monkeypatch):
    import numpy as np
    import rasterio
    import xarray as xr
    from tp import config, fallback

    from pipeline.fetch import imerg

    frames = [{"at": "2024-05-01T12:00Z", "band": "20240501T1200"}]
    monkeypatch.setattr(lead_time, "replay_plan", lambda _: {"frames": frames})
    monkeypatch.setattr(config, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(lead_time.common, "init", lambda _: "p")
    monkeypatch.setattr(lead_time.common, "aoi_geom", lambda _: "aoi")
    monkeypatch.setattr(imerg, "search_granules_range", lambda *args: ["early"])
    monkeypatch.setattr(imerg, "download_granules", lambda *args: ["archive.h5"])
    rain = xr.DataArray(np.ones((144, 2, 2)), dims=("time", "lat", "lon"), coords={
        "time": pd.date_range("2024-04-28T08:00", periods=144, freq="30min"),
        "lat": [-29.05, -29.15], "lon": [-51.05, -50.95]})
    monkeypatch.setattr(imerg, "open_halfhourly", lambda *args: rain)
    arrays = [np.broadcast_to(np.array([0, 36, 72])[:, None, None], (3, 2, 2)),
              np.ones((2, 2, 2)), np.full((1, 2, 2), 0.9)]
    params = []

    class Image:
        def __getattr__(self, method):
            return lambda *args, **kwargs: self

        def getDownloadURL(self, spec):
            params.append(spec)
            return "fake"

    monkeypatch.setattr(lead_time.tpi, "climatology", lambda *args: Image())
    monkeypatch.setattr(lead_time.tpi, "terrain_scores", lambda: (Image(), Image()))
    monkeypatch.setattr(lead_time, "ee", SimpleNamespace(
        Image=SimpleNamespace(cat=lambda _: Image()), Date=lambda _: Image(),
        ImageCollection=lambda _: Image(), Filter=SimpleNamespace(lte=lambda *args: None)))

    def fetch(url, limit):
        array = arrays.pop(0).astype("float32")
        with rasterio.MemoryFile() as mem:
            with mem.open(driver="GTiff", width=2, height=2, count=len(array),
                          dtype="float32", transform=rasterio.transform.from_origin(-51.1, -29, .1, .1)) as dst:
                dst.write(array)
            return mem.read()

    monkeypatch.setattr(fallback, "_download", fetch)
    lead_time.run_local("rs_2024")
    for kind in ("slide", "flood"):
        path = tmp_path / "data/rs_2024/cog" / f"rs_2024_lead_time_{kind}_3h.tif"
        with rasterio.open(path) as src:
            assert src.descriptions == ("20240501T1200",)
            assert src.crs.to_epsg() == 4326
            np.testing.assert_allclose(src.read(), 1)
            assert "GPM_3IMERGHHE" in src.tags()["precipitation_source"]
    assert len(params) == 3
    assert params[0]["dimensions"] == [2, 2]
