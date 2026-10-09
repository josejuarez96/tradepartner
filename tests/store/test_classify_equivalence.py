"""#564: `build_classifications` in one pass per security.

`_reference_build_classifications` is the pre-#564 implementation, copied
verbatim (it re-filtered every form, SIC and listing at each stamp, so it was
quadratic in filings per company). The tests check that the new one returns
exactly the same rows in the same order on randomized companies, that a
company with thousands of filings is fast, and that the classification at `t`
ignores evidence known after `t`.
"""

from __future__ import annotations

import random
import time
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from tradepartner.adapters.filings import FilingHeader, FilingIndexEntry
from tradepartner.adapters.fixture_filings import FixtureFilingSource
from tradepartner.config import Settings
from tradepartner.store.classify import (
    _EDGAR,
    COMMON,
    DOMESTIC_FORMS,
    F6_FORMS,
    FOREIGN_FORMS,
    FUND_FORMS,
    SPAC_SIC,
    UNCLASSIFIABLE,
    ClassificationBuild,
    _base_form,
    _title_type,
    build_classifications,
    ticker_suffix_type,
)
from tradepartner.store.master import MasterBuild
from tradepartner.timeutil import ensure_tz_aware_utc

Row = dict[str, Any]

INGESTED_AT = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)
EPOCH = datetime(2000, 1, 3, 21, 0, tzinfo=UTC)


# --- the pre-#564 implementation, verbatim apart from names ------------------


@dataclass(frozen=True)
class _RefEvidence:
    forms: tuple[tuple[datetime, str], ...]
    sics: tuple[tuple[datetime, int], ...]


def _reference_classify(
    t: datetime,
    evidence: _RefEvidence,
    listings: Sequence[Row],
    benchmark: Row | None,
) -> tuple[str, str, int | None, str]:
    forms = [form for known_at, form in evidence.forms if known_at <= t]
    sics = [sic for known_at, sic in evidence.sics if known_at <= t]
    sic = sics[-1] if sics else None
    if benchmark is not None:
        return "etf", "benchmark_config", sic, benchmark["provenance"]
    if any(form in FUND_FORMS for form in forms):
        return "fund", "fund_form", sic, "filing"
    if any(form in F6_FORMS for form in forms):
        return "depositary", "f6_depositary", sic, "filing"
    status = [form for form in forms if form in DOMESTIC_FORMS | FOREIGN_FORMS]
    domestic = bool(status) and status[-1] in DOMESTIC_FORMS
    if status and not domestic:
        return "foreign", "foreign_form", sic, "filing"
    if sic == SPAC_SIC:
        return "spac", "sic_6770", sic, "filing"
    known = [row for row in listings if row["known_at"] <= t]
    titled = [row for row in known if row["class_title"] is not None]
    if titled:
        security_type, rule = _title_type(titled[-1]["class_title"])
        return security_type, rule, sic, "filing"
    if known:
        suffix_type = ticker_suffix_type(known[-1]["ticker"])
        if suffix_type is not None:
            return suffix_type, f"suffix_{suffix_type}", sic, "snapshot_static"
    if domestic:
        return COMMON, "common_default", sic, "filing"
    return UNCLASSIFIABLE, "no_rule_matched", sic, "filing"


def _reference_evidence(
    source: FixtureFilingSource, ciks: Iterable[str], settings: Settings
) -> dict[str, _RefEvidence]:
    wanted = set(ciks)
    forms: dict[str, list[tuple[datetime, str, str]]] = defaultdict(list)
    for entry in source.filing_index():
        if entry.cik in wanted:
            forms[entry.cik].append((entry.accepted_at, entry.accession, _base_form(entry.form)))
    out: dict[str, _RefEvidence] = {}
    for cik in wanted:
        headers = source.filing_headers(cik, settings.edgar.header_forms)
        sics = sorted((h.accepted_at, h.accession, h.sic) for h in headers if h.sic is not None)
        out[cik] = _RefEvidence(
            forms=tuple((stamp, form) for stamp, _, form in sorted(forms[cik])),
            sics=tuple((stamp, sic) for stamp, _, sic in sics),
        )
    return out


