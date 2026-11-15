"""R/S/TPI scoring mirrors gee/01_tpi_index.js on synthetic arrays."""
import numpy as np
import pytest

from live import config
from live.tpi import (
    accumulate3,
    class_label,
    classify,
    projected_rain3,
    rain_score,
    soil_score,
    terrain_flood,
    terrain_slide,
    tpi,
    unit_scale,
)


def _pcts():
    shape = (7,)
    return (np.full(shape, 10.0), np.full(shape, 20.0), np.full(shape, 40.0))


def test_config_loads_thresholds():
    assert config.RAIN_PERCENTILES == (90, 95, 99)
    assert config.SOIL_WETNESS_RANGE == (0.5, 0.9)
    assert config.SLOPE_RANGE_DEG == (15, 30)
    assert config.HAND_RANGE_M == (5, 15)
    assert config.TPI_CLASSES == (0.2, 0.4, 0.6)


def test_unit_scale_clamps():
    out = unit_scale(np.array([-1.0, 0.0, 0.5, 1.0, 2.0]), 0.0, 1.0)
    assert out.tolist() == [0.0, 0.0, 0.5, 1.0, 1.0]


def test_rain_score_piecewise():
    p90, p95, p99 = _pcts()
    rain3 = np.array([0.0, 10.0, 15.0, 20.0, 30.0, 40.0, 60.0])
    r = rain_score(rain3, p90, p95, p99)
    assert r == pytest.approx([0.0, 0.0, 0.25, 0.5, 0.75, 1.0, 1.0])


def test_soil_score():
    s = soil_score(np.array([0.4, 0.5, 0.7, 0.9, 0.95]))
    assert s == pytest.approx([0.0, 0.0, 0.5, 1.0, 1.0])


def test_terrain_slide():
    t = terrain_slide(np.array([10.0, 15.0, 22.5, 30.0, 40.0]))
    assert t == pytest.approx([0.0, 0.0, 0.5, 1.0, 1.0])


def test_terrain_flood():
    flat = np.zeros(4)
    t = terrain_flood(np.array([0.0, 5.0, 10.0, 20.0]), flat)
    assert t == pytest.approx([1.0, 1.0, 0.5, 0.0])
    steep = np.full(4, 6.0)  # slope > 5 deg -> no flood risk
    assert terrain_flood(np.array([0.0, 5.0, 10.0, 20.0]), steep).tolist() == [0.0] * 4


def test_tpi_geomean():
    shape = (3,)
    clim = {"p90": np.zeros(shape), "p95": np.ones(shape), "p99": np.full(shape, 2.0)}
    out = tpi(np.full(shape, 5.0), np.full(shape, 1.0), clim,
              np.ones(shape), np.ones(shape))
    assert out["r"] == pytest.approx([1.0] * 3)
    assert out["tpi_slide"] == pytest.approx([1.0] * 3)
    assert out["tpi_flood"] == pytest.approx([1.0] * 3)
    # geometric mean, not product: r=0.5, s=t=1 -> 0.5^(1/3) (not 0.5)
    out = tpi(np.ones(shape), np.full(shape, 0.9), clim,
              np.ones(shape), np.ones(shape),
              weights=(1 / 3, 1 / 3, 1 / 3))  # rain3 == p95 -> r = 0.5
    assert out["r"] == pytest.approx([0.5] * 3)
    assert out["tpi_slide"] == pytest.approx([0.5 ** (1 / 3)] * 3)
    # config.yaml weights (0.3333, 0.3333, 0.3334) give the same geomean
    assert config.TPI_WEIGHTS == (0.3333, 0.3333, 0.3334)
    out = tpi(np.ones(shape), np.full(shape, 0.9), clim,
              np.ones(shape), np.ones(shape))
    assert out["tpi_slide"] == pytest.approx([0.5 ** (1 / 3)] * 3, abs=1e-3)
    # weights are honoured: w_R=1 -> tpi == r
    out = tpi(np.ones(shape), np.full(shape, 0.9), clim,
              np.full(shape, 0.5), np.ones(shape), weights=(1.0, 0.0, 0.0))
    assert out["tpi_slide"] == pytest.approx([0.5] * 3)


def test_classify_cuts():
    cls = classify(np.array([0.0, 0.2, 0.21, 0.4, 0.41, 0.6, 0.61, np.nan]))
    assert cls.tolist() == [0, 0, 1, 1, 2, 2, 3, 0]
    assert class_label(3) == "Crítico"


def test_accumulate3():
    daily = np.arange(1.0, 5.0).reshape(4, 1, 1)
    out = accumulate3(daily)
    assert out[:, 0, 0].tolist() == [1.0, 3.0, 6.0, 9.0]


def test_projected_rain3_blends_observed_and_forecast():
    obs = np.ones((3, 2, 2))            # last 3 observed days, 1 mm each
    fc = np.full((3, 2, 2), 10.0)       # D+1..D+3, 10 mm each
    assert projected_rain3(obs, fc, 1)[0, 0] == pytest.approx(2.0 + 10.0)
    assert projected_rain3(obs, fc, 2)[0, 0] == pytest.approx(1.0 + 20.0)
    assert projected_rain3(obs, fc, 3)[0, 0] == pytest.approx(30.0)
    with pytest.raises(ValueError):
        projected_rain3(obs, fc, 4)


def test_r_zero_forces_tpi_zero():
    """Geometric mean: no rain signal (R = 0) => TPI = 0, whatever S and T."""
    import numpy as np
    from live import tpi as t
    rain3 = np.array([[0.0, 6.0, 30.0]])
    clim = {k: np.full((1, 3), v) for k, v in (("p90", 41.0), ("p95", 54.0), ("p99", 78.0))}
    out = t.tpi(rain3, np.full((1, 3), 0.95), clim, np.ones((1, 3)), np.ones((1, 3)))
    assert (out["r"] == 0).all()
    assert (out["tpi_slide"] == 0).all() and (out["tpi_flood"] == 0).all()


def test_frame_rain3_l28b_alignment():
    """Frame D+k = 3 days ending D+k; obs only < D, forecast >= D (L28b).
    A partial observed day D must never enter (it shrank the 2026-11-15
    05:47Z Now window to ~2 days and zeroed TPI)."""
    import datetime as dt
    import numpy as np
    from live.tpi import frame_rain3
    D = dt.date(2026, 11, 15)
    obs = {D - dt.timedelta(days=j): np.full((2, 2), 10.0 * j) for j in (1, 2, 3)}
    fc = {D + dt.timedelta(days=j): np.full((2, 2), 1.0 + j) for j in range(4)}
    series = {**obs, **fc}
    assert frame_rain3(series, D)[0, 0] == 20.0 + 10.0 + 1.0     # obs D-2, D-1 + fc D
    assert frame_rain3(series, D + dt.timedelta(days=1))[0, 0] == 10.0 + 1.0 + 2.0
    assert frame_rain3(series, D + dt.timedelta(days=3))[0, 0] == 2.0 + 3.0 + 4.0
    assert frame_rain3({k: v for k, v in series.items() if k != D}, D) is None
