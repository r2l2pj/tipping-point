"""Published manifest: layer ids are unambiguous per case (conductor bug 2026-11-15:
wa_2025 'nisar_sme2' x3). Per-date families (tpi_flood, tpi_flood_gfs_d1, ... — SCHEMA:
repeated ids = per-day series) are allowed, but never two entries with the same
(id, date, issued, datetime); NISAR layers (nisar_frame / nisar_extra / nisar_change)
are addressed by id alone in the app, so their ids must be unique."""
import json
from collections import Counter
from pathlib import Path

import pytest

MANIFEST = Path(__file__).resolve().parents[2] / "data" / "manifest.json"
NISAR_KINDS = {"nisar_frame", "nisar_extra", "nisar_change"}


def _cases():
    m = json.loads(MANIFEST.read_text())
    cases = m["cases"]
    return cases.items() if isinstance(cases, dict) else ((c.get("id"), c) for c in cases)


@pytest.mark.parametrize("case_id,case", list(_cases()))
def test_layer_keys_unique(case_id, case):
    keys = Counter((lay.get("id"), lay.get("date"), lay.get("issued"), lay.get("datetime"))
                   for lay in case.get("layers", []))
    assert not [k for k, n in keys.items() if n > 1], case_id


@pytest.mark.parametrize("case_id,case", list(_cases()))
def test_nisar_layer_ids_unique(case_id, case):
    ids = Counter(lay["id"] for lay in case.get("layers", []) if lay.get("kind") in NISAR_KINDS)
    assert not [i for i, n in ids.items() if n > 1], case_id