def _reference_build_classifications(
    source: FixtureFilingSource,
    master: MasterBuild,
    settings: Settings,
    *,
    ingested_at: datetime,
) -> ClassificationBuild:
    ingested_at = ensure_tz_aware_utc(ingested_at, field_name="ingested_at")
    securities = list(master.securities)
    issuers = {row["cik"] for row in securities if not row["benchmark"]}
    evidence = _reference_evidence(source, issuers, settings)
    listings: dict[str, list[Row]] = defaultdict(list)
    for row in sorted(master.listings, key=lambda r: r["known_at"]):
        listings[row["security_id"]].append(row)

    rows: list[Row] = []
    for security in sorted(securities, key=lambda r: r["security_id"]):
        security_id = security["security_id"]
        benchmark = security if security["benchmark"] else None
        facts = evidence.get(security["cik"], _RefEvidence((), ()))
        if benchmark is not None:
            facts = _RefEvidence((), ())
        stamps = {k for k, _ in facts.forms} | {k for k, _ in facts.sics}
        stamps |= {row["known_at"] for row in listings[security_id]}
        late = [stamp for stamp in stamps if stamp > ingested_at]
        if late:
            raise ValueError(
                f"{security_id}: evidence at {max(late).isoformat()} is after "
                f"ingested_at {ingested_at.isoformat()}"
            )
        start = security["known_at"]
        previous: tuple[str, str, int | None] | None = None
        for t in sorted({start} | {stamp for stamp in stamps if stamp > start}):
            security_type, rule, sic, provenance = _reference_classify(
                t, facts, listings[security_id], benchmark
            )
            if (security_type, rule, sic) == previous:
                continue
            previous = (security_type, rule, sic)
            rows.append(
                {
                    "security_id": security_id,
                    "sic": sic,
                    "security_type": security_type,
                    "rule": rule,
                    "known_at": t,
                    "ingested_at": ingested_at,
                    "source": security["source"] if benchmark is not None else _EDGAR,
                    "provenance": provenance,
                }
            )
    return ClassificationBuild(classifications=tuple(rows))


# --- random scenarios ---------------------------------------------------------

# Forms in and out of the status, fund and F-6 sets, with amendments.
_FORMS = (
    *sorted(DOMESTIC_FORMS),
    *sorted(FOREIGN_FORMS),
    *sorted(FUND_FORMS),
    *sorted(F6_FORMS),
    "10-K/A",
    "20-F/A",
    "6-K/A",
    "8-K",
    "8-K",
    "8-K",
    "10-12B",
    "S-4",
    "DEF 14A",
)
_SICS = (None, SPAC_SIC, SPAC_SIC, 7372, 2834, 6798)
_TITLES = (
    None,
    None,
    "Common Stock, par value $0.01",
    "Class A Common Stock",
    "6.5% Series A Preferred Stock",
    "Warrants, each exercisable for one share",
    "Units, each of one share and one warrant",
    "Rights to receive one-tenth of one share",
    "American Depositary Shares",
    "5.25% Senior Notes due 2030",
    "Shares of Beneficial Interest",
)
_TICKERS = ("ABC", "ABC-WS", "ABCDW", "ABCDU", "ABC.U", "ABC-PA", "ABCDY", "BRK.B", "ABCDE")


def _settings() -> Settings:
    return Settings(_env_file=None)  # type: ignore[call-arg]


def _cik(n: int) -> str:
    return f"{n:010d}"


