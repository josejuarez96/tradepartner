"""#3032: the per-CIK facts and statement caches carry a key sidecar, so the
nightly completeness probe (`ingest._cache_completeness`) reads each file's
key without parsing the payload.

The sidecar must give the full parse's answer on every file, including the
fail-closed ones (truncated, other version, other CIK, rewritten without a
sidecar), and must save the parse whenever it matches."""

from __future__ import annotations

import json
import os
import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest
from edgar_transport import EdgarRouter, edgar_settings
from test_edgar_source_cik import (
    APPLE,
    APPLE_ACCEPTED,
    APPLE_ACCESSION,
    SHARES,
    _facts_router,
    _record,
    _seed_apple,
    _shares,
)
from test_edgar_source_cik import _settings as _cik_settings
from test_edgar_source_cik import _source as _cik_source

from tradepartner import ingest
from tradepartner.adapters import edgar_source
from tradepartner.adapters.edgar_source import (
    PARSER_VERSION,
    STATEMENT_VERSION,
    EdgarFilingSource,
)

CIK = "0000000042"
OTHER = "0000000043"


@pytest.fixture
def parses(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[Path]]:
    """Every full parse `_cached_key` makes, by path."""
    calls: list[Path] = []
    real = edgar_source._parse_cache_key

    def counting(path: Path, cik: str, version: int) -> str | None:
        calls.append(path)
        return real(path, cik, version)

    monkeypatch.setattr(edgar_source, "_parse_cache_key", counting)
    yield calls


def _payload(cik: str = CIK, version: int = PARSER_VERSION, key: str = "acc|names") -> dict:
    return {"version": version, "cik": cik, "key": key, "facts": [["n", "a", "2025-01-01", 1.0]]}


