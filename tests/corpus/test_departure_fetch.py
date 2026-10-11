"""Offline tests for `tradepartner.corpus.departure_fetch` (plan T120; spec
research-labeling, amendment 2026-10-06 C11 and its corpus acceptance line;
plan T168: every acceptance from the filing's SGML header).

Every EDGAR response is a hand-built file under `tests/fixtures/corpus/`,
served by URL through an `httpx.MockTransport`; an unmatched URL fails the
test. Nothing here touches the network or the runtime store.
"""

from __future__ import annotations

import ast
import hashlib
import itertools
import json
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from tradepartner.adapters import edgar_raw
from tradepartner.config import Settings
from tradepartner.corpus import departure_fetch
from tradepartner.corpus.departure_fetch import (
    eightk_passage,
    fetch_departure_corpus,
    normalise_provision,
    notice_exhibit,
)

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "corpus"
CORPUS_SRC = ROOT / "src" / "tradepartner" / "corpus"
USER_AGENT = "TradePartner test-agent test@example.com"
NOW = datetime(2026, 10, 6, 12, tzinfo=UTC)
#: After every fixture window (the latest closes 400 days after 2026-09-25).
LATER = datetime(2028, 1, 1, tzinfo=UTC)

KLX = "0001354457-26-000904"
KLX_AMENDMENT = "0001354457-26-000910"
RELEVANT_8K = "0001738827-26-000046"
NEARER_8K = "0001738827-26-000051"
MARKER_8A = "0001738827-26-000053"

_ARCHIVES = "https://www.sec.gov/Archives/edgar/data"
INDEX_URL = "https://www.sec.gov/Archives/edgar/full-index/2026/QTR3/form.idx"
KLX_TXT_URL = f"{_ARCHIVES}/1738827/000135445726000904/{KLX}.txt"
AMENDMENT_TXT_URL = f"{_ARCHIVES}/1738827/000135445726000910/{KLX_AMENDMENT}.txt"
PREXML_TXT_URL = f"{_ARCHIVES}/1000001/000100000126000002/0001000001-26-000002.txt"
STUB_TXT_URL = f"{_ARCHIVES}/1999999/000135445726000700/0001354457-26-000700.txt"
KLX_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK0001738827.json"
EIGHTK_URL = f"{_ARCHIVES}/1738827/000173882726000046/klxe-20260918.htm"
EIGHTK_HEADER_URL = f"{_ARCHIVES}/1738827/000173882726000046/{RELEVANT_8K}.txt"
MARKER_HEADER_URL = f"{_ARCHIVES}/1738827/000173882726000053/{MARKER_8A}.txt"
#: The SGML headers' acceptances (Eastern wall clock in the fixtures, EDT here).
KLX_ACCEPTED = "2026-09-24T14:08:40+00:00"
AMENDMENT_ACCEPTED = "2026-09-25T13:00:00+00:00"
EIGHTK_ACCEPTED = "2026-09-18T13:26:59+00:00"
MARKER_ACCEPTED = "2026-09-23T21:15:21+00:00"


def _fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def _amendment_txt() -> bytes:
    """The KLX notice refiled as a `25-NSE/A` (same issuer and exchange), its
    header accepted the next morning (09:00 EDT, 13:00 UTC)."""
    text = _fixture(f"klx_25nse_{KLX}.txt").decode()
    text = text.replace(KLX, KLX_AMENDMENT).replace("<TYPE>25-NSE\n", "<TYPE>25-NSE/A\n")
    text = text.replace(
        "<ACCEPTANCE-DATETIME>20260924100840", "<ACCEPTANCE-DATETIME>20260925090000"
    )
    return text.encode()


def _stub_txt() -> bytes:
    """Stubco's 25-NSE with no `ACCEPTANCE-DATETIME` in its header: unstamped."""
    text = _fixture("stub_25nse_0001354457-26-000700.txt").decode()
    return text.replace("<ACCEPTANCE-DATETIME>20260715101500\n", "").encode()


