"""OpenFIGI current types are parsed without historical inference."""

import json
from pathlib import Path

from tradepartner.adapters.openfigi import parse_mapping


def test_parse_synthetic_mapping_fixture() -> None:
    fixture = json.loads(
        (Path(__file__).parents[1] / "fixtures/openfigi/mapping_synthetic.json").read_text()
    )
    assert fixture["synthetic"] is True
    rows = parse_mapping(fixture["responses"])
    assert [row.security_type if row else None for row in rows] == [
        "Common Stock",
        "Medium Term Note",
        "Common Stock",
        "Depositary Receipt",
        None,
    ]
    assert rows[0] is not None and rows[0].share_class_figi == "BBG001S5ZPM4"


def test_multiple_us_results_have_stable_composite_choice() -> None:
    ordinary = {"figi": "BBG2", "compositeFIGI": "BBG1", "exchCode": "US"}
    composite = {"figi": "BBG1", "compositeFIGI": "BBG1", "exchCode": "US"}
    assert parse_mapping([{"data": [ordinary, composite]}]) == parse_mapping(
        [{"data": [composite, ordinary]}]
    )
    assert parse_mapping([{"data": [ordinary, composite]}])[0].figi == "BBG1"
    assert parse_mapping([{"data": [{"figi": "OTHER", "exchCode": "LN"}]}]) == [None]