def _fixtures(root: Path) -> dict[str, Path]:
    """One cache file per case; the keyed ones written by the real writer."""
    root.mkdir(parents=True, exist_ok=True)
    out: dict[str, Path] = {}

    def keyed(name: str, data: dict) -> Path:
        path = root / name / f"{CIK}.json"
        path.parent.mkdir()
        edgar_source._write_keyed_cache(path, data)
        out[name] = path
        return path

    keyed("keyed", _payload())
    keyed("keyed_other_version_asked", _payload())  # asked under another version below
    # truncated in place after a keyed write: size and mtime no longer match
    truncated = keyed("truncated", _payload())
    with truncated.open("r+b") as file:
        file.truncate(truncated.stat().st_size // 2)
    # truncated by a replace that keeps the old size and mtime (padded garbage)
    padded = keyed("truncated_same_size", _payload())
    st = padded.stat()
    body = padded.read_bytes()
    half = body[: len(body) // 2]
    tmp = padded.with_name("tmp")
    tmp.write_bytes(half + b" " * (len(body) - len(half)))
    os.utime(tmp, ns=(st.st_atime_ns, st.st_mtime_ns))
    os.replace(tmp, padded)  # new inode: the sidecar no longer matches
    # rewritten without a sidecar (an older writer), stale sidecar left behind
    rewritten = keyed("rewritten", _payload(key="old|names"))
    edgar_source.edgar_raw.write_atomic(rewritten, json.dumps(_payload(key="new|names")).encode())
    # legacy: no sidecar at all
    legacy = root / "legacy" / f"{CIK}.json"
    legacy.parent.mkdir()
    legacy.write_text(json.dumps(_payload()))
    out["legacy"] = legacy
    legacy_truncated = root / "legacy_truncated" / f"{CIK}.json"
    legacy_truncated.parent.mkdir()
    legacy_truncated.write_text(json.dumps(_payload())[:-7])
    out["legacy_truncated"] = legacy_truncated
    keyed("other_version", _payload(version=PARSER_VERSION + 1))
    keyed("other_cik", _payload(cik=OTHER))
    # a sidecar copied from another CIK's file
    stolen = keyed("stolen_sidecar", _payload())
    other = root / "stolen_sidecar" / f"{OTHER}.json"
    edgar_source._write_keyed_cache(other, _payload(cik=OTHER, key="theirs|names"))
    shutil.copy(edgar_source._key_sidecar_path(other), edgar_source._key_sidecar_path(stolen))
    # a sidecar that is not JSON
    garbage = keyed("garbage_sidecar", _payload())
    edgar_source._key_sidecar_path(garbage).write_bytes(b'{"version": ')
    # a sidecar whose data file is gone
    gone = keyed("data_gone", _payload())
    gone.unlink()
    return out


def _asked_version(name: str) -> int:
    return PARSER_VERSION + 1 if name == "keyed_other_version_asked" else PARSER_VERSION


def test_the_sidecar_answer_equals_the_full_parse_on_every_fixture(tmp_path: Path) -> None:
    files = _fixtures(tmp_path / "cache")
    for name, path in files.items():
        version = _asked_version(name)
        expected = edgar_source._parse_cache_key(path, CIK, version)
        # twice: the first read may heal the sidecar, the second reads it
        assert edgar_source._cached_key(path, CIK, version) == expected, name
        assert edgar_source._cached_key(path, CIK, version) == expected, name
        latest = None if expected is None else expected.split("|", 1)[0]
        if version == PARSER_VERSION:
            assert edgar_source._cached_latest(path, CIK) == latest, name
    # and the fail-closed ones really are refused
    for name in (
        "truncated",
        "truncated_same_size",
        "legacy_truncated",
        "other_version",
        "other_cik",
        "keyed_other_version_asked",
        "data_gone",
    ):
        assert edgar_source._cached_key(files[name], CIK, _asked_version(name)) is None, name
    assert edgar_source._cached_key(files["rewritten"], CIK, PARSER_VERSION) == "new|names"
    assert edgar_source._cached_key(files["stolen_sidecar"], CIK, PARSER_VERSION) == "acc|names"


def test_a_matching_sidecar_saves_the_parse_and_a_mismatch_parses(
    tmp_path: Path, parses: list[Path]
) -> None:
    files = _fixtures(tmp_path / "cache")
    for _ in range(3):
        assert edgar_source._cached_key(files["keyed"], CIK, PARSER_VERSION) == "acc|names"
    assert parses == []
    # a truncated file is parsed on every read, never healed into "keyed"
    for _ in range(3):
        assert edgar_source._cached_key(files["truncated"], CIK, PARSER_VERSION) is None
    assert parses == [files["truncated"]] * 3
    parses.clear()
    # a legacy file is parsed once, then its healed sidecar answers
    for _ in range(3):
        assert edgar_source._cached_key(files["legacy"], CIK, PARSER_VERSION) == "acc|names"
    assert parses == [files["legacy"]]
    parses.clear()
    # a rewrite after healing (no sidecar write) is caught by the stat pin
    edgar_source.edgar_raw.write_atomic(
        files["legacy"], json.dumps(_payload(key="next|names")).encode()
    )
    assert edgar_source._cached_key(files["legacy"], CIK, PARSER_VERSION) == "next|names"
    assert parses == [files["legacy"]]


def _completeness_source(tmp_path: Path) -> EdgarFilingSource:
    settings = edgar_settings(tmp_path / "edgar", statement_facts_enabled=True)
    source = EdgarFilingSource(settings, client=EdgarRouter().client())
    source._filing_index_ran = True
    return source


def test_cache_completeness_equals_the_full_parse_and_parses_nothing_when_keyed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`ingest._cache_completeness` over keyed, stale, truncated, legacy and
    absent caches: the same answer as with the sidecar ignored, and with
    every file keyed, no payload parse at all."""
    source = _completeness_source(tmp_path)
    forms = set(source._settings.edgar.cover_page_forms)
    ciks = [f"00000001{n:02d}" for n in range(6)]
    for n, cik in enumerate(ciks):
        accession = f"{cik}-25-0000{n:02d}"
        source._save_stamps(cik, {accession: _record(accession, "10-K", APPLE_ACCEPTED)})
    facts_dir = source._cache / "facts" / f"v{PARSER_VERSION}"

    def write(cik: str, *, stale: bool = False) -> tuple[Path, Path]:
        latest = source._facts_cache_key(source._load_stamps(cik), forms)
        assert latest is not None
        accession = "0000000000-20-000001" if stale else latest
        fact = facts_dir / f"{cik}.json"
        edgar_source._write_keyed_cache(fact, _payload(cik, key=f"{accession}|{SHARES}"))
        statement = source._statement_cache_path(cik)
        edgar_source._write_keyed_cache(
            statement,
            {
                "version": STATEMENT_VERSION,
                "cik": cik,
                "key": source._statement_key(accession),
                "records": [],
                "conflicts": [],
            },
        )
        return fact, statement

    write(ciks[0])  # complete
    write(ciks[1], stale=True)  # keyed by an older accession
    fact, statement = write(ciks[2])  # truncated after the write
    for path in (fact, statement):
        with path.open("r+b") as file:
            file.truncate(10)
    fact, statement = write(ciks[3])  # legacy: no sidecars
    edgar_source._key_sidecar_path(fact).unlink()
    edgar_source._key_sidecar_path(statement).unlink()
    write(ciks[4])
    edgar_source._key_sidecar_path(source._statement_cache_path(ciks[4])).unlink()
    # ciks[5]: no cache files at all
    issuers = frozenset(ciks)

    calls: list[Path] = []
    real = edgar_source._parse_cache_key

    def counting(path: Path, cik: str, version: int) -> str | None:
        calls.append(path)
        return real(path, cik, version)

    monkeypatch.setattr(edgar_source, "_parse_cache_key", counting)
    answer = ingest._cache_completeness(source, issuers)
    # first probe: only the truncated, the legacy and the unsidecarred files parse
    assert sorted(calls) == sorted(
        [
            facts_dir / f"{ciks[2]}.json",
            source._statement_cache_path(ciks[2]),
            facts_dir / f"{ciks[3]}.json",
            source._statement_cache_path(ciks[3]),
            source._statement_cache_path(ciks[4]),
        ]
    )
    # the reference: the same probe with every sidecar ignored
    monkeypatch.setattr(edgar_source, "_read_key_sidecar", lambda *a: None)
    reference = ingest._cache_completeness(source, issuers)
    monkeypatch.undo()
    assert answer == reference
    facts, statements = answer
    assert facts == {
        ciks[0]: True,
        ciks[1]: False,
        ciks[2]: False,
        ciks[3]: True,
        ciks[4]: True,
        ciks[5]: False,
    }
    assert statements == facts
    # a second probe (sidecars healed): only the truncated files parse
    calls.clear()
    monkeypatch.setattr(edgar_source, "_parse_cache_key", counting)
    assert ingest._cache_completeness(source, issuers) == reference
    assert sorted(calls) == sorted(
        [facts_dir / f"{ciks[2]}.json", source._statement_cache_path(ciks[2])]
    )


def test_the_facts_and_statement_writers_leave_a_matching_sidecar(
    tmp_path: Path, parses: list[Path]
) -> None:
    """The real write path (`facts`, filling the statement cache too)."""
    settings = _cik_settings(tmp_path, statement_facts_enabled=True)
    source = _cik_source(settings, _facts_router())
    _seed_apple(source)
    _shares(source, APPLE)
    fact = Path(settings.edgar.cache_dir) / "facts" / f"v{PARSER_VERSION}" / f"{APPLE}.json"
    statement = source._statement_cache_path(APPLE)
    for path in (fact, statement):
        assert edgar_source._key_sidecar_path(path).exists()
    parses.clear()
    assert edgar_source._cached_latest(fact, APPLE) == APPLE_ACCESSION
    assert edgar_source._cached_key(statement, APPLE, STATEMENT_VERSION) == (
        source._statement_key(APPLE_ACCESSION)
    )
    assert parses == []
    assert edgar_source._cached_key(fact, APPLE, PARSER_VERSION) == (
        edgar_source._parse_cache_key(fact, APPLE, PARSER_VERSION)
    )