def _shifted_submissions(hours: int) -> bytes:
    """The KLX submissions with every `acceptanceDateTime` `hours` later (#1496)."""
    payload = json.loads(_fixture("submissions_klx_CIK0001738827.json"))
    recent = payload["filings"]["recent"]
    recent["acceptanceDateTime"] = [
        (datetime.fromisoformat(value) + timedelta(hours=hours)).isoformat()
        for value in recent["acceptanceDateTime"]
    ]
    return json.dumps(payload).encode()


def _routes() -> dict[str, tuple[int, bytes]]:
    return {
        INDEX_URL: (200, _fixture("form_2026_qtr3.idx")),
        KLX_TXT_URL: (200, _fixture(f"klx_25nse_{KLX}.txt")),
        AMENDMENT_TXT_URL: (200, _amendment_txt()),
        PREXML_TXT_URL: (200, _fixture("prexml_25_0001000001-26-000002.txt")),
        STUB_TXT_URL: (200, _stub_txt()),
        KLX_SUBMISSIONS_URL: (200, _fixture("submissions_klx_CIK0001738827.json")),
        EIGHTK_URL: (200, _fixture("klx_8k_klxe-20260918.htm")),
        EIGHTK_HEADER_URL: (200, _fixture(f"klx_8k_hdr_{RELEVANT_8K}.txt")),
        MARKER_HEADER_URL: (200, _fixture(f"klx_8a12b_hdr_{MARKER_8A}.txt")),
    }


class Router:
    """Serves `routes` by URL and records every request it receives."""

    def __init__(self, routes: dict[str, tuple[int, bytes]]) -> None:
        self.routes = routes
        self.requests: list[httpx.Request] = []
        self.on_request: Callable[[httpx.Request], None] | None = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.on_request is not None:
            self.on_request(request)
        url = str(request.url)
        if url not in self.routes:
            pytest.fail(f"unrouted EDGAR request: {url}")
        status, body = self.routes[url]
        return httpx.Response(status, content=body)

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self))

    def urls(self) -> list[str]:
        return [str(request.url) for request in self.requests]


@pytest.fixture(autouse=True)
def _reset_edgar_raw_state() -> None:
    """`edgar_raw`'s limiter and block memory are process-wide singletons."""
    edgar_raw._LIMITER._last_request_monotonic = None
    edgar_raw._RATE_LIMIT_BLOCK.clear()


def _settings(cache_dir: Path, **edgar: Any) -> Settings:
    overrides: dict[str, Any] = {"cache_dir": str(cache_dir), **edgar}
    settings = Settings(_env_file=None, sec_edgar_user_agent=USER_AGENT, edgar=overrides)
    if "requests_per_second" in edgar:
        return settings
    # A negligible throttle is past the config's `le=10` (#1108): set it unvalidated.
    fast = settings.edgar.model_copy(update={"requests_per_second": 1e6})
    return settings.model_copy(update={"edgar": fast})


def _run(
    settings: Settings, router: Router, *, now: datetime = NOW, **kwargs: Any
) -> departure_fetch.FetchResult:
    return fetch_departure_corpus(
        since=date(2026, 7, 1),
        until=date(2026, 9, 30),
        settings=settings,
        client=router.client(),
        now=now,
        **kwargs,
    )


def _index_without(*accessions: str) -> bytes:
    lines = _fixture("form_2026_qtr3.idx").decode().splitlines(keepends=True)
    return "".join(
        line for line in lines if not any(f"{acc}.txt" in line for acc in accessions)
    ).encode()


def _records(result: departure_fetch.FetchResult) -> list[dict[str, Any]]:
    lines = result.corpus_path.read_text().splitlines()
    return [json.loads(line) for line in lines]


# --- the population and the counts identity --------------------------------


