"""No `list_contains(?, <column>)` id filters left in `src/` (#3341).

On DuckDB a `list_contains(?, security_id)` filter plans as a per-row list scan
(807 ms on 10M rows / 500 ids) where a semi-join against the unnested parameter
plans as a hash join (20 ms). The first test forbids the slow spelling anywhere
under `src/tradepartner/`; the second pins the replacement's semantics,
including the empty list.
"""

from __future__ import annotations

from pathlib import Path

import duckdb

SRC = Path(__file__).resolve().parents[1] / "src" / "tradepartner"


def test_no_list_contains_parameter_filter_in_src() -> None:
    offenders: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "list_contains(?," in line:
                offenders.append(f"{path.relative_to(SRC.parent.parent)}:{lineno}: {line.strip()}")
    assert not offenders, "use `<column> IN (SELECT unnest(?))` instead:\n" + "\n".join(offenders)


def test_semi_join_unnest_filter_matches_ids_and_empty_list() -> None:
    con = duckdb.connect()
    con.execute("CREATE TABLE t (security_id VARCHAR)")
    con.execute("INSERT INTO t VALUES ('a'), ('b'), ('c')")

    kept = con.execute(
        "SELECT security_id FROM t WHERE security_id IN (SELECT unnest(?)) ORDER BY 1",
        [["a", "c"]],
    ).fetchall()
    assert kept == [("a",), ("c",)]

    empty = con.execute(
        "SELECT security_id FROM t WHERE security_id IN (SELECT unnest(?)) ORDER BY 1",
        [[]],
    ).fetchall()
    assert empty == []
