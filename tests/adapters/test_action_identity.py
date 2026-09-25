"""`CorporateAction` identity, re-dates and cancellations on the price side
(#108): the record's key, the revision rule and the fixture contract."""

from __future__ import annotations

import csv
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from tradepartner.adapters.fixture_prices import FixtureContractError, FixturePriceSource
from tradepartner.adapters.prices import ActionType, CorporateAction, revision_of

FAR_PAST = date(2000, 1, 1)
FAR_FUTURE = date(2030, 12, 31)
T_INGEST = datetime(2019, 3, 8, 20, 0, tzinfo=UTC)


def _make_action(**overrides: object) -> CorporateAction:
    fields: dict[str, object] = {
        "security_id": "SEC_X",
        "action_type": ActionType.SPLIT,
        "ex_date": date(2019, 3, 4),
        "ratio_or_amount": 2.0,
        "known_at": datetime(2019, 3, 1, 21, 0, tzinfo=UTC),
        "source": "test",
    }
    fields.update(overrides)
    return CorporateAction(**fields)  # type: ignore[arg-type]


class TestRecord:
    def test_defaults_are_no_id_and_not_cancelled(self) -> None:
        action = _make_action()
        assert action.source_action_id is None
        assert action.cancelled is False

    def test_key_without_an_id_is_the_ex_date_key(self) -> None:
        assert _make_action().key == ("SEC_X", "split", date(2019, 3, 4))

    def test_key_with_an_id_ignores_type_and_ex_date(self) -> None:
        a = _make_action(source_action_id="A1")
        b = _make_action(source_action_id="A1", ex_date=date(2019, 3, 11))
        assert a.key == b.key == ("SEC_X", "source_action_id", "A1")

    @pytest.mark.parametrize("bad", ["", " A1", 7])
    def test_invalid_source_action_id_is_rejected(self, bad: object) -> None:
        with pytest.raises(ValueError, match="source_action_id"):
            _make_action(source_action_id=bad)

    def test_cancelled_must_be_a_bool(self) -> None:
        with pytest.raises(TypeError, match="cancelled"):
            _make_action(cancelled="TRUE")


class TestRevisionRule:
    def test_redate_with_the_same_id_is_a_revision(self) -> None:
        stored = _make_action(source_action_id="A1")
        incoming = _make_action(source_action_id="A1", ex_date=date(2019, 3, 11))
        revised = revision_of(incoming, stored, ingested_at=T_INGEST)
        assert revised is not None
        assert revised.ex_date == date(2019, 3, 11)
        assert revised.known_at == T_INGEST

    def test_cancellation_is_a_revision(self) -> None:
        stored = _make_action()
        revised = revision_of(_make_action(cancelled=True), stored, ingested_at=T_INGEST)
        assert revised is not None
        assert revised.cancelled is True
        assert revised.known_at == T_INGEST

    def test_repeat_cancellation_is_a_no_op(self) -> None:
        stored = _make_action(cancelled=True, known_at=T_INGEST)
        later = datetime(2019, 3, 15, 20, 0, tzinfo=UTC)
        assert revision_of(_make_action(cancelled=True), stored, ingested_at=later) is None

    def test_a_first_seen_action_cannot_be_cancelled(self) -> None:
        with pytest.raises(ValueError, match="cancel"):
            revision_of(_make_action(cancelled=True), None, ingested_at=T_INGEST)

    def test_different_ids_do_not_revise_each_other(self) -> None:
        with pytest.raises(ValueError, match="key"):
            revision_of(
                _make_action(source_action_id="A2"),
                _make_action(source_action_id="A1"),
                ingested_at=T_INGEST,
            )


_SECURITIES = (
    "security_id,cik,name,benchmark,known_at,ingested_at,source,provenance\n"
    "SEC_A,CIKSEC_A,SEC_A,FALSE,2018-01-02T21:00:00+00:00,2018-01-02T21:10:00+00:00,edgar,filing\n"
)
_HEADER = [
    "security_id",
    "action_type",
    "ex_date",
    "ratio_or_amount",
    "announced_at",
    "source_action_id",
    "cancelled",
    "known_at",
    "ingested_at",
    "source",
    "provenance",
]


def _row(**overrides: str) -> dict[str, str]:
    row = {
        "security_id": "SEC_A",
        "action_type": "split",
        "ex_date": "2019-03-04",
        "ratio_or_amount": "2.0",
        "announced_at": "",
        "source_action_id": "",
        "cancelled": "FALSE",
        "known_at": "2019-03-01T21:00:00+00:00",
        "ingested_at": "2019-03-01T21:10:00+00:00",
        "source": "alpaca",
        "provenance": "action",
    }
    row.update(overrides)
    return row


def _fixture(tmp_path: Path, rows: list[dict[str, str]]) -> Path:
    (tmp_path / "securities.csv").write_text(_SECURITIES)
    with (tmp_path / "corporate_actions.csv").open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=_HEADER)
        writer.writeheader()
        writer.writerows(rows)
    return tmp_path


