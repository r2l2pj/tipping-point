"""build_clim: window/date logic, percentile semantics, band layout (L30-clim).
Hermetic — tiny synthetic rasters, no network."""
import datetime as dt
import struct

import numpy as np
import pytest

from live import build_clim as bc


def test_window_is_three_days_before_end_exclusive():
    end = dt.date(2010, 1, 2)
    assert bc.sample_days(end) == [dt.date(2009, 12, 30),
                                   dt.date(2009, 12, 31),
                                   dt.date(2010, 1, 1)]


def test_window_ends_count_and_offsets():
    ends = bc.window_ends(1)                     # Jan 1
    assert len(ends) == 23 * 21
    # year-boundary: Jan 1 2001 - 30 d is in 2000
    assert ends[0] == dt.date(2000, 12, 2)
    assert ends[10] == dt.date(2001, 1, 1)
    assert ends[-1] == dt.date(2023, 1, 31)


def test_feb29_rolls_to_mar1_in_non_leap_years():
    assert bc.doy_month_day(60) == (2, 29)
    assert bc.from_ymd(2001, 2, 29) == dt.date(2001, 3, 1)
    assert bc.from_ymd(2004, 2, 29) == dt.date(2004, 2, 29)
    ends = bc.window_ends(60)
    centers = ends[10::21]                       # offset 0 per year
    assert centers[0] == dt.date(2001, 3, 1)
    assert centers[3] == dt.date(2004, 2, 29)
    # doy 60 and doy 61 share the centre date in non-leap years
    assert bc.window_ends(61)[10] == dt.date(2001, 3, 1)


def test_leap_doy_and_month_day_roundtrip():
    for doy in (1, 59, 60, 61, 319, 320, 366):
        m, d = bc.doy_month_day(doy)
        assert bc.leap_doy(dt.date(2000, m, d)) == doy
    assert bc.leap_doy(dt.date(2026, 11, 15)) == 320   # non-leap year
    assert bc.leap_doy(dt.date(2026, 3, 1)) == 61


def test_needed_days_span():
    days = bc.needed_days()
    assert days[0] == dt.date(2000, 11, 29)      # Jan 1 2001 - 30 - 3
    assert days[-1] == dt.date(2024, 1, 29)      # Dec 31 2023 + 30 - 1
    assert len(days) == len(set(days))


def test_band_index():
    assert bc.band_index(1, "p90") == 0
    assert bc.band_index(1, 99) == 2
    assert bc.band_index(319, "p95") == 318 * 3 + 1
    assert bc.band_index(366, "p99") == 1097


def test_native_grid_matches_verified_opendap_slice():
    assert bc.native_index_window((-58, -34, -48, -22.5)) == (1219, 1320, 559, 675)
    lats, lons = bc.native_centers((-58, -34, -48, -22.5))
    assert len(lons) == 102 and len(lats) == 117
    assert lats[0] == pytest.approx(-22.45) and lats[-1] == pytest.approx(-34.05)
    assert lons[0] == pytest.approx(-58.05) and lons[-1] == pytest.approx(-47.95)


def test_parse_dods_first_array():
    hdr = b"Dataset {\n  ...\n} x.nc4;\nData:\n"
    vals = np.arange(6, dtype=">f4")
    raw = hdr + struct.pack(">II", 6, 6) + vals.tobytes() + b"maps..."
    a = bc.parse_dods_first_array(raw, (1, 2, 3))
    assert a.shape == (1, 2, 3) and a.dtype == np.float32
    np.testing.assert_array_equal(a.ravel(), np.arange(6))
    with pytest.raises(ValueError):
        bc.parse_dods_first_array(raw, (1, 2, 4))


def test_compute_climatology_matches_bruteforce():
    """Daily rain = day ordinal (2x2 pixels, offset per pixel), so every
    3-day sum is known in closed form."""
    days = bc.needed_days()
    base = np.array([[0.0, 1.0], [2.0, 3.0]], np.float32)
    stack = np.stack([base + (d.toordinal() % 97) for d in days])
    index = {d: k for k, d in enumerate(days)}
    doys = [1, 60, 320]
    got = bc.compute_climatology(index, stack, doys)
    assert got.shape == (9, 2, 2)
    for k, doy in enumerate(doys):
        samples = np.array([
            sum(base + (d.toordinal() % 97) for d in bc.sample_days(e))
            for e in bc.window_ends(doy)])
        assert samples.shape[0] == 483
        want = np.percentile(samples, [90, 95, 99], axis=0)
        np.testing.assert_allclose(got[k * 3:(k + 1) * 3], want, rtol=1e-6)


def test_write_tif_layout(tmp_path):
    rasterio = pytest.importorskip("rasterio")
    lats, lons = bc.native_centers((-58, -34, -57.8, -33.8), pad=0)
    doys = range(1, 4)
    data = np.stack([np.full((len(lats), len(lons)), b + 0.04, np.float32)
                     for b in range(9)])
    out = bc.write_tif(tmp_path / "c.tif", data, lats, lons, doys=doys)
    with rasterio.open(out) as ds:
        assert ds.count == 9 and ds.crs.to_epsg() == 4326
        assert ds.descriptions[0] == "doy001_p90"
        assert ds.descriptions[5] == "doy002_p99"
        assert ds.transform.e < 0                    # north-up
        assert ds.transform.f == pytest.approx(-33.8)
        assert ds.read(bc.band_index(2, "p95") + 1)[0, 0] == pytest.approx(4.0)
        assert ds.tags()["collection"] == "GPM_3IMERGDF.07"
        assert ds.compression.name.lower() == "deflate"