def test_one_listing_end_from_the_five_rows_with_the_counts_identity(tmp_path: Path) -> None:
    result = _run(_settings(tmp_path), Router(_routes()))

    assert result.corpus_path == tmp_path / "corpus" / "departure-reason" / "corpus.jsonl"
    records = _records(result)
    assert [r["listing_end_id"] for r in records] == [KLX]
    klx = records[0]
    assert klx["cik"] == "0001738827"
    assert klx["issuer"] == "KLX Energy Services Holdings, Inc."
    assert klx["exchange"] == "NASDAQ"
    assert klx["exchange_name"] == "Nasdaq Stock Market LLC"
    assert klx["class_title"] == "rights"
    assert klx["form"] == "25-NSE"
    assert klx["fetch_version"] == departure_fetch.FETCH_VERSION == 2
    assert klx["form25_accepted_at"] == KLX_ACCEPTED
    assert klx["form25_filed_on"] == "2026-09-24"
    assert klx["signature_date"] == "2026-09-24"
    assert klx["effective_on"] == "2026-10-04"
    assert klx["amendments"] == [{"accession": KLX_AMENDMENT, "accepted_at": AMENDMENT_ACCEPTED}]
    assert klx["orphan_amendment"] is False
    assert klx["missing"] == []

    counts = json.loads(result.counts_path.read_text())
    assert counts == {
        "index_rows_seen": 5,
        "kept": 1,
        "orphan_amendment": 0,
        "pre_xml": 1,
        "unstamped": 1,
        "exchange_copy": 1,
        "amendment_attached": 1,
        "identity_holds": True,
        "since": "2026-07-01",
        "until": "2026-09-30",
        "ciks": [],
        "limit": None,
        "fetched_at": "2026-10-06T12:00:00+00:00",
        "fetch_version": 2,
    }
    assert result.counts.kept + result.counts.excluded() == result.counts.index_rows_seen


def test_an_amendment_with_no_original_is_its_own_listing_end(tmp_path: Path) -> None:
    routes = _routes()
    routes[INDEX_URL] = (200, _index_without(KLX))

    result = _run(_settings(tmp_path), Router(routes))

    records = _records(result)
    assert [(r["listing_end_id"], r["form"], r["orphan_amendment"]) for r in records] == [
        (KLX_AMENDMENT, "25-NSE/A", True)
    ]
    assert result.counts.orphan_amendment == 1
    assert result.counts.amendment_attached == 0
    assert result.counts.kept + result.counts.excluded() == result.counts.index_rows_seen == 3


def test_a_blank_acceptance_in_submissions_does_not_matter(tmp_path: Path) -> None:
    """A row with an empty `acceptanceDateTime` used to abort the fetch (#1055
    item 3), then to leave its filing unstamped; the JSON's acceptance is no
    longer read at all (T168), so the header stamps the filing as usual."""
    submissions = json.loads(_fixture("submissions_klx_CIK0001738827.json"))
    recent = submissions["filings"]["recent"]
    for index in range(len(recent["acceptanceDateTime"])):
        recent["acceptanceDateTime"][index] = ""
    routes = _routes()
    routes[KLX_SUBMISSIONS_URL] = (200, json.dumps(submissions).encode())

    result = _run(_settings(tmp_path), Router(routes))

    records = _records(result)
    assert [r["listing_end_id"] for r in records] == [KLX]
    assert records[0]["form25_accepted_at"] == KLX_ACCEPTED
    assert records[0]["eightk"]["accepted_at"] == EIGHTK_ACCEPTED
    assert result.counts.unstamped == 1  # Stubco, whose header carries no acceptance


# --- the acceptance comes from the SGML header (T168) ----------------------------


