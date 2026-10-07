"""The broker export's parser: the stubbed edge of the 1099-B reconciliation
(Phase 4 plan T90, plan-shape rule 4).

The owner's realised-gains or 1099-B export exists only after a taxable year,
and its columns are unknown until then, so `parse_export` refuses every file
today with `unknown export format`. The real parser is written by a `size:S`
issue once the first real export exists; its one call site is
`tradepartner paper lots-reconcile`.
"""

from __future__ import annotations

from pathlib import Path

from tradepartner.execution.lots_reconcile import BrokerLotRow

UNKNOWN_EXPORT_FORMAT = "unknown export format"


class UnknownExportFormat(ValueError):
    """The export is not in a format this module can parse (today: any file)."""


def parse_export(path: Path) -> list[BrokerLotRow]:
    """The export's rows as `BrokerLotRow`s. Refuses every file today with
    `UnknownExportFormat` (module docstring); it never reads the file."""
    raise UnknownExportFormat(
        f"{UNKNOWN_EXPORT_FORMAT}: {path.name}: no broker export parser exists yet; "
        "it is written once the first real export's columns are known"
    )