def _scenario(seed: int, companies: int) -> tuple[FixtureFilingSource, MasterBuild]:
    """`companies` random issuers, each with a few classes, on a coarse clock
    (whole days, so filings, headers and listings often share an instant)."""
    rng = random.Random(seed)
    settings = _settings()
    header_forms = list(settings.edgar.header_forms)
    index: list[FilingIndexEntry] = []
    headers: list[FilingHeader] = []
    securities: list[Row] = []
    listings: list[Row] = []
    accession = 0

    def stamp() -> datetime:
        return EPOCH + timedelta(days=rng.randrange(0, 400))

    for n in range(1, companies + 1):
        cik = _cik(n)
        for _ in range(rng.randrange(0, 25)):
            accession += 1
            acc = f"{cik}-{accession:08d}"
            at = stamp()
            form = rng.choice(_FORMS)
            index.append(FilingIndexEntry(cik, "Co", form, acc, at))
            if rng.random() < 0.6:
                header_form = form if rng.random() < 0.5 else rng.choice(header_forms)
                headers.append(FilingHeader(cik, acc, header_form, rng.choice(_SICS), at))
        for k in range(rng.randrange(1, 4)):
            security_id = f"{cik}:{k}"
            benchmark = rng.random() < 0.05
            securities.append(
                {
                    "security_id": security_id,
                    "cik": cik,
                    "benchmark": benchmark,
                    "known_at": stamp(),
                    "source": "config" if benchmark else _EDGAR,
                    "provenance": "config" if benchmark else "filing",
                }
            )
            for _ in range(rng.randrange(0, 6)):
                listings.append(
                    {
                        "security_id": security_id,
                        "known_at": stamp(),
                        "class_title": rng.choice(_TITLES),
                        "ticker": rng.choice(_TICKERS),
                    }
                )
    # A CIK with no evidence at all, and out-of-order input everywhere.
    securities.append(
        {
            "security_id": f"{_cik(companies + 1)}:0",
            "cik": _cik(companies + 1),
            "benchmark": False,
            "known_at": stamp(),
            "source": _EDGAR,
            "provenance": "filing",
        }
    )
    rng.shuffle(securities)
    rng.shuffle(listings)
    source = _ShuffledSource(rng, index=index, headers=headers)
    master = MasterBuild(
        securities=tuple(securities),
        listings=tuple(listings),
        unmatched_snapshot=(),
        missing_benchmarks=(),
    )
    return source, master


