# Form 4 rule 5 comparison: the read-only disagreement baseline

**Task:** T353 (#2900; #2714, #2706) · **Date:** 2026-11-02 · **Status:** COMPLETE (offline; counts only, no store write, no SEC request) · **Agent/model:** team sont353, Claude Sonnet 5.5

## Answer

Run the T349 comparison at its defaults (2-decimal price and shares, exact identity, owner and order) over what is on disk, and the share of disagreeing accessions is **0 of 92** on the order sample and **17 of 12,395 (0.14%)** on accessions whose XML rows are stored. The highest single quarter is **2024q2 at 15 of 2,141 (0.70%)**, and every one of those 15 is a multi-owner accession where SEC's bulk data puts the role flags on the wrong owner. Single-owner accessions disagree **2 of 10,705 (0.02%)**.

**Recommendation for spec question 1** (owner's call): keep price and shares at 2 decimals with zero extra tolerance, keep identity, owner and order exact, and set `events.form4_compare_threshold = 0.01` (1%).

- It sits above the worst observed quarter (0.70%) with a margin of about 1.4 times, and far above the 0.02% single-owner rate.
- It is a share over the whole run, so it dilutes the order sample. A run that compares the sample (20 per quarter) and every XML-routed accession (1,856 to 2,712 per quarter) would pass a quarter whose sample is entirely mis-ordered (20 of about 2,276 is 0.9%), and the unchecked bulk rows would then be stored. **Recommend keeping a per-quarter refusal on the order sample alongside the 1% share**: one disagreeing sample accession still stops that quarter's bulk writes, as today, and the 1% share governs the XML-routed comparison. Without that, the 1% line is too loose to guard bulk order. This is a design point for T351 (raise on #2714).
- Cost of being wrong: at 1%, a quarter with a 0.9% systematic reader fault would pass and store the trusted (XML) row plus an unresolved marker. Markers never change facts, and XML is the trusted source for every field, so the stored row is the right one either way.

Two things the owner should know before enabling it, neither changed here:

1. **Multi-owner accessions are the disagreement.** 15 of the 17 are 2024q2 multi-owner accessions (SEC bulk assigns the owner roles to the wrong owner; #2706). With #2711's route (multi-owner goes to XML before any bulk row is stored) their bulk side is never written, so a bulk-versus-XML comparison of them measures SEC's defect, not ours. If they stay in the denominator the numerator rises with every such accession. If the owner would rather have the share describe only the rows the bulk route would have stored, exclude multi-owner accessions from the comparison. At 1% either choice passes on this data.
2. **Two single-owner `deemed_execution_date` disagreements** (2019q1 `0001491519-19-000001`, 2019q3 `0001763113-19-000007`) look like a row-association slip in the bulk set: the date sits on a different transaction than in the XML. This is the kind of order defect the sample exists to catch. It was already a failure under today's `rows_agree`, so it is not new, but it shows the 1% line will not hide order differences: they are counted like any other.

## What ran

| Item | Value |
| --- | --- |
| Quarters | #2706's 11 refused: 2016q1, 2017q4, 2018q4, 2019q1, 2019q3, 2019q4, 2020q1, 2020q3, 2021q3, 2022q1, 2024q2 |
| Code | main at `c630c7a7` (T349 `form4_compare` with the `FORM4_COMPARE_FIELDS` defaults; #2710 quoted titles, #2705 roles and #2711 multi-owner routing all landed) |
| Pending set | store backup `bak-20261031-0441`, opened read-only (the state #2706 refused), cached `form.idx` files and bulk zips |
| Stored XML rows | store backup `bak-20261102-1729`, read-only |
| Sample XML | the 100 XMLs fetched for #2706 (`~/tradepartner-probes/f4second/xml`); no new request |
| Script | `~/tradepartner-probes/t353/probe.py`; results `report.json`, `pairs.json` beside it |
| Network, fact writes | none |

The recorded numbers are in `tests/adapters/test_form4_compare.py` (`BASELINE`, `BASELINE_DISAGREEMENTS`), and the tests re-derive the numerator, denominator, threshold outcomes and request cost from them.

## Two populations

The rule 5 unit is the XML-fetched accession compared in a run: the order sample plus XML-routed filings. No run has fetched the 11 quarters' full XML-routed set yet, so the baseline uses two stand-ins. Neither is the exact population a rule 5 run will compare.

**A. The order sample.** The sample the current code would draw (20 per quarter by SHA-256 order, multi-owner accessions now excluded) where its XML is on disk. Only 2017q4, 2019q1, 2019q3, 2020q3 and 2024q2 have any (from #2706's replay), and 8 of those 100 accessions left the sample when multi-owner accessions were excluded, so 92 compare. The other six quarters' sample XML was never retained, and fetching it needs the network, which this task forbids.

| Quarter | Compared | Disagree |
| --- | ---: | ---: |
| 2017q4 | 19 | 0 |
| 2019q1 | 19 | 0 |
| 2019q3 | 20 | 0 |
| 2020q3 | 17 | 0 |
| 2024q2 | 17 | 0 |
| **Total** | **92** | **0** |

For reference, #2706 saw 5 of 100 disagree before its fixes (4 CSV-quoted titles, fixed in #2710, and the 2024q2 owner swap, now excluded from the sample by #2711).

**B. Stored XML versus the bulk zip.** Every accession in the quarter's zip whose XML-derived rows are already in the store (`sec_form4_transactions`, source XML), compared with the bulk rows. This covers all 11 quarters. It is biased toward what earlier runs routed to XML (price under 1.00, multi-owner, earlier samples), which is also what a rule 5 run will mostly compare.

| Quarter | Compared | of which multi-owner | Disagree | Share | Fields (accessions) |
| --- | ---: | ---: | ---: | ---: | --- |
| 2016q1 | 1,471 | 120 | 0 | 0 | none |
| 2017q4 | 892 | 43 | 0 | 0 | none |
| 2018q4 | 986 | 71 | 0 | 0 | none |
| 2019q1 | 1,078 | 53 | 1 | 0.09% | deemed_execution_date |
| 2019q3 | 934 | 59 | 1 | 0.11% | deemed_execution_date |
| 2019q4 | 941 | 58 | 0 | 0 | none |
| 2020q1 | 1,142 | 70 | 0 | 0 | none |
| 2020q3 | 1,009 | 48 | 0 | 0 | none |
| 2021q3 | 788 | 11 | 0 | 0 | none |
| 2022q1 | 1,013 | 22 | 0 | 0 | none |
| 2024q2 | 2,141 | 1,135 | 15 | 0.70% | is_ten_percent_owner (12), is_director (2), is_officer (1), is_other (1) |
| **Total** | **12,395** | **1,690** | **17** | **0.14%** | |

Row-level field counts (diagnosis only, not in the share): `is_ten_percent_owner` 144, `is_director` 4, `is_other` 2, `is_officer` 2 (all 2024q2) and `deemed_execution_date` 4 (2019q1, 2019q3). No price, shares, share-count, key or identity disagreement at 2 decimals appears anywhere, so no tolerance beyond the 2-decimal rounding is needed.

2024q2 holds 115 further accessions with a stored XML marker but no stored rows; they are left out (they are not a comparison).

## Request cost and headroom

Rule 5 adds **no XML request**. Its eligible set is the existing order sample plus the XML-routed filings, and the route fetches every one of those today; the comparison only reads what was already fetched. Only enlarging the sample would add requests.

Today's XML fetches per quarter are the XML-routed pending accessions plus the sample of 20:

| Quarter | Pending | XML-routed (multi-owner / under 1.00) | + sample | Requests at 1 to 2 per fetch |
| --- | ---: | ---: | ---: | ---: |
| 2016q1 | 61,865 | 2,692 (1,341 / 1,351) | 2,712 | 2,712 to 5,424 |
| 2017q4 | 37,128 | 2,066 (1,217 / 849) | 2,086 | 2,086 to 4,172 |
| 2018q4 | 37,647 | 1,996 (1,081 / 915) | 2,016 | 2,016 to 4,032 |
| 2019q1 | 61,289 | 1,987 (962 / 1,025) | 2,007 | 2,007 to 4,014 |
| 2019q3 | 34,066 | 1,836 (961 / 875) | 1,856 | 1,856 to 3,712 |
| 2019q4 | 34,771 | 1,922 (1,038 / 884) | 1,942 | 1,942 to 3,884 |
| 2020q1 | 63,165 | 2,262 (1,190 / 1,072) | 2,282 | 2,282 to 4,564 |
| 2020q3 | 34,619 | 2,064 (1,102 / 962) | 2,084 | 2,084 to 4,168 |
| 2021q3 | 37,956 | 2,332 (1,555 / 777) | 2,352 | 2,352 to 4,704 |
| 2022q1 | 64,283 | 2,245 (1,254 / 991) | 2,265 | 2,265 to 4,530 |
| 2024q2 | 49,447 | 2,256 (1,250 / 1,006) | 2,276 | 2,276 to 4,552 |
| **Total** | | **23,658** | **23,878** | **23,878 to 47,756** |

A fetch is one request when the submissions primary document is the XML and two when a filing-index lookup comes first. Against `sec_forms_max_requests` = 40,000 per run: any one quarter fits with at least **34,576** left (the busiest, 2016q1, costs at most 5,424), all 11 quarters need, on XML requests alone, **one run at best and two at worst** (23,878 to 47,756; a changed-zip marker recheck adds a few more), and these XML requests come on top of the submissions pages and SGML anchor headers the run already makes. Identity-bad and order-bad accessions numbered zero in all 11 quarters under the current reader, and the header-disagreement and bulk-missing routes need a submissions record, so they are not in these counts.

## Limits

- The baseline is an offline proxy. Population A is partial (five quarters, 92 accessions). Population B is selected by earlier routing, not drawn at random.
- A rule 5 run also compares XML-routed accessions that have no stored rows yet (new fetches). Their disagreement rate is not measured here; the owner's real-store step after T351 measures it.
- For the 2024q2 multi-owner accessions the XML owner flags are taken as right, as in #2706's hand check of `0001437749-24-011338`; the bulk side is SEC's owner join. This is checked on one accession by hand, not on all 15.
- Nothing here set the threshold key. It stays unset until the owner answers spec question 1.
