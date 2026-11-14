"""Source-level checks for build_static (GEE needs creds, so no live runs)."""
from pathlib import Path

from live import build_static

SRC = Path(build_static.__file__).read_text()


def test_population_source_is_gpw():
    assert build_static.POP_COLLECTION == "CIESIN/GPWv411/GPW_Population_Count"
    assert build_static.POP_BAND == "population_count"


def test_no_ghsl_references():
    assert "GHSL" not in SRC


def test_entry_points_exist():
    assert callable(build_static.main)
    assert callable(build_static._terrain)
