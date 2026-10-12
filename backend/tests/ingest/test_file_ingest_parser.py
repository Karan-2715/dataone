import io
import json

import pytest

from app.services.file_ingest_parser import (
    UnsupportedFileError,
    parse_tabular_file,
    table_name_from_filename,
)


def test_csv_dedupes_columns_and_skips_blank_lines():
    cols, rows = parse_tabular_file("a.csv", b"id,name,name\n1,Al,x\n\n2,Bo,y\n")
    assert cols == ["id", "name", "name_2"]
    assert len(rows) == 2 and rows[1]["name_2"] == "y"


def test_csv_blank_cells_become_null():
    _, rows = parse_tabular_file("a.csv", b'id,age,cabin\n1,22,\n2,,"C85"\n3,  ,\n')
    assert rows == [
        {"id": "1", "age": "22", "cabin": None},
        {"id": "2", "age": None, "cabin": "C85"},
        {"id": "3", "age": None, "cabin": None},
    ]


def test_tsv():
    cols, rows = parse_tabular_file("a.tsv", b"id\tname\n1\tAl\n")
    assert cols == ["id", "name"] and rows == [{"id": "1", "name": "Al"}]


def test_json_array_unions_keys_and_serialises_nested_values():
    data = json.dumps([{"id": 1, "tags": ["a"]}, {"id": 2, "extra": True}]).encode()
    cols, rows = parse_tabular_file("a.json", data)
    assert cols == ["id", "tags", "extra"]
    assert rows[0]["tags"] == '["a"]' and rows[1]["extra"] == "true" and rows[1]["tags"] is None


def test_json_wrapped_records():
    cols, rows = parse_tabular_file("a.json", json.dumps({"data": [{"id": 1}]}).encode())
    assert cols == ["id"] and rows == [{"id": "1"}]


def test_jsonl():
    cols, rows = parse_tabular_file("a.ndjson", b'{"id":1}\n{"id":2,"x":3.5}\n')
    assert cols == ["id", "x"] and rows[1]["x"] == "3.5"


def test_parquet_roundtrip_keeps_nulls():
    pa = pytest.importorskip("pyarrow")
    pq = pytest.importorskip("pyarrow.parquet")
    buf = io.BytesIO()
    pq.write_table(pa.table({"id": [1, 2], "s": ["a", None]}), buf)
    cols, rows = parse_tabular_file("a.parquet", buf.getvalue())
    assert cols == ["id", "s"] and rows == [{"id": "1", "s": "a"}, {"id": "2", "s": None}]


def test_xlsx_skips_empty_rows():
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    wb.active.append(["id", "d"])
    wb.active.append([1, "x"])
    wb.active.append([2, ""])
    wb.active.append([None, None])
    buf = io.BytesIO()
    wb.save(buf)
    _, rows = parse_tabular_file("a.xlsx", buf.getvalue())
    assert rows == [{"id": "1", "d": "x"}, {"id": "2", "d": None}]


@pytest.mark.parametrize("name,content", [
    ("a.pdf", b"x"), ("a.json", b"{bad"), ("a.jsonl", b"[1]"), ("a.parquet", b"nope"), ("a.xlsx", b"nope"),
])
def test_bad_files_raise_unsupported(name, content):
    with pytest.raises(UnsupportedFileError):
        parse_tabular_file(name, content)


def test_table_name_from_filename():
    assert table_name_from_filename("My Data-2024.final.CSV") == "my_data_2024_final"
    assert table_name_from_filename("2024.csv") == "t_2024"
    assert table_name_from_filename("???.csv") == "uploaded_file"
