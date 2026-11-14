"""Stub test for the retired OPERA fetcher (L10b)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fetch import opera


def test_opera_stub_prints_retired_and_exits_0(capsys):
    with pytest.raises(SystemExit) as exc:
        opera.main([])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "retired (L10b)" in out
    assert "OPERA/DSWX/L3_V1/HLS|S1" in out