class _ShuffledSource(FixtureFilingSource):
    """A fixture source that answers in a random order, as a live index may."""

    def __init__(self, rng: random.Random, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._rng = rng

    def filing_index(self, since: datetime | None = None) -> list[FilingIndexEntry]:
        out = super().filing_index(since)
        self._rng.shuffle(out)
        return out

    def filing_headers(self, cik: str, forms: Sequence[str]) -> list[FilingHeader]:
        out = super().filing_headers(cik, forms)
        self._rng.shuffle(out)
        return out


@pytest.mark.parametrize("seed", [564, 1, 2, 3])
def test_matches_the_reference_on_random_companies(seed: int) -> None:
    settings = _settings()
    source, master = _scenario(seed, companies=300)
    # Both builds see the same answers: one shuffle each, from equal seeds.
    expected = _reference_build_classifications(
        _ShuffledSource(random.Random(seed), index=source._index, headers=source._headers),
        master,
        settings,
        ingested_at=INGESTED_AT,
    )
    actual = build_classifications(
        _ShuffledSource(random.Random(seed + 1), index=source._index, headers=source._headers),
        master,
        settings,
        ingested_at=INGESTED_AT,
    )
    assert len(expected.classifications) > 500
    assert actual.classifications == expected.classifications


def test_scenarios_cover_every_rule() -> None:
    """The random scenarios reach every rule, so equality above means something."""
    rules: set[str] = set()
    for seed in (564, 1, 2, 3):
        source, master = _scenario(seed, companies=300)
        built = build_classifications(source, master, _settings(), ingested_at=INGESTED_AT)
        rules |= {row["rule"] for row in built.classifications}
    assert rules >= {
        "benchmark_config",
        "fund_form",
        "f6_depositary",
        "foreign_form",
        "sic_6770",
        "title_common",
        "title_preferred",
        "title_warrant",
        "title_unrecognized",
        "suffix_warrant",
        "common_default",
        "no_rule_matched",
    }


def test_late_evidence_raises_like_the_reference() -> None:
    settings = _settings()
    source, master = _scenario(564, companies=50)
    early = INGESTED_AT.replace(year=2000, month=6)
    with pytest.raises(ValueError, match="ingested_at") as ref:
        _reference_build_classifications(source, master, settings, ingested_at=early)
    with pytest.raises(ValueError, match="ingested_at") as new:
        build_classifications(source, master, settings, ingested_at=early)
    assert str(new.value) == str(ref.value)


def _big_company(filings: int) -> tuple[FixtureFilingSource, MasterBuild]:
    cik = _cik(1)
    rng = random.Random(564)
    index = []
    headers = []
    for n in range(filings):
        at = EPOCH + timedelta(hours=n)
        acc = f"{cik}-{n:08d}"
        form = rng.choice(("10-Q", "8-K", "10-K", "20-F", "6-K"))
        index.append(FilingIndexEntry(cik, "Co", form, acc, at))
        headers.append(FilingHeader(cik, acc, "8-K", rng.choice((7372, SPAC_SIC)), at))
    securities = tuple(
        {
            "security_id": f"{cik}:{k}",
            "cik": cik,
            "benchmark": False,
            "known_at": EPOCH,
            "source": _EDGAR,
            "provenance": "filing",
        }
        for k in range(3)
    )
    master = MasterBuild(
        securities=securities, listings=(), unmatched_snapshot=(), missing_benchmarks=()
    )
    return FixtureFilingSource(index=index, headers=headers), master


def test_a_company_with_thousands_of_filings_is_fast() -> None:
    """Three classes of one issuer with 5,000 filings and headers each. The
    quadratic version took minutes here."""
    source, master = _big_company(5_000)
    settings = _settings()
    start = time.perf_counter()
    built = build_classifications(source, master, settings, ingested_at=INGESTED_AT)
    elapsed = time.perf_counter() - start
    assert built.classifications
    assert elapsed < 2.0, f"{elapsed:.2f}s"


def test_the_fast_build_matches_the_reference_on_a_mid_sized_company() -> None:
    source, master = _big_company(600)
    settings = _settings()
    expected = _reference_build_classifications(source, master, settings, ingested_at=INGESTED_AT)
    actual = build_classifications(source, master, settings, ingested_at=INGESTED_AT)
    assert actual.classifications == expected.classifications


@pytest.mark.parametrize("seed", [564, 7])
def test_rows_known_by_t_ignore_evidence_known_after_t(seed: int) -> None:
    """No look-ahead: the rows with `known_at <= t` built from evidence known by
    `t` equal those built from all evidence, for every evidence instant `t`."""
    settings = _settings()
    source, master = _scenario(seed, companies=40)
    full = build_classifications(source, master, settings, ingested_at=INGESTED_AT)
    stamps = sorted(
        {e.accepted_at for e in source._index}
        | {e.accepted_at for e in source._headers}
        | {row["known_at"] for row in master.listings}
        | {row["known_at"] for row in master.securities}
    )
    for t in stamps[:: max(1, len(stamps) // 60)]:
        known = FixtureFilingSource(
            index=[e for e in source._index if e.accepted_at <= t],
            headers=[e for e in source._headers if e.accepted_at <= t],
        )
        truncated = MasterBuild(
            securities=tuple(r for r in master.securities if r["known_at"] <= t),
            listings=tuple(r for r in master.listings if r["known_at"] <= t),
            unmatched_snapshot=(),
            missing_benchmarks=(),
        )
        partial = build_classifications(known, truncated, settings, ingested_at=INGESTED_AT)
        assert partial.classifications == tuple(
            r for r in full.classifications if r["known_at"] <= t
        ), f"T={t!r}"
