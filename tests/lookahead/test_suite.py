"""The no-look-ahead suite over price adapters (spec "Look-ahead"; plan T10).

Five named checks, one per violation the spec's broken adapter must show.
Each returns a list of findings (empty = pass) rather than asserting, so a
test can say which check caught what:

- `check_early_known_at`: a first-seen bar is stamped no earlier than its
  session close, a first-seen action no earlier than
  `action_first_seen_known_at` (its announcement capped at the proxy, else
  the proxy, #83). Later stamps are conservative, not look-ahead. Trust
  boundary: `announced_at` is taken from the record, so an adapter that
  fills it with a too-early value (a declaration date read as midnight
  instead of `announcement_date_known_at`) passes here; that rule belongs
  to the real adapter's tests (T12).
- `check_prices_unadjusted`: across every split's ex-date the raw close
  drops by about the split ratio, in the first fetch and every revision. A
  source that returns adjusted history shows no drop, and adjusting it
  again at read time would double-count.
- `check_resolves_by_security_id_only`: a ticker passed as an id raises
  `UnknownSecurityIdError`, alone or mixed with a real id (spec req 3).
- `check_known_at_not_after_ingested_at`: nothing is stamped as knowable,
  or as announced, later than the moment it was fetched.
- `check_revisions_not_back_dated`: every later record for a key is
  stamped at its own `ingested_at`, strictly after the one before it.

The fixture price adapter passes all five; `BrokenPriceSource` with each
violation is caught by exactly its named check, and by no other, so each
check has teeth and none of them is doing another's job. The truncation
invariance over the as-of API lives in `test_asof_invariance.py`.

The fixture adapter already refuses rows breaking rules 1, 4 and 5 when it
is built, so its pass on those is close to automatic. The checks earn their
keep on a real store: a price table row maps to `Ingested` directly (the
record's columns plus `ingested_at`), which is how the data-validator (spec
req 15) runs them.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import replace
from datetime import date, datetime, timedelta
from functools import cache
from itertools import pairwise

import pytest

from lookahead.broken_adapter import (
    ALL_TIME,
    UNIVERSE_DIR,
    BrokenPriceSource,
    Ingested,
    Violation,
    fixture_history,
    security_ids,
    tickers,
)
from tradepartner.adapters.fixture_prices import FixturePriceSource
from tradepartner.adapters.prices import (
    ActionType,
    Bar,
    CorporateAction,
    PriceSource,
    UnknownSecurityIdError,
    action_first_seen_known_at,
    bar_known_at,
)

#: ~11k bars per run; `bar_known_at` builds a pandas timestamp per call.
_bar_known_at: Callable[[date], datetime] = cache(bar_known_at)


def _by_key(history: Sequence[Ingested]) -> dict[tuple[object, ...], list[Ingested]]:
    """History grouped by record type and natural key, each group in ingest
    order (`ingested_at`, then `known_at`)."""
    groups: defaultdict[tuple[object, ...], list[Ingested]] = defaultdict(list)
    for item in history:
        groups[(type(item.record).__name__, *item.record.key)].append(item)
    for group in groups.values():
        group.sort(key=lambda i: (i.ingested_at, i.record.known_at))
    return groups


def check_early_known_at(history: Sequence[Ingested]) -> list[str]:
    """First-seen records stamped before they were knowable."""
    findings = []
    for key, group in _by_key(history).items():
        record = group[0].record
        if isinstance(record, Bar):
            expected = _bar_known_at(record.session)
        else:
            expected = action_first_seen_known_at(record.ex_date, announced_at=record.announced_at)
        if record.known_at < expected:
            findings.append(
                f"early known_at: {key} first seen at {record.known_at.isoformat()}, "
                f"knowable at {expected.isoformat()}"
            )
    return findings


def _split_windows(
    history: Sequence[Ingested],
) -> tuple[list[tuple[CorporateAction, list[Bar], Bar]], list[CorporateAction]]:
    """For each split: every version of the last bar before its ex-date, the
    first-seen bar of the first session on or after it; plus the splits
    without bars on both sides."""
    versions: defaultdict[str, dict[date, list[Bar]]] = defaultdict(dict)
    splits = []
    for group in _by_key(history).values():
        record = group[0].record
        if isinstance(record, Bar):
            versions[record.security_id][record.session] = [
                i.record for i in group if isinstance(i.record, Bar)
            ]
        elif record.action_type is ActionType.SPLIT:
            splits.append(record)
    windows, skipped = [], []
    for split in splits:
        sessions = versions[split.security_id]
        before = [d for d in sessions if d < split.ex_date]
        after = [d for d in sessions if d >= split.ex_date]
        if before and after:
            windows.append((split, sessions[max(before)], sessions[min(after)][0]))
        else:
            skipped.append(split)
    return windows, skipped


def unexamined_splits(history: Sequence[Ingested]) -> list[CorporateAction]:
    """Splits `check_prices_unadjusted` cannot test (no bars on both sides)."""
    return _split_windows(history)[1]


def check_prices_unadjusted(history: Sequence[Ingested]) -> list[str]:
    """Splits whose ex-date shows no raw price drop (pre-adjusted bars).

    Compares **every version** (first fetch and each revision) of the last
    bar before the ex-date with the first-seen bar on or after it: the log
    ratio must be nearer the split ratio than 1:1. Checking revisions
    catches a later re-fetch from an adjusted feed, stamped correctly as a
    revision (quant-auditor on #126). `unexamined_splits` lists the splits
    this cannot test.

    Limits: only splits are visible from prices alone. A dividend-adjusted
    feed, or any adjustment on a security with no split in range, passes;
    the guard for those is the real adapter's request (`adjustment=raw`,
    T12).
    """
    findings = []
    for split, pre_versions, on_bar in _split_windows(history)[0]:
        for pre in pre_versions:
            observed = math.log(pre.close / on_bar.close)
            if abs(observed - math.log(split.ratio_or_amount)) >= abs(observed):
                findings.append(
                    f"pre-adjusted prices: {split.security_id} close ratio across the "
                    f"{split.ratio_or_amount}-for-1 split on {split.ex_date} is "
                    f"{math.exp(observed):.3f} (bar known_at {pre.known_at.isoformat()})"
                )
    return findings


def check_resolves_by_security_id_only(
    source: PriceSource, probes: Sequence[str], known_id: str
) -> list[str]:
    """Tickers in `probes` that `source` resolved instead of refusing, alone
    or mixed with a real id (an adapter that drops unknown ids silently
    loses data without an error)."""
    findings = []
    calls: list[tuple[str, Callable[..., object]]] = [
        ("bars", source.bars),
        ("corporate_actions", source.corporate_actions),
    ]
    for ticker in probes:
        for name, call in calls:
            for ids, label in (([ticker], ""), ([known_id, ticker], " (mixed)")):
                try:
                    call(ids, *ALL_TIME)
                except UnknownSecurityIdError:
                    continue
                findings.append(f"ticker resolution: {name}({ids!r}) did not raise{label}")
    return findings


def check_known_at_not_after_ingested_at(history: Sequence[Ingested]) -> list[str]:
    """Records stamped as knowable, or announced, after they were fetched."""
    findings = []
    for i in history:
        key = (type(i.record).__name__, *i.record.key)
        if i.record.known_at > i.ingested_at:
            findings.append(
                f"known_at after ingested_at: {key} known_at "
                f"{i.record.known_at.isoformat()} > ingested_at {i.ingested_at.isoformat()}"
            )
        announced = i.record.announced_at if isinstance(i.record, CorporateAction) else None
        if announced is not None and announced > i.ingested_at:
            findings.append(
                f"known_at after ingested_at: {key} announced_at "
                f"{announced.isoformat()} > ingested_at {i.ingested_at.isoformat()}"
            )
    return findings


def check_revisions_not_back_dated(history: Sequence[Ingested]) -> list[str]:
    """Revisions not stamped at their own ingest time, or not after the
    record they revise."""
    findings = []
    for key, group in _by_key(history).items():
        for previous, current in pairwise(group):
            known_at = current.record.known_at
            if known_at != current.ingested_at or known_at <= previous.record.known_at:
                findings.append(
                    f"back-dated revision: {key} revision ingested "
                    f"{current.ingested_at.isoformat()} stamped {known_at.isoformat()} "
                    f"(previous {previous.record.known_at.isoformat()})"
                )
    return findings


_HISTORY_CHECKS: dict[str, Callable[[Sequence[Ingested]], list[str]]] = {
    "check_early_known_at": check_early_known_at,
    "check_prices_unadjusted": check_prices_unadjusted,
    "check_known_at_not_after_ingested_at": check_known_at_not_after_ingested_at,
    "check_revisions_not_back_dated": check_revisions_not_back_dated,
}

#: Which check must catch which violation.
_CAUGHT_BY: dict[Violation, str] = {
    Violation.EARLY_KNOWN_AT: "check_early_known_at",
    Violation.PRE_ADJUSTED_PRICES: "check_prices_unadjusted",
    Violation.PRE_ADJUSTED_ON_REFETCH: "check_prices_unadjusted",
    Violation.TICKER_RESOLUTION: "check_resolves_by_security_id_only",
    Violation.KNOWN_AT_AFTER_INGESTED_AT: "check_known_at_not_after_ingested_at",
    Violation.BACK_DATED_REVISION: "check_revisions_not_back_dated",
}


def run_suite(source: PriceSource, history: Sequence[Ingested]) -> dict[str, list[str]]:
    """Every check's findings for one adapter, by check name."""
    results = {name: check(history) for name, check in _HISTORY_CHECKS.items()}
    results["check_resolves_by_security_id_only"] = check_resolves_by_security_id_only(
        source, sorted(tickers()), security_ids()[0]
    )
    return results


