"""The filing-name confirmation decision of #1597."""

from datetime import UTC, date, datetime

from tradepartner.adapters.filings import FilenameListing
from tradepartner.store.filename_listings import (
    ClassTicker,
    IndependentReading,
    confirm_filename_listings,
    is_filename_listing_refused,
)

STAMP = datetime(2019, 6, 1, tzinfo=UTC)


def filing(
    ticker: str, year: int, *, tag: str | None = None, cik: str = "0000000001"
) -> FilenameListing:
    return FilenameListing(
        cik=cik,
        accession=f"{year}-{ticker}",
        form="10-K",
        accepted_at=datetime(year, 3, 1, tzinfo=UTC),
        valid_from=date(year, 3, 1),
        ticker=ticker,
        exchange="NYSE",
        class_title="Common Stock",
        dei_symbol=tag,
    )


def reading(*pairs: tuple[str, str]) -> IndependentReading:
    return IndependentReading(tuple(ClassTicker(ticker, class_id) for ticker, class_id in pairs))


def decide(
    *filings: FilenameListing,
    cover: tuple[IndependentReading, ...] = (),
    exhibits: tuple[IndependentReading, ...] = (),
    cover_prefix: str | None = None,
    refused: tuple[str, ...] = (),
    first_cover_at: datetime | None = STAMP,
):
    return confirm_filename_listings(
        filings,
        cover_readings=cover,
        form25_readings=exhibits,
        first_cover_accepted_at=first_cover_at,
        first_cover_prefix=cover_prefix,
        primary_class_id="primary",
        refused=refused,
        forms=("10-K", "10-KT"),
    )


def test_own_tag_contradiction_precedes_matching_cover() -> None:
    result = decide(filing("ABC", 2016, tag="XYZ"), cover=(reading(("ABC", "class-a")),))
    assert result[0].verdict == "refused_contradicted"


def test_own_tag_match_confirms_despite_later_difference() -> None:
    result = decide(filing("ABC", 2016, tag="ABC"), cover=(reading(("XYZ", "class-x")),))
    assert result[0].verdict == "confirmed"
    assert result[0].class_id == "primary"


def test_matching_cover_confirms_and_assigns_class_of_two() -> None:
    cover = reading(("GOOG", "class-c"), ("GOOGL", "class-a"))
    result = decide(filing("GOOG", 2016), cover=(cover,))
    assert result[0].verdict == "confirmed"
    assert result[0].class_id == "class-c"
    assert result[0].class_read
    assert {entry.class_id for entry in cover.tickers} - {
        item.class_id for item in result if item.class_read
    } == {"class-a"}
    assert (
        decide(filing("GOOG", 2016), cover=(reading(("GOOGL", "class-a"), ("GOOG", "class-c")),))
        == result
    )


def test_different_cover_set_refuses_without_rename() -> None:
    result = decide(
        filing("SYN", 2016),
        filing("SYN", 2017),
        cover=(reading(("SNV", "class-a"), ("SNVB", "class-b")),),
    )
    assert [item.verdict for item in result] == ["refused_contradicted", "refused_contradicted"]


def test_later_10k_prefix_dates_rename() -> None:
    result = decide(filing("HTZ", 2016), filing("HRI", 2017), cover=(reading(("HRI", "class-a")),))
    assert [item.verdict for item in result] == ["renamed", "confirmed"]
    assert result[0].class_id == "primary"
    assert result[1].class_id == "class-a"


def test_first_cover_filing_prefix_keeps_rename() -> None:
    result = decide(filing("HTZ", 2016), cover=(reading(("HRI", "class-a")),), cover_prefix="HRI")
    assert result[0].verdict == "renamed"


def test_matching_form25_exhibit_confirms() -> None:
    result = decide(
        filing("FSYS", 2016), exhibits=(reading(("FSYS", "class-f")),), first_cover_at=None
    )
    assert result[0].verdict == "confirmed"
    assert result[0].class_id == "class-f"


def test_single_reading_is_written_with_primary_class() -> None:
    result = decide(filing("ABC", 2016), first_cover_at=None)
    assert result[0].verdict == "single_reading"
    assert result[0].class_id == "primary"
    assert not result[0].class_read


def test_owner_refusal_precedes_matching_tag_and_cover() -> None:
    result = decide(
        filing("ABC", 2016, tag="ABC"),
        cover=(reading(("ABC", "class-a")),),
        refused=("0000000001:ABC",),
    )
    assert result[0].verdict == "refused_owner"


def test_readings_after_first_cover_are_ignored_as_candidates() -> None:
    result = decide(filing("ABC", 2016), filing("ABC", 2020), cover=(reading(("ABC", "class-a")),))
    assert len(result) == 1
    assert result[0].filing.accession == "2016-ABC"


def test_result_does_not_depend_on_input_order() -> None:
    early, late = filing("HTZ", 2016), filing("HRI", 2017)
    first = decide(
        early, late, cover=(reading(("HRI", "class-a")),), exhibits=(reading(("HRI", "class-a")),)
    )
    second = decide(
        late, early, exhibits=(reading(("HRI", "class-a")),), cover=(reading(("HRI", "class-a")),)
    )
    assert first == second


def test_deny_list_applies_to_form25_exhibit_too() -> None:
    refused = ("0000000001:FSYS",)
    assert is_filename_listing_refused("0000000001", "fsys", refused)
    assert not is_filename_listing_refused("0000000001", "OTHER", refused)


def test_filing_at_first_cover_acceptance_is_outside_window() -> None:
    cover_at = datetime(2016, 3, 1, tzinfo=UTC)
    assert decide(filing("ABC", 2016), first_cover_at=cover_at) == ()