def test_a_submissions_acceptance_4h_late_gives_way_to_the_header(tmp_path: Path) -> None:
    """SEC's submissions JSON served `acceptanceDateTime` 4 h late from about
    2026-10-01 (#1496): the Form 25, its amendment, the 8-K and the marker all
    carry their SGML header's time, never the JSON's."""
    routes = _routes()
    routes[KLX_SUBMISSIONS_URL] = (200, _shifted_submissions(4))

    klx = _records(_run(_settings(tmp_path), Router(routes)))[0]

    assert klx["form25_accepted_at"] == KLX_ACCEPTED
    assert klx["amendments"] == [{"accession": KLX_AMENDMENT, "accepted_at": AMENDMENT_ACCEPTED}]
    assert klx["eightk"]["accepted_at"] == EIGHTK_ACCEPTED
    marker = next(m for m in klx["markers"] if m["accession"] == MARKER_8A)
    assert marker["accepted_at"] == MARKER_ACCEPTED


def test_the_amendment_matching_orders_by_the_header(tmp_path: Path) -> None:
    """The JSON says the 25-NSE/A was accepted a day BEFORE the original, which
    would make it an orphan; the headers order it after, so it is attached."""
    payload = json.loads(_fixture("submissions_klx_CIK0001738827.json"))
    recent = payload["filings"]["recent"]
    recent["acceptanceDateTime"][recent["accessionNumber"].index(KLX_AMENDMENT)] = (
        "2026-09-23T13:00:00.000Z"
    )
    routes = _routes()
    routes[KLX_SUBMISSIONS_URL] = (200, json.dumps(payload).encode())
    flipped = _routes()
    flipped[AMENDMENT_TXT_URL] = (
        200,
        _amendment_txt().replace(b"20260925090000", b"20260923090000"),
    )

    attached = _run(_settings(tmp_path / "attached"), Router(routes))
    orphaned = _run(_settings(tmp_path / "orphaned"), Router(flipped))

    assert (attached.counts.amendment_attached, attached.counts.orphan_amendment) == (1, 0)
    assert _records(attached)[0]["amendments"] == [
        {"accession": KLX_AMENDMENT, "accepted_at": AMENDMENT_ACCEPTED}
    ]
    # And the header alone decides: an amendment header dated before the original's
    # makes it an orphan even though the JSON orders it after.
    assert (orphaned.counts.amendment_attached, orphaned.counts.orphan_amendment) == (0, 1)


@pytest.mark.parametrize(
    "header_edit",
    [
        pytest.param(lambda t: t.replace("<ACCEPTANCE-DATETIME>20260924100840\n", ""), id="none"),
        pytest.param(
            lambda t: t.replace(f"NUMBER:\t\t{KLX}", "NUMBER:\t\t0000000000-26-000001"), id="other"
        ),
        pytest.param(lambda t: t.replace("</SEC-HEADER>", ""), id="unclosed"),
        pytest.param(lambda t: t.replace("20260924100840", "2026092410"), id="malformed"),
    ],
)
def test_a_form25_header_that_does_not_read_leaves_it_unstamped(
    tmp_path: Path, header_edit: Callable[[str], str]
) -> None:
    """The JSON still carries KLX's acceptance; the header's is what counts."""
    routes = _routes()
    routes[INDEX_URL] = (200, _index_without(KLX_AMENDMENT))
    edited = header_edit(_fixture(f"klx_25nse_{KLX}.txt").decode())
    assert edited.encode() != _fixture(f"klx_25nse_{KLX}.txt")
    routes[KLX_TXT_URL] = (200, edited.encode())

    result = _run(_settings(tmp_path), Router(routes))

    assert _records(result) == []
    assert result.counts.unstamped == 2  # KLX and Stubco
    assert result.counts.kept + result.counts.excluded() == result.counts.index_rows_seen == 4


