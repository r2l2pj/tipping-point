"""static_fetch: source ordering + loud doy substitution (L26-static)."""
import datetime as dt

import numpy as np

from live import static_fetch


def test_clim_for_day_nearest_substitution_is_recorded(capsys):
    clim = {318: {"p90": np.ones((2, 2)), "p95": np.ones((2, 2)),
                  "p99": np.ones((2, 2))}}
    static = {"clim": clim}
    day = dt.date(2026, 11, 15)          # leap-calendar doy 320, not fetched
    got = static_fetch.clim_for_day(static, day)
    assert got is clim[318]
    assert static["clim_doy_used"] == {320: 318}
    assert "CLIM SUBSTITUTION" in capsys.readouterr().err


def test_clim_for_day_exact_doy_records_and_stays_quiet(capsys):
    clim = {320: {"p90": np.zeros((1, 1)), "p95": np.zeros((1, 1)),
                  "p99": np.zeros((1, 1))}}
    static = {"clim": clim}
    # 2026-11-15 is tm_yday 319 but leap-calendar doy 320 (= Nov 15 in the
    # 2000-based climatology calendar both products were built on)
    static_fetch.clim_for_day(static, dt.date(2026, 11, 15))
    assert static["clim_doy_used"] == {320: 320}
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


def test_terrain_checks_reject_stale_flat_slope():
    import numpy as np
    from live.static_fetch import terrain_checks
    from live.tpi import terrain_slide
    rng = np.random.default_rng(0)
    stale = rng.gamma(1.0, 1.2, (200, 200))           # p99 ~ 5.5 deg: the stale raster's shape
    c = terrain_checks(stale, terrain_slide(stale))
    assert not c["slope_p99_ok"] and not c["t_slide_ok"]
    sane = rng.gamma(1.5, 4.0, (200, 200))            # p99 ~ 20 deg like static-v1
    c = terrain_checks(sane, terrain_slide(sane))
    assert c["slope_p99_ok"] and c["t_slide_ok"]


def test_terrain_never_reads_precomputed_t_files(monkeypatch):
    import numpy as np
    from live import static_fetch
    reads = []
    slope = np.full((1, 4, 4), 22.5)
    hand = np.full((1, 4, 4), 2.0)

    def fake(rel):
        reads.append(rel)
        return {"terrain/slope_deg.tif": slope, "terrain/hand_m.tif": hand}.get(rel)
    monkeypatch.setattr(static_fetch, "_read_cog", fake)
    t_slide, t_flood, checks = static_fetch._terrain()
    assert reads == ["terrain/slope_deg.tif", "terrain/hand_m.tif"]
    assert np.allclose(t_slide, 0.5) and np.allclose(t_flood, 0.0)  # slope > 5 deg
    assert checks["slope_p99_ok"]


def _write_clim(path, doys=3):
    """tiny whole-year-layout file: 0.1 deg grid over (-58,-34,-57.6,-33.7)
    padded by a cell; band b holds b*10 + a lon ramp."""
    import rasterio
    from rasterio.transform import from_origin

    h, w = 5, 6                       # lat -33.65..-34.05, lon -58.05..-57.55
    lon_c = -58.05 + np.arange(w) * 0.1
    data = np.stack([np.tile((b * 10 + lon_c * 0 + np.arange(w)).astype(
        np.float32), (h, 1)) for b in range(doys * 3)])
    with rasterio.open(path, "w", driver="GTiff", width=w, height=h,
                       count=doys * 3, dtype="float32", crs="EPSG:4326",
                       transform=from_origin(-58.1, -33.6, 0.1, 0.1)) as ds:
        ds.write(data)
    return lon_c


def test_clim_from_file_reads_doy_bands_and_interpolates(tmp_path, monkeypatch):
    import rasterio

    path = tmp_path / "clim_imerg_0p1.tif"
    lon_c = _write_clim(path)
    read_idx = []
    real_open = rasterio.open

    class _Spy:
        def __init__(self, ds):
            self.ds = ds

        def __enter__(self):
            self.ds.__enter__()
            return self

        def __exit__(self, *a):
            return self.ds.__exit__(*a)

        def read(self, idx):
            read_idx.append(list(idx))
            return self.ds.read(idx)

        def __getattr__(self, k):
            return getattr(self.ds, k)

    monkeypatch.setattr(rasterio, "open", lambda p, *a, **k: _Spy(real_open(p, *a, **k)))
    bounds = (-58.0, -34.0, -57.6, -33.7)
    got = static_fetch._clim_from_file(path, 2, bounds)
    assert read_idx == [[4, 5, 6]]                  # doy 2 -> bands 4..6
    from live.grid import dst_lats_lons
    lats, lons = dst_lats_lons(bounds)
    assert got["p90"].shape == (len(lats), len(lons))
    # linear in lon on cell centres: value = band*10 + (lon - lon_c[0]) / 0.1
    want = 3 * 10 + (lons - lon_c[0]) / 0.1
    for k, name in enumerate(("p90", "p95", "p99")):
        assert np.isfinite(got[name]).all()
        np.testing.assert_allclose(got[name][0], want + k * 10, atol=1e-6)


