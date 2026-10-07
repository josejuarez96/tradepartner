"""The stubbed export parser (Phase 4 plan T90): it refuses every file today."""

from __future__ import annotations

from pathlib import Path

import pytest

from tradepartner.execution.lots_reconcile_export import (
    UNKNOWN_EXPORT_FORMAT,
    UnknownExportFormat,
    parse_export,
)


@pytest.mark.parametrize(
    "name, content",
    [
        ("gains.csv", "Symbol,CUSIP,Date Sold,Quantity,Proceeds,Cost Basis,Wash Sale\n"),
        ("1099b.pdf", "%PDF-1.7\n"),
        ("empty.csv", ""),
    ],
)
def test_the_stub_refuses_any_file(tmp_path: Path, name: str, content: str) -> None:
    path = tmp_path / name
    path.write_text(content)

    with pytest.raises(UnknownExportFormat, match=UNKNOWN_EXPORT_FORMAT):
        parse_export(path)
