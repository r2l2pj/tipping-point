"""pipeline.fetch.inmet — parsing/filtering against a fixture zip, no network."""
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fetch import inmet

HEADER = (
    "REGIAO:;S\n"
    "UF:;RS\n"
    "ESTACAO:;{name}\n"
    "CODIGO (WMO):;{code}\n"
    "LATITUDE:;{lat}\n"
    "LONGITUDE:;{lon}\n"
    "ALTITUDE:;41,18\n"
    "DATA DE FUNDACAO:;22/09/00\n"
    "Data;Hora UTC;PRECIPITAÇÃO TOTAL, HORÁRIO (mm);TEMPERATURA DO AR - "
    "BULBO SECO, HORÁRIO (°C);\n"
)


def _csv(name, code, lat, lon, rows):
    return HEADER.format(name=name, code=code, lat=lat, lon=lon) + "".join(
        f"{date};{hour:04d} UTC;{mm};20;\n" for date, hour, mm in rows)


def _write_zip(tmp_path, members):
    path = tmp_path / "fixture.zip"
    with zipfile.ZipFile(path, "w") as zf:
        for name, text in members:
            zf.writestr(name, text.encode("latin-1"))
    return path


def test_parse_station_csv_aggregates_hours_to_days():
    text = _csv("TESTE", "A999", "-30,0", "-51,0", [
        ("2024/04/27", 2200, "1,5"), ("2024/04/27", 2300, "2,5"),
        ("2024/04/28", 0, "10"), ("2024/04/28", 100, ""),  # blank hour skipped
    ])
    meta, daily = inmet.parse_station_csv(text)
    assert meta == {"id": "A999", "name": "TESTE", "lat": -30.0, "lon": -51.0}
    assert daily == {"2024-04-27": 4.0, "2024-04-28": 10.0}


def test_fetch_filters_bbox_and_window(tmp_path):
    inside = _csv("DENTRO", "A801", "-30,05", "-51,17",
                  [("2024/04/26", 0, "3"), ("2024/04/28", 0, "7"),
                   ("2024/05/12", 0, "9")])
    outside = _csv("FORA", "A802", "-32,08", "-52,17",
                   [("2024/04/28", 0, "5")])
    path = _write_zip(tmp_path, [("in.CSV", inside), ("out.CSV", outside)])
    bbox = [-52.5, -30.5, -50.5, -29.0]
    out = inmet.fetch(str(path), bbox, "2024-04-27", "2024-05-10",
                      url="fixture")
    assert [s["id"] for s in out["stations"]] == ["A801"]
    assert out["stations"][0]["daily"] == {"2024-04-28": 7.0}
    assert out["url"] == "fixture" and out["license"] and out["source"]


def test_main_writes_json(tmp_path, monkeypatch):
    csv_text = _csv("DENTRO", "A801", "-30,05", "-51,17",
                    [("2024/05/01", 0, "1")])
    zip_path = _write_zip(tmp_path, [("s.CSV", csv_text)])
    out = tmp_path / "out.json"
    rc = inmet.main([
        "--event", "rs-2024-05", "--start", "2024-04-20",
        "--end", "2024-05-10", "--zip", str(zip_path),
        "--out", str(out)])
    assert rc == 0
    import json
    data = json.loads(out.read_text())
    assert data["stations"][0]["daily"] == {"2024-05-01": 1.0}