def test_an_8k_or_marker_header_that_does_not_read_leaves_accepted_at_null(
    tmp_path: Path,
) -> None:
    routes = _routes()
    header = _fixture(f"klx_8k_hdr_{RELEVANT_8K}.txt").decode()
    routes[EIGHTK_HEADER_URL] = (
        200,
        header.replace("<ACCEPTANCE-DATETIME>20260918092659\n", "").encode(),
    )
    routes[MARKER_HEADER_URL] = (404, b"Not Found")
    settings = _settings(tmp_path)

    klx = _records(_run(settings, Router(routes), now=LATER))[0]

    assert klx["form25_accepted_at"] == KLX_ACCEPTED
    assert klx["eightk"]["accession"] == RELEVANT_8K
    assert klx["eightk"]["accepted_at"] is None
    marker = next(m for m in klx["markers"] if m["accession"] == MARKER_8A)
    assert marker["accepted_at"] is None
    assert klx["missing"] == [
        {"document": "marker_header", "url": MARKER_HEADER_URL, "status": 404}
    ]
    rerun = Router(routes)
    _run(settings, rerun, now=LATER)
    assert rerun.requests == []  # the unread header is cached, the 404 remembered


class _RangeRouter(Router):
    """Serves the 8-K header cut at the first `edgar.header_bytes` when asked
    for that range, the whole header for a longer one."""

    def __call__(self, request: httpx.Request) -> httpx.Response:
        response = super().__call__(request)
        if str(request.url) == EIGHTK_HEADER_URL:
            last = int(request.headers["Range"].removeprefix("bytes=0-"))
            return httpx.Response(200, content=response.content[: last + 1])
        return response


def test_a_header_cut_short_is_asked_once_more_at_the_longer_range(tmp_path: Path) -> None:
    router = _RangeRouter(_routes())
    lines: list[str] = []

    result = _run(
        _settings(tmp_path, header_bytes=200), router, max_requests=3, report=lines.append
    )
    klx = _records(result)[0]

    # The count is one per header (documented): the second range is not counted,
    # so a limit of 3 lets 4 header-and-8-K requests through here.
    assert lines[0].startswith("requests to make: 3 (2 SGML headers, 1 8-K documents")
    after = [u for u in router.urls() if u in {EIGHTK_URL, EIGHTK_HEADER_URL, MARKER_HEADER_URL}]
    assert len(after) == 4

    asked = [r.headers["Range"] for r in router.requests if str(r.url) == EIGHTK_HEADER_URL]
    assert asked == ["bytes=0-199", "bytes=0-65535"]
    assert klx["eightk"]["accepted_at"] == EIGHTK_ACCEPTED


# --- the request limit (T168) ----------------------------------------------------


def test_the_request_count_is_reported_and_over_the_limit_nothing_is_fetched(
    tmp_path: Path,
) -> None:
    lines: list[str] = []
    router = Router(_routes())

    with pytest.raises(departure_fetch.RequestLimitExceeded, match="3 requests"):
        _run(_settings(tmp_path), router, max_requests=2, report=lines.append)

    assert lines == [
        "requests to make: 3 (2 SGML headers, 1 8-K documents, "
        "a cut header asking once more; --max-requests 2)"
    ]
    asked = router.urls()
    assert not {EIGHTK_URL, EIGHTK_HEADER_URL, MARKER_HEADER_URL} & set(asked)
    out = tmp_path / "corpus" / "departure-reason"
    assert not (out / "corpus.jsonl").exists()
    assert not (out / "counts.json").exists()


