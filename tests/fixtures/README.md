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

Recorded by the owner on 2026-09-25 (T3, #84). Facts learned while recording, and the
config values they set, are in
[docs/research/2026-09-25-free-data-terms.md](../../docs/research/2026-09-25-free-data-terms.md).

**Size rules** (pre-commit rejects files over 500 KB; the raw payloads were 0.9-7.7 MB):
- `company_facts_<label>.json` keeps the `dei` namespace whole plus every concept whose
  name contains `SharesOutstanding` or `SharesIssued`; all other concepts are dropped
  (`cli_record.trim_company_facts`). Parsers that need another concept extend that list
  and the owner re-records.
- `company_tickers.json` keeps the first 200 rows plus every row for a recorded CIK or
  symbol (`cli_record.trim_company_tickers`).
- `filing_<label>_<document>.gz` is the whole primary document, gzip-compressed (the filing-index excerpt stays plain `.txt`): the
  `dei:` cover tags are spread across the file, so it cannot be truncated. Read it with
  `gzip.open(path, "rt")`; `tests/test_fixture_scrub.py` decompresses before checking.

**Feed:** every bars payload records the feed it was fetched with in its `feed` key. The
free plan returns SIP history for past date ranges (probed 2026-09-25), so
`alpaca.historical_feed` defaults to `sip` and `alpaca_raw.daily_bars` uses it unless a
caller passes `feed=`. `daily_bars.json` is the SIP recording; `daily_bars_iex.json` is
the same window on IEX (about 2.5% of the tape: SPY 576,077 shares on 2020-08-03 against
tens of millions on SIP). **T12 must key `source` on the payload's `feed`
(`alpaca_sip` / `alpaca_iex`) and ingest must never mix feeds for one security**, or a
feed switch shows up as a price revision. Tests of the liquidity rule use the SIP file.

**Fetch times:** `recorded_at.json` maps each fixture to the UTC time it was written,
seconds after its fetch. Snapshot-provenance records (`assets_snapshot.json`,
`company_tickers.json`) take that as their `known_at`; git keeps no file times.

**Acceptance times, for T11:**
- Company-facts entries carry `filed` (a date) and `accn`, not an acceptance time. The
  acceptance time comes from the submissions payload (`filings.recent`) or from the
  older pages `submissions_<label>_<NNN>.json`, trimmed to the accessions the facts
  reference. A fact whose accession has no acceptance time is dropped or flagged,
  **never stamped from `filed`** (a filing accepted after 16:00 ET would leak one session).
- Submissions `acceptanceDateTime` is UTC (`2026-09-24T14:08:40.000Z`); the SGML header
  `ACCEPTANCE-DATETIME` (`20260924100840`) is Eastern time with no zone.
- The companyfacts API omits dimensioned facts, so a dual-class filer's per-class share
  counts (Alphabet: `dei` has only `EntityPublicFloat`) exist only in the filing's iXBRL
  (`filing_dual_class_*.htm.gz` has three `dei:EntityCommonStockSharesOutstanding`
  contexts). Per-class shares come from the document, not from company facts.
- `company_tickers.json` is a current, cap-ordered sample: shape data for
  `snapshot_static` only, never a universe or market-cap source.

## What is recorded

### `tests/fixtures/alpaca/`

| File | Source call | Notes |
|---|---|---|
| `daily_bars.json`, `daily_bars_iex.json` | `alpaca_raw.daily_bars(symbols, start, end)` (SIP; IEX with `feed=`) | SPY, MTUM plus AAPL/MSFT/KO, 2020-08-01..2020-09-30 — brackets AAPL's 2020-08-31 4:1 split and a regular SPY/KO dividend. Raw (`adjustment=raw`); records which feed was used. |
| `corporate_actions.json` | `alpaca_raw.corporate_actions(symbols, start, end)` | Same symbols and window: splits and dividends. |
| `assets_snapshot.json` | `alpaca_raw.assets_snapshot(symbols)` | Current-only assets-endpoint snapshot for the same symbols. |

### `tests/fixtures/edgar/`

| File | Source call | Notes |
|---|---|---|
| `company_tickers.json` | `edgar_raw.company_tickers()` | Current ticker/exchange snapshot, sampled (see size rules). |
| `filing_index_<year>_qtr<n>.txt` | `edgar_raw.filing_index_quarter(year, qtr)` | The real full-index file for one quarter is every SEC filing that quarter; the recorder keeps only the header rows plus the lines naming the three CIKs below, so the fixture stays small. |
| `submissions_plain_issuer.json` | `edgar_raw.submissions(cik)` | Apple Inc. (CIK 0000320193) — a straightforward single-class issuer. |
| `submissions_dual_class.json` | `edgar_raw.submissions(cik)` | Alphabet Inc. (CIK 0001652044) — dual-class (GOOGL/GOOG). |
| `submissions_<label>_<NNN>.json` | `edgar_raw.submissions_page(name)` | Older filings page(s) listed in `filings.files`, trimmed to the accessions the trimmed company facts reference (acceptance times for T11). |
| `submissions_delisted_25nse.json` | `edgar_raw.submissions(cik)` | KLX Energy Services Holdings, Inc. (CIK 0001738827) — has a real Form 25-NSE delisting notice on file as of 2026-09-24; confirm it still resolves before recording, since EDGAR's current-filings feed changes over time. |
| `company_facts_<label>.json` | `edgar_raw.company_facts(cik)` | XBRL company facts for each of the three CIKs above, trimmed to `dei` and share-count concepts (see size rules). |
| `sgml_header_<label>.txt` | `edgar_raw.filing_sgml_header(cik, accession)` | First 4 KB of one filing per CIK (the 25-NSE for the delisted case; a recent 10-K otherwise), chosen automatically by the recorder from that CIK's own filing history. |
| `filing_<label>_<document>.gz` | `edgar_raw.download_filing_file(cik, accession, filename)` | The primary document of that same filing, downloaded and then copied here scrubbed (the raw client itself caches to `edgar.cache_dir`, which is gitignored — this copy is what T11's tests read). For an XBRL-only form (e.g. 25-NSE) whose `primaryDocument` points at an XSL-rendered view like `xslF25X02/primary_doc.xml`, the recorder fetches the raw root-level copy (`primary_doc.xml`) instead; any remaining `/` in the fixture filename is flattened to `__`. |

CIKs and form choices were picked against live EDGAR on 2026-09-24 (see
`src/tradepartner/cli_record.py`); if the owner running T3 finds one no
longer fits (e.g. the 25-NSE filer's filing history changed), swap the
CIK in `EDGAR_CIKS`/`_FORM_PREFERENCE` and note the change here.