def test_load_static_prefers_clim_file_and_falls_back(tmp_path, monkeypatch):
    path = tmp_path / "clim_imerg_0p1.tif"
    _write_clim(path, doys=366)
    monkeypatch.delenv("NOW_CLIM_SOURCE", raising=False)
    monkeypatch.setattr(static_fetch, "CLIM_FILE", path)
    monkeypatch.setattr(static_fetch, "_terrain",
                        lambda: (np.zeros((3, 4)), np.zeros((3, 4)), {}))
    legacy = []
    monkeypatch.setattr(static_fetch, "_clim_for_doy",
                        lambda d: legacy.append(d) or {"p90": 1, "p95": 1, "p99": 1})
    bounds = (-58.0, -34.0, -57.6, -33.7)
    st = static_fetch.load_static(bounds, days=[dt.date(2026, 11, 15)])
    assert set(st["clim"]) == {320} and legacy == []
    assert st["clim_source"] == "imerg-final-0p1"

    monkeypatch.setenv("NOW_CLIM_SOURCE", "legacy")
    st = static_fetch.load_static(bounds, days=[dt.date(2026, 11, 15)])
    assert legacy == [320] and st["clim_source"] == "legacy-doy-cogs"


def test_clim_file_downloads_from_release_when_missing(tmp_path, monkeypatch):
    import io

    path = tmp_path / "clim" / "clim_imerg_0p1.tif"
    monkeypatch.delenv("NOW_CLIM_SOURCE", raising=False)
    monkeypatch.setattr(static_fetch, "CLIM_FILE", path)
    urls = []

    def fake_urlopen(url, timeout=None):
        urls.append(url)
        return io.BytesIO(b"tif-bytes")

    monkeypatch.setattr(static_fetch.urllib.request, "urlopen", fake_urlopen)
    assert static_fetch._clim_file() == path
    assert path.read_bytes() == b"tif-bytes"
    assert urls[0].endswith("/static-v1/clim_imerg_0p1.tif")

    path.unlink()

    def boom(url, timeout=None):
        raise OSError("404")

    monkeypatch.setattr(static_fetch.urllib.request, "urlopen", boom)
    assert static_fetch._clim_file() is None
    assert not path.exists() and not path.with_suffix(".part").exists()


def test_leap_doy_calendar():
    """Both climatology products are on the leap-year calendar
    (date(2000,1,1)+doy-1); tm_yday is off by one after Feb in non-leap
    years (2026-11-15 is tm_yday 319 but Nov 15 = leap doy 320)."""
    assert static_fetch.leap_doy(dt.date(2026, 11, 15)) == 320
    assert dt.date(2026, 11, 15).timetuple().tm_yday == 319
    assert static_fetch.leap_doy(dt.date(2024, 11, 15)) == 320
    assert static_fetch.leap_doy(dt.date(2026, 2, 28)) == 59
    assert static_fetch.leap_doy(dt.date(2026, 3, 1)) == 61
    assert static_fetch.leap_doy(dt.date(2024, 2, 29)) == 60
    assert static_fetch.leap_doy(dt.date(2026, 12, 31)) == 366


def test_clim_source_matches_run_now_verified_value():
    from live import run_now
    assert run_now.CLIM_SOURCE_OK == static_fetch.CLIM_SOURCE_NEW


def _clim_tif(path, values_fn, north=-22.4, west=-58.1, res=0.1, h=117, w=102, flip=False):
    import numpy as np
    import rasterio
    from rasterio.transform import from_origin
    lats = north - (np.arange(h) + 0.5) * res
    arr = np.zeros((1098, h, w), dtype="float32")
    for b in range(3):
        arr[(320 - 1) * 3 + b] = values_fn(lats, b)[:, None]
    if flip:                                   # scrambled: rows reversed
        arr = arr[:, ::-1, :]
    with rasterio.open(path, "w", driver="GTiff", width=w, height=h, count=1098,
                       dtype="float32", crs="EPSG:4326",
                       transform=from_origin(west, north, res, res)) as ds:
        ds.write(arr)
    return path


def test_clim_checks_pass_on_matching_file_and_catch_scramble(tmp_path):
    import json
    from live.static_fetch import clim_checks
    ref = {"doy": 320, "tolerance_mm": 1.0, "tolerance_rel": 0.03, "points": {
        "a": {"lon": -51.25, "lat": -30.05, "p90": 30.05, "p95": 31.05, "p99": 32.05},
        "b": {"lon": -49.25, "lat": -25.45, "p90": 25.45, "p95": 26.45, "p99": 27.45},
        "c": {"lon": -52.05, "lat": -23.45, "p90": 23.45, "p95": 24.45, "p99": 25.45}}}
    rp = tmp_path / "ref.json"
    rp.write_text(json.dumps(ref))
    fn = lambda lats, b: -lats + b              # value = |lat| + band offset
    good = clim_checks(_clim_tif(tmp_path / "g.tif", fn), reference=rp)
    assert good["clim_ref_ok"] and good["clim_lat_monotonic"] and good["clim_covers_domain"]
    bad = clim_checks(_clim_tif(tmp_path / "b.tif", fn, flip=True), reference=rp)
    assert not bad["clim_ref_ok"]               # geolocation scramble caught
    small = clim_checks(_clim_tif(tmp_path / "s.tif", fn, h=60), reference=rp)
    assert not small["clim_covers_domain"]
    assert clim_checks(None, reference=rp)["clim_ref_ok"] is False


def test_clim_reference_is_independent_and_has_3_points():
    import json
    from live.static_fetch import CLIM_REFERENCE
    ref = json.loads(CLIM_REFERENCE.read_text())
    assert len(ref["points"]) >= 3 and ref["doy"] == 320
    lats = sorted(p["lat"] for p in ref["points"].values())
    assert lats[-1] - lats[0] > 5               # spread N-S: catches row scrambles
