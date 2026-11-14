"""gee.check_series — Mode-1 done-when checks on a synthetic series COG."""
import json

import numpy as np
import rasterio
from rasterio.transform import from_origin

from gee import check_series, common


def _write_series(path, values, names):
    """Small multiband float32 GeoTIFF with per-band name descriptions."""
    arr = np.stack(
        [np.full((4, 5), v, dtype=np.float32) for v in values])
    with rasterio.open(
            path, "w", driver="GTiff", height=arr.shape[1], width=arr.shape[2],
            count=arr.shape[0], dtype="float32", crs="EPSG:4326",
            transform=from_origin(-122.6, 48.5, 0.01, 0.01)) as dst:
        dst.write(arr)
        for i, name in enumerate(names, start=1):
            dst.set_band_description(i, name)
    return path


def _wa_names():
    return [d.replace("-", "") for d in common.event_dates("wa_2025")]


def test_ok_series_passes(tmp_path):
    vals = [0.1] * 21
    vals[8] = 0.9                        # peak two days before the event
    p = _write_series(tmp_path / "s.tif", vals, _wa_names())
    assert check_series.check(p, "wa_2025") == []


def test_peak_after_event_fails(tmp_path):
    vals = [0.1] * 21
    vals[15] = 0.9                       # peak after event-day band 10
    p = _write_series(tmp_path / "wa_2025_tpi_slide_20251210.tif",
                      vals, _wa_names())
    failures = check_series.check(p, "wa_2025")
    assert any("peak TPI" in f for f in failures)


def test_score_series_skips_peak_check(tmp_path):
    # delta L5: r_series/s_series share the series checks but have no
    # peak-on/before-event requirement (soil saturates after the rain).
    vals = [0.1] * 21
    vals[15] = 0.9                       # peak after event — fine for scores
    p = _write_series(tmp_path / "wa_2025_s_series_20251210.tif",
                      vals, _wa_names())
    assert check_series.check(p, "wa_2025") == []


def test_static_score_cog(tmp_path):
    # delta L5: t_slide/t_flood are single-band float32 in [0,1]
    p = _write_series(tmp_path / "wa_2025_t_slide_20251210.tif", [0.6], ["x"])
    assert check_series.check(p, "wa_2025") == []
    bad = _write_series(tmp_path / "wa_2025_t_flood_20251210.tif",
                        [0.6, 0.2], ["a", "b"])
    failures = check_series.check(bad, "wa_2025")
    assert any("expected 1" in f for f in failures)


def test_wrong_band_count_fails(tmp_path):
    p = _write_series(tmp_path / "s.tif", [0.5] * 3, ["a", "b", "c"])
    failures = check_series.check(p, "wa_2025")
    assert any("band count" in f for f in failures)


def test_band_names_must_be_dates(tmp_path):
    vals = [0.1] * 21
    names = _wa_names()
    names[3] = "3_TPI_slide"             # old-style name, not a date
    p = _write_series(tmp_path / "s.tif", vals, names)
    failures = check_series.check(p, "wa_2025")
    assert any("band 3 name" in f for f in failures)


def test_out_of_range_fails(tmp_path):
    vals = [0.1] * 21
    vals[5] = 1.4
    p = _write_series(tmp_path / "s.tif", vals, _wa_names())
    failures = check_series.check(p, "wa_2025")
    assert any("[0,1]" in f for f in failures)


def test_main_records_collections_on_success(tmp_path):
    vals = [0.1] * 21
    vals[8] = 0.9
    cog = _write_series(tmp_path / "s.tif", vals, _wa_names())
    metrics = tmp_path / "wa-2025-12" / "metrics.json"
    rc = check_series.main(
        [str(cog), "--case", "wa_2025", "--metrics-path", str(metrics)])
    assert rc == 0
    m = json.loads(metrics.read_text())
    blob = m["cases"]["wa_2025"]
    assert blob["event"] == "wa-2025-12"
    assert blob["collections"]["imerg"] == common.COL_IMERG
    assert blob["collections"]["smap_l4"] == common.COL_SMAP
    assert blob["collections"]["nasadem"] == common.IMG_NASADEM
    # delta L8: derived asset ids recorded alongside the input collections
    assert (blob["collections"]["gee_tpi_slide_series"].endswith(
        "/tipping-point/wa_2025/tpi_slide_series"))


def test_main_exits_nonzero_and_writes_nothing(tmp_path):
    cog = _write_series(tmp_path / "s.tif", [0.5] * 3, ["a", "b", "c"])
    metrics = tmp_path / "wa-2025-12" / "metrics.json"
    rc = check_series.main(
        [str(cog), "--case", "wa_2025", "--metrics-path", str(metrics)])
    assert rc == 1
    assert not metrics.exists()