class TestFixtureContract:
    def test_redate_by_id_loads_as_one_event_with_two_rows(self, tmp_path: Path) -> None:
        fixtures = _fixture(
            tmp_path,
            [
                _row(source_action_id="A1"),
                _row(
                    source_action_id="A1",
                    ex_date="2019-03-11",
                    known_at="2019-03-06T22:00:00+00:00",
                    ingested_at="2019-03-06T22:00:00+00:00",
                ),
            ],
        )
        actions = FixturePriceSource(fixtures).corporate_actions(["SEC_A"], FAR_PAST, FAR_FUTURE)
        assert [(a.ex_date, a.source_action_id) for a in actions] == [
            (date(2019, 3, 4), "A1"),
            (date(2019, 3, 11), "A1"),
        ]

    def test_redate_by_id_must_be_stamped_at_ingest(self, tmp_path: Path) -> None:
        # The new ex-date's proxy would be 2019-03-08; a re-date is a
        # revision, so it may not borrow a first-seen stamp.
        fixtures = _fixture(
            tmp_path,
            [
                _row(source_action_id="A1"),
                _row(
                    source_action_id="A1",
                    ex_date="2019-03-11",
                    known_at="2019-03-02T21:00:00+00:00",
                    ingested_at="2019-03-06T22:00:00+00:00",
                ),
            ],
        )
        with pytest.raises(FixtureContractError, match="back-dated"):
            FixturePriceSource(fixtures)

    def test_cancel_row_loads(self, tmp_path: Path) -> None:
        fixtures = _fixture(
            tmp_path,
            [
                _row(),
                _row(
                    cancelled="TRUE",
                    known_at="2019-03-06T22:00:00+00:00",
                    ingested_at="2019-03-06T22:00:00+00:00",
                ),
            ],
        )
        actions = FixturePriceSource(fixtures).corporate_actions(["SEC_A"], FAR_PAST, FAR_FUTURE)
        assert [a.cancelled for a in actions] == [False, True]

    def test_first_seen_cancel_is_refused(self, tmp_path: Path) -> None:
        fixtures = _fixture(tmp_path, [_row(cancelled="TRUE")])
        with pytest.raises(FixtureContractError, match=r"corporate_actions\.csv:2: .*cancel"):
            FixturePriceSource(fixtures)

    def test_bad_cancelled_cell_is_refused(self, tmp_path: Path) -> None:
        fixtures = _fixture(tmp_path, [_row(cancelled="yes")])
        with pytest.raises(FixtureContractError, match="cancelled"):
            FixturePriceSource(fixtures)

    def test_columns_are_optional(self, tmp_path: Path) -> None:
        # An older fixture without the two columns reads as no id, live.
        (tmp_path / "securities.csv").write_text(_SECURITIES)
        header = [c for c in _HEADER if c not in ("source_action_id", "cancelled")]
        with (tmp_path / "corporate_actions.csv").open("w", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=header)
            writer.writeheader()
            writer.writerow({k: v for k, v in _row().items() if k in header})
        (action,) = FixturePriceSource(tmp_path).corporate_actions(["SEC_A"], FAR_PAST, FAR_FUTURE)
        assert (action.source_action_id, action.cancelled) == (None, False)


class TestReplacementRows:
    """Audit findings 1 and 2 on PR #111: a row that replaces a cancelled
    key in the same ingest revises a known event, so it is stamped at its
    `ingested_at`, never at a first-seen proxy."""

    CANCEL_AT = "2019-03-06T22:00:00+00:00"

    def _cancel_old_key(self) -> list[dict[str, str]]:
        return [
            _row(),
            _row(cancelled="TRUE", known_at=self.CANCEL_AT, ingested_at=self.CANCEL_AT),
        ]

    def test_idless_redate_stamped_at_the_new_proxy_is_refused(self, tmp_path: Path) -> None:
        # Moved to 2019-03-05 in the 2019-03-06 ingest: its proxy (the
        # 2019-03-04 close) predates the ingest, which is look-ahead.
        fixtures = _fixture(
            tmp_path,
            [
                *self._cancel_old_key(),
                _row(
                    ex_date="2019-03-05",
                    known_at="2019-03-04T21:00:00+00:00",
                    ingested_at=self.CANCEL_AT,
                ),
            ],
        )
        with pytest.raises(FixtureContractError, match="replaces"):
            FixturePriceSource(fixtures)

    def test_idless_redate_stamped_at_ingest_loads_even_after_its_proxy(
        self, tmp_path: Path
    ) -> None:
        fixtures = _fixture(
            tmp_path,
            [
                *self._cancel_old_key(),
                _row(ex_date="2019-03-05", known_at=self.CANCEL_AT, ingested_at=self.CANCEL_AT),
            ],
        )
        actions = FixturePriceSource(fixtures).corporate_actions(["SEC_A"], FAR_PAST, FAR_FUTURE)
        assert len(actions) == 3

    def test_id_row_sharing_a_live_idless_key_is_refused(self, tmp_path: Path) -> None:
        fixtures = _fixture(
            tmp_path,
            [
                _row(),
                _row(source_action_id="A1", ingested_at="2019-03-06T22:00:00+00:00"),
            ],
        )
        with pytest.raises(FixtureContractError, match="id-less"):
            FixturePriceSource(fixtures)

    def test_id_row_replacing_a_cancelled_idless_key_loads(self, tmp_path: Path) -> None:
        fixtures = _fixture(
            tmp_path,
            [
                *self._cancel_old_key(),
                _row(source_action_id="A1", known_at=self.CANCEL_AT, ingested_at=self.CANCEL_AT),
            ],
        )
        actions = FixturePriceSource(fixtures).corporate_actions(["SEC_A"], FAR_PAST, FAR_FUTURE)
        assert [a.source_action_id for a in actions].count("A1") == 1
