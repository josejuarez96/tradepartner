# Research Report: Terms of paid vendors used by research jobs (ADR 0013 point 9)

**Brief:** research-labeling spec req 17 (#963), plan T124 (#1348)  ·  **Date:** 2026-10-06 (terms read 2026-10-05; written up 2026-10-09)  ·  **Status:** COMPLETE for one vendor (TypeSafe, the `jev` classification models). Later paid vendors add a line here.  ·  **Agent/model:** team labelcli, claude-opus-5-5, transcribing the terms line the labeling spec records; no page was fetched for this file

The paid-vendor sibling of the [free-data terms report](2026-09-25-free-data-terms.md). ADR 0013 point 9 and spec req 17 require a vendor line here before the first real call. Every quotation below is the one the spec records from the vendor's legal pages, read on 2026-10-05.

## Answer
**Verdict:** n/a. This is a factual terms line, not a hypothesis.  ·  **Confidence:** medium. The quotations are Tier 1 (the vendor's own legal pages), read once on 2026-10-05 and not re-read since. Terms can change without notice to us.

TypeSafe's terms meet ADR 0013's bar: a vendor that trains on inputs without an opt-out is not used, and TypeSafe states it will not train or fine-tune any model on our input. Input retention is not time-bound ("as long as reasonably necessary"). This is accepted because the inputs are public SEC filings and carry nothing else (ADR 0013 Consequences; spec req 3). Output is assigned to the customer. There is no restriction on personal, financial or non-commercial use, or on publishing results. Purchased credits expire 12 months after purchase. The owner's promotional credit is under "additional terms" whose expiry the owner reads in his account and records on #946 before the pilot.

## Evidence

| # | Claim | Source | Tier | Key figure (quoted) | OOS / post-pub? | Net of costs? |
|---|---|---|---|---|---|---|
| 1 | No training on our input | S2, Privacy Policy (last updated 2025-11-19) | 1 | "We will not train or fine tune any artificial intelligence or machine learning models on your prompts or other Input" | n/a | n/a |
| 2 | Input not disclosed beyond service providers | S2 | 1 | "We will not disclose any Input to a third party other than our service providers" | n/a | n/a |
| 3 | Retention not time-bound (privacy policy) | S2 | 1 | retained "for as long as reasonably necessary to provide you with the Services" | n/a | n/a |
| 4 | Output belongs to the customer | S3, Master Customer Agreement (last updated 2026-09-23) | 1 | TypeSafe "disclaims ownership of Output" and "assigns to Customer all of its right, title, and interest, if any, in the Output" | n/a | n/a |
| 5 | Use restrictions | S3 | 1 | restrictions on reselling the service, distillation and reverse engineering; none on personal, financial or non-commercial use or on publishing results | n/a | n/a |
| 6 | Purchased credits expire | S3 | 1 | "Purchased Credits expire ... 12 months after the purchase date" | n/a | n/a |
| 7 | Promotional credits carry their own terms | S3 | 1 | "Promotional Credits are subject to any additional terms ... including terms with respect to expiration, revocation" | n/a | n/a |
| 8 | Retention not time-bound (data processing) | S4, Data Processing Addendum (last updated 2026-04-24) | 1 | retained "for as long as necessary taking into account the purpose of the Processing" | n/a | n/a |
| 9 | Zero data retention is an enterprise option | S4 | 1 | zero data retention is offered as an enterprise option, not on our plan | n/a | n/a |

## Disconfirmation
- Searches run: none for this file. The line transcribes the spec's req 17, which records the pages read on 2026-10-05.
- What was found against: nothing in the three documents restricts our use (one owner, personal research, public filings in, labels kept locally).

## Caveats & gaps
- The pages were read once. A change to training or retention terms is an ADR 0013 "Revisit if" item. Re-read the three documents before any spend beyond the owner's $40 ceiling.
- The API returns no billed cost. Spend is computed from returned input tokens at the list-price snapshot (`research.labeling.price_usd_per_million_input_tokens`, $0.042 per million, vendor Models page 2026-10-05). The owner reconciles it with the first invoice on #946.
- The expiry of the owner's promotional credit is not in the public terms (row 7). The owner records it on #946 before the pilot (spec req 17).

## UNVERIFIED items
- Whether the vendor honours the no-training and no-disclosure statements in practice. This cannot be verified from outside, and the bar rests on the stated terms.

## Follow-up questions (not answered here)
- None for the pilot. A second paid vendor, or the SDK or a local stack in a `research` dependency group, adds its own line here.

## Sources
- S1: TypeSafe legal index, `https://docs.typesafe.ai/legal`, read 2026-10-05, linking S2 to S4.
- S2: TypeSafe Privacy Policy, last updated 2025-11-19, read 2026-10-05.
- S3: TypeSafe Master Customer Agreement, last updated 2026-09-23, read 2026-10-05.
- S4: TypeSafe Data Processing Addendum, last updated 2026-04-24, read 2026-10-05.