@pytest.fixture(scope="module")
def fixture_results() -> dict[str, list[str]]:
    source = FixturePriceSource(UNIVERSE_DIR)
    return run_suite(source, fixture_history(source, security_ids()))


@pytest.mark.parametrize("check", sorted(_CAUGHT_BY.values()))
def test_fixture_adapter_passes(fixture_results: dict[str, list[str]], check: str) -> None:
    assert fixture_results[check] == []


def test_every_violation_has_a_named_check() -> None:
    assert set(_CAUGHT_BY) == set(Violation)
    assert set(_CAUGHT_BY.values()) == {*_HISTORY_CHECKS, "check_resolves_by_security_id_only"}


#: Text every injected form of a violation must show up as, so a check
#: that catches only half of an injection (bars but not actions, `bars` but
#: not `corporate_actions`) fails.
_MUST_MENTION: dict[Violation, tuple[str, ...]] = {
    Violation.EARLY_KNOWN_AT: (
        "('Bar', 'SEC_SPLIT_PLAIN'",
        "('CorporateAction', 'SEC_SPLIT_PLAIN'",
    ),
    Violation.PRE_ADJUSTED_PRICES: ("SEC_SPLIT_PLAIN", "SEC_SPLIT_FUTURE"),
    Violation.PRE_ADJUSTED_ON_REFETCH: ("SEC_SPLIT_PLAIN", "SEC_SPLIT_FUTURE"),
    Violation.TICKER_RESOLUTION: ("bars([", "corporate_actions([", "mixed"),
    Violation.KNOWN_AT_AFTER_INGESTED_AT: ("('Bar', 'SEC_SPLIT_PLAIN'",),
    Violation.BACK_DATED_REVISION: ("('Bar', 'SEC_SPLIT_BACKFILLED'",),
}


