# Fixtures

Recorded raw payloads from Alpaca and SEC EDGAR, used by the adapter
parser tests (T11, T12). Every file here is a **raw payload** (spec
"Definitions": "the bytes a source returns") — no parsing happened before
it was written, and every value that could identify the owner or their
account has been scrubbed (see `tests/test_fixture_scrub.py`).

## Regenerating

```bash
uv run python -m tradepartner.cli_record
```

Requires real `ALPACA_API_KEY`, `ALPACA_API_SECRET` and
`SEC_EDGAR_USER_AGENT` in `.env` (see `.env.example`); it refuses to run
and names whichever of those three is missing. This is an owner-run,
one-time step (plan task T3) — the test suite and CI never call it and
never touch the network.

Before writing anything, every payload passes through
`tradepartner.cli_record.scrub_json` / `.scrub_text`. A value under a
sensitive header name (`Authorization`, `APCA-API-KEY-ID`,
`APCA-API-SECRET-KEY`, `User-Agent`, case-insensitive) or a `key`/`secret`
-named field holding a bare token is replaced *wholly* with `<scrubbed>`;
every other string only has the matched span of a configured secret (as a
substring, including the HTTP Basic `base64("key:secret")` form of the
Alpaca credential pair), an email address, an Alpaca-style key prefix, or
a `key`/`secret`-labeled token replaced, leaving the rest of the string
intact. `tests/test_fixture_scrub.py` independently re-checks every file
here against the same patterns.

As of this PR, `tests/fixtures/{alpaca,edgar}/` are still **empty**: T2
builds the clients and the recorder; T3 (the owner, with real keys) runs
it and commits the output in a follow-up PR.

**Notes for whoever runs T3:**
- `assets_snapshot.json` is fetched through `TradingClient(paper=True)`
  (`alpaca_raw._trading_client`). A **live-only** Alpaca key pair will
  fail that call with an auth/permission error even though
  `daily_bars.json`/`corporate_actions.json` (market data, account-type
  agnostic) succeed — if that happens, it's expected, not a bug; open an
  issue if the recorder should instead try both base URLs.
- `daily_bars.json` is fetched with `feed=DataFeed.IEX` by default
  (`alpaca_raw.DEFAULT_FEED`) — ADR 0003's working assumption that the
  free plan is IEX-only, not yet confirmed. If T3 finds the account
  actually gets SIP or another feed, note that here and consider whether
  `daily_bars`'s default should change.

## What T3 will record

### `tests/fixtures/alpaca/`

| File | Source call | Notes |
|---|---|---|
| `daily_bars.json` | `alpaca_raw.daily_bars(symbols, start, end)` | SPY, MTUM plus AAPL/MSFT/KO, 2020-08-01..2020-09-30 — brackets AAPL's 2020-08-31 4:1 split and a regular SPY/KO dividend. Raw (`adjustment=raw`); records which feed was used. |
| `corporate_actions.json` | `alpaca_raw.corporate_actions(symbols, start, end)` | Same symbols and window: splits and dividends. |
| `assets_snapshot.json` | `alpaca_raw.assets_snapshot(symbols)` | Current-only assets-endpoint snapshot for the same symbols. |

### `tests/fixtures/edgar/`

| File | Source call | Notes |
|---|---|---|
| `company_tickers.json` | `edgar_raw.company_tickers()` | Full current ticker/exchange snapshot. |
| `filing_index_<year>_qtr<n>.txt` | `edgar_raw.filing_index_quarter(year, qtr)` | The real full-index file for one quarter is every SEC filing that quarter; the recorder keeps only the header rows plus the lines naming the three CIKs below, so the fixture stays small. |
| `submissions_plain_issuer.json` | `edgar_raw.submissions(cik)` | Apple Inc. (CIK 0000320193) — a straightforward single-class issuer. |
| `submissions_dual_class.json` | `edgar_raw.submissions(cik)` | Alphabet Inc. (CIK 0001652044) — dual-class (GOOGL/GOOG). |
| `submissions_delisted_25nse.json` | `edgar_raw.submissions(cik)` | KLX Energy Services Holdings, Inc. (CIK 0001738827) — has a real Form 25-NSE delisting notice on file as of 2026-09-24; confirm it still resolves before recording, since EDGAR's current-filings feed changes over time. |
| `company_facts_<label>.json` | `edgar_raw.company_facts(cik)` | XBRL company facts for each of the three CIKs above. |
| `sgml_header_<label>.txt` | `edgar_raw.filing_sgml_header(cik, accession)` | First 4 KB of one filing per CIK (the 25-NSE for the delisted case; a recent 10-K otherwise), chosen automatically by the recorder from that CIK's own filing history. |
| `filing_<label>_<document>` | `edgar_raw.download_filing_file(cik, accession, filename)` | The primary document of that same filing, downloaded and then copied here scrubbed (the raw client itself caches to `edgar.cache_dir`, which is gitignored — this copy is what T11's tests read). For an XBRL-only form (e.g. 25-NSE) whose `primaryDocument` points at an XSL-rendered view like `xslF25X02/primary_doc.xml`, the recorder fetches the raw root-level copy (`primary_doc.xml`) instead; any remaining `/` in the fixture filename is flattened to `__`. |

CIKs and form choices were picked against live EDGAR on 2026-09-24 (see
`src/tradepartner/cli_record.py`); if the owner running T3 finds one no
longer fits (e.g. the 25-NSE filer's filing history changed), swap the
CIK in `EDGAR_CIKS`/`_FORM_PREFERENCE` and note the change here.