def test_at_the_limit_the_fetch_runs_and_a_cached_rerun_counts_zero(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    lines: list[str] = []
    _run(settings, Router(_routes()), now=LATER, max_requests=3, report=lines.append)
    _run(settings, Router(_routes()), now=LATER, max_requests=0, report=lines.append)

    assert lines == [
        "requests to make: 3 (2 SGML headers, 1 8-K documents, "
        "a cut header asking once more; --max-requests 3)",
        "requests to make: 0 (0 SGML headers, 0 8-K documents, "
        "a cut header asking once more; --max-requests 0)",
    ]


# --- the notice exhibit -----------------------------------------------------


def test_notice_is_text_against_stub_against_none() -> None:
    klx = notice_exhibit(_fixture(f"klx_25nse_{KLX}.txt").decode(), max_chars=4000)
    stub = notice_exhibit(_fixture("stub_25nse_0001354457-26-000700.txt").decode(), max_chars=4000)
    none = notice_exhibit(_fixture("prexml_25_0001000001-26-000002.txt").decode(), max_chars=4000)

    assert klx.status == "text"
    assert klx.document_type == "EX-99.25"
    assert klx.text is not None
    assert klx.text.startswith('The Nasdaq Stock Market LLC (the "Exchange") hereby notifies')
    assert "expired by their terms on September 23, 2026" in klx.text
    assert (stub.status, stub.document_type, stub.text) == ("stub", "EX-99.25", None)
    assert (none.status, none.document_type, none.text) == ("none", None, None)


def test_the_text_notice_is_cut_at_a_sentence_boundary() -> None:
    notice = notice_exhibit(_fixture(f"klx_25nse_{KLX}.txt").decode(), max_chars=250)

    assert notice.text is not None
    assert notice.text.endswith("on the Exchange. [...]")


def test_an_encoded_document_is_never_taken_for_the_notice() -> None:
    graphic = (
        "begin 644 logo.jpg\n"
        + "M_]C_X``02D9)1@`!`0$`8`!@``#_VP!#``@&!@<&!0@'!P<)\n" * 40
        + "end\n"
    )
    text = (
        _fixture("stub_25nse_0001354457-26-000700.txt")
        .decode()
        .replace(
            "</SEC-DOCUMENT>",
            f"<DOCUMENT>\n<TYPE>GRAPHIC\n<SEQUENCE>3\n<FILENAME>logo.jpg\n<TEXT>\n{graphic}"
            "</TEXT>\n</DOCUMENT>\n</SEC-DOCUMENT>",
        )
    )

    notice = notice_exhibit(text, max_chars=4000)

    assert (notice.status, notice.document_type) == ("stub", "EX-99.25")


def test_the_record_carries_the_notice_with_the_raw_bytes_hash(tmp_path: Path) -> None:
    result = _run(_settings(tmp_path), Router(_routes()))

    exhibit = _records(result)[0]["exhibit"]
    assert exhibit["status"] == "text"
    assert exhibit["type"] == "EX-99.25"
    raw = _fixture(f"klx_25nse_{KLX}.txt")
    assert exhibit["sha256"] == hashlib.sha256(raw).hexdigest()


# --- the one 8-K --------------------------------------------------------------


def test_the_nearest_relevant_8k_is_chosen_over_a_nearer_irrelevant_one(tmp_path: Path) -> None:
    router = Router(_routes())
    result = _run(_settings(tmp_path), router)

    eightk = _records(result)[0]["eightk"]
    assert eightk["accession"] == RELEVANT_8K
    assert eightk["form"] == "8-K"
    assert eightk["filed_on"] == "2026-09-18"
    assert eightk["accepted_at"] == EIGHTK_ACCEPTED
    assert eightk["index_items"] == ["3.01", "8.01"]
    assert list(eightk["items"]) == ["3.01", "8.01"]
    assert eightk["body_head"] is None
    assert eightk["sha256"] == hashlib.sha256(_fixture("klx_8k_klxe-20260918.htm")).hexdigest()
    assert not any(NEARER_8K.replace("-", "") in url for url in router.urls())


def test_the_items_split_on_their_headings_in_the_configured_order() -> None:
    html = _fixture("klx_8k_klxe-20260918.htm").decode()

    passage = eightk_passage(html, ["8.01", "3.01"], item_max_chars=4000, eightk_max_chars=12000)

    assert list(passage.items) == ["8.01", "3.01"]
    assert passage.items["3.01"].startswith("Item 3.01 Notice of Delisting")
    assert "Nasdaq will file a Form 25" in passage.items["3.01"]
    assert "Item 8.01" not in passage.items["3.01"]
    assert passage.items["8.01"].startswith("Item 8.01 Other Events.")
    assert "9.01" not in passage.items
    assert passage.body_head is None


def test_the_body_head_is_the_fallback_when_no_listed_item_segments() -> None:
    html = "<html><body><p>KLX Energy announces a rights expiry.</p><p>More text.</p></body></html>"

    passage = eightk_passage(html, ["3.01"], item_max_chars=4000, eightk_max_chars=12000)

    assert passage.items == {}
    assert passage.body_head == "KLX Energy announces a rights expiry.\n\nMore text."


def test_the_8k_items_stop_at_the_whole_document_cap() -> None:
    html = _fixture("klx_8k_klxe-20260918.htm").decode()

    passage = eightk_passage(html, ["3.01", "8.01"], item_max_chars=4000, eightk_max_chars=150)

    assert list(passage.items) == ["3.01", "8.01"]
    assert all(text.endswith(" [...]") for text in passage.items.values())
    assert sum(len(text) for text in passage.items.values()) <= 150 + 2 * len(" [...]")


# --- the marker filings --------------------------------------------------------


def test_the_marker_filings_are_recorded_with_only_their_header_requested(
    tmp_path: Path,
) -> None:
    router = Router(_routes())
    result = _run(_settings(tmp_path), router)

    markers = _records(result)[0]["markers"]
    assert {"form": "8-A12B", "accession": MARKER_8A} in [
        {"form": m["form"], "accession": m["accession"]} for m in markers
    ]
    marker = next(m for m in markers if m["accession"] == MARKER_8A)
    assert marker == {
        "form": "8-A12B",
        "accession": MARKER_8A,
        "filed_on": "2026-09-23",
        "accepted_at": MARKER_ACCEPTED,
    }
    assert [url for url in router.urls() if MARKER_8A.replace("-", "") in url] == [
        MARKER_HEADER_URL
    ]
    (header,) = [r for r in router.requests if str(r.url) == MARKER_HEADER_URL]
    assert header.headers["Range"] == "bytes=0-4095"


# --- the pace, the cache and the missing documents -----------------------------


def test_every_request_is_at_or_under_the_pace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = [1000.0]

    def fake_sleep(seconds: float) -> None:
        clock[0] += seconds

    monkeypatch.setattr(edgar_raw.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(edgar_raw.time, "sleep", fake_sleep)
    router = Router(_routes())
    stamps: list[float] = []
    router.on_request = lambda _request: stamps.append(clock[0])

    _run(_settings(tmp_path, requests_per_second=4.0), router)

    assert len(stamps) == len(router.requests) >= 7
    gaps = [later - earlier for earlier, later in itertools.pairwise(stamps)]
    assert min(gaps) >= 0.25 - 1e-9
    assert all(r.headers["User-Agent"] == USER_AGENT for r in router.requests)


def test_a_rerun_makes_no_second_request(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    first_router = Router(_routes())
    first = _run(settings, first_router, now=LATER)
    first_corpus, first_counts = first.corpus_path.read_bytes(), first.counts_path.read_bytes()
    assert first_router.requests

    second_router = Router(_routes())
    second = _run(settings, second_router, now=LATER)

    assert second_router.requests == []
    assert second.corpus_path.read_bytes() == first_corpus
    assert second.counts_path.read_bytes() == first_counts


def test_a_document_edgar_does_not_serve_is_recorded_under_missing(tmp_path: Path) -> None:
    routes = _routes()
    routes[EIGHTK_URL] = (404, b"Not Found")
    settings = _settings(tmp_path)

    result = _run(settings, Router(routes), now=LATER)

    klx = _records(result)[0]
    assert klx["listing_end_id"] == KLX
    assert klx["eightk"] is None
    assert klx["eightk_note"] == "8-K primary document not served"
    assert klx["missing"] == [{"document": "eightk", "url": EIGHTK_URL, "status": 404}]

    rerun = Router(routes)
    _run(settings, rerun, now=LATER)
    assert rerun.requests == []


def test_a_rerun_refetches_submissions_whose_windows_were_open(tmp_path: Path) -> None:
    """A filing made after the first fetch (here the 25-NSE/A, with the quarter
    still open) is stamped on the rerun, not counted `unstamped`."""
    settings = _settings(tmp_path)
    routes = _routes()
    routes[INDEX_URL] = (200, _index_without(KLX_AMENDMENT))
    submissions = json.loads(_fixture("submissions_klx_CIK0001738827.json"))
    recent = submissions["filings"]["recent"]
    for column in recent.values():
        del column[0]  # the 25-NSE/A row
    routes[KLX_SUBMISSIONS_URL] = (200, json.dumps(submissions).encode())
    first = _run(settings, Router(routes), now=datetime(2026, 9, 24, 23, tzinfo=UTC))
    assert (first.counts.kept, first.counts.amendment_attached) == (1, 0)

    rerun = Router(_routes())
    second = _run(settings, rerun)

    assert (second.counts.unstamped, second.counts.amendment_attached) == (1, 1)
    assert _records(second)[0]["amendments"] == [
        {"accession": KLX_AMENDMENT, "accepted_at": AMENDMENT_ACCEPTED}
    ]
    assert KLX_SUBMISSIONS_URL in rerun.urls()
    assert EIGHTK_URL not in rerun.urls()


def test_an_unserved_form25_is_asked_under_the_issuer_and_counted_unstamped(
    tmp_path: Path,
) -> None:
    """With no `.txt` there is no header, so no acceptance (T168): the filing is
    `unstamped`, never dated from the JSON. It was asked for under the issuer's
    row, not the exchange's, even with the exchange's row first in the index."""
    routes = _routes()
    lines = _index_without(KLX_AMENDMENT).decode().splitlines(keepends=True)
    klx_rows = [line for line in lines if f"{KLX}.txt" in line]
    others = [line for line in lines if f"{KLX}.txt" not in line]
    routes[INDEX_URL] = (200, "".join(others + klx_rows[::-1]).encode())  # exchange row first
    routes[KLX_TXT_URL] = (404, b"Not Found")
    router = Router(routes)

    result = _run(_settings(tmp_path), router)

    assert _records(result) == []
    assert KLX_TXT_URL in router.urls()
    assert (result.counts.unstamped, result.counts.exchange_copy) == (2, 1)
    assert result.counts.kept + result.counts.excluded() == result.counts.index_rows_seen == 4


def test_an_unfinished_quarter_index_is_not_cached(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    for _ in range(2):
        router = Router(_routes())
        fetch_departure_corpus(
            since=date(2026, 7, 1),
            until=date(2026, 9, 30),
            settings=settings,
            client=router.client(),
            now=datetime(2026, 10, 2, 23, tzinfo=UTC),  # inside INDEX_SETTLE_DAYS
        )
        assert INDEX_URL in router.urls()


# --- rule_provision -------------------------------------------------------------


def test_rule_provision_is_normalised(tmp_path: Path) -> None:
    assert normalise_provision("17 CFR 240.12d2-2(a)(2)") == "12d2-2(a)(2)"
    assert normalise_provision("  17 CFR 240.12d2-2(b) ") == "12d2-2(b)"
    assert normalise_provision("") is None

    klx = _records(_run(_settings(tmp_path), Router(_routes())))[0]
    assert klx["rule_provision_raw"] == "17 CFR 240.12d2-2(a)(2)"
    assert klx["rule_provision"] == "12d2-2(a)(2)"


# --- the boundary ---------------------------------------------------------------


def _imported_modules(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            names.add(node.module)
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
    return names


def test_the_corpus_package_imports_nothing_from_tradepartner_research() -> None:
    sources = sorted(CORPUS_SRC.glob("*.py"))
    assert {p.name for p in sources} >= {"__init__.py", "departure_fetch.py"}
    for source in sources:
        imported = _imported_modules(source)
        research = sorted(
            name
            for name in imported
            if name == "tradepartner.research" or name.startswith("tradepartner.research.")
        )
        assert research == [], f"{source.name} imports {research}"
        assert not any(name.startswith("tradepartner.store") for name in imported), source.name