@pytest.mark.parametrize("violation", list(Violation))
def test_broken_adapter_violation_is_caught_by_its_named_check(violation: Violation) -> None:
    source = BrokenPriceSource(violation)
    results = run_suite(source, source.history())
    flagged = {name for name, findings in results.items() if findings}
    assert flagged == {_CAUGHT_BY[violation]}, results
    text = "\n".join(results[_CAUGHT_BY[violation]])
    for marker in _MUST_MENTION[violation]:
        assert marker in text, (marker, text[:2000])


def test_every_fixture_split_is_examined() -> None:
    history = fixture_history(FixturePriceSource(UNIVERSE_DIR), security_ids())
    assert unexamined_splits(history) == []


def test_announced_at_after_ingested_at_is_caught() -> None:
    source = BrokenPriceSource(Violation.EARLY_KNOWN_AT)
    item = next(i for i in source.history() if isinstance(i.record, CorporateAction))
    assert isinstance(item.record, CorporateAction)
    late = Ingested(
        replace(item.record, announced_at=item.ingested_at + timedelta(days=1)), item.ingested_at
    )
    (finding,) = check_known_at_not_after_ingested_at([late])
    assert "announced_at" in finding


def test_fixture_history_covers_the_split_and_revision_cases() -> None:
    """The checks are only as good as the cases they see: the fixture must
    hold splits with bars on both sides and revisions of both record types."""
    history = fixture_history(FixturePriceSource(UNIVERSE_DIR), security_ids())
    splits = [
        i.record
        for i in history
        if isinstance(i.record, CorporateAction) and i.record.action_type is ActionType.SPLIT
    ]
    assert len(splits) >= 3
    revised = [group for group in _by_key(history).values() if len(group) > 1]
    assert {type(group[0].record) for group in revised} == {Bar, CorporateAction}
