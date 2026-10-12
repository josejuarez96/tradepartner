# H1: known data holes (disclosure, 2026-10-11)

**For:** H1's write-up (`h1-momentum-12-1`, trials 2 and 4) · **Issue:** #1541 · **Source:** the 2026-10-11 read-only coverage check, EDGAR and store evidence only · **Why a note:** the hypothesis file is frozen since registration (a prose-only edit is refused), so disclosures after it live here.

## Bank-level registrants are absent

A bank that is not a holding company registers its securities under Exchange Act section 12(i) and files its periodic reports with its banking regulator (the FDIC, the OCC or the Federal Reserve), not with EDGAR. That is outside knowledge of the rule; the store evidence is this: there is no `securities` row for First Republic Bank (its CIK 0001132979 has none), Signature Bank or Cadence Bank (the only Cadence row is the old holding company, Cadence Bancorporation, 0001614184), so no listing, no bars and no delisting for any of them.

- **Two of the three large 2023 bank failures, Signature and First Republic, are outside the universe on every session**, inside the in-sample window (2020-08-31 to 2023-12-29) and the holdout alike. SVB Financial Group (0000719739), a holding company and SEC filer, is in the store with bars through 2023-03-09 and its 25-NSE row.
- **Cadence Bank did not fail.** It is a surviving bank-level registrant; its absence is a hole in coverage of survivors, not a survivorship hole.
- The absence of the two failed names can only flatter a long-only book, by an amount this check did not measure. Only a vendor or a hand seed fills the class (data-foundation spec, amendment 2026-10-11, #1541, item 5); no pipeline is planned. Claim TP-4.

## Exchange-tagged cover listings were dropped

Both cover-page parsers drop every listing a filer tags with `dei:EntityListingsExchangeAxis` (claim TP-3). Until the `cover-listings-1541` release lands (data-foundation plan, T181), the store's universe lacks IBM, AbbVie, Abbott and 3M for every session before 2026-10-04 (their only listing is the companies snapshot known then), lacks Allstate, Fifth Third and Whirlpool on every session including today's (a `securities` row and no listing at all, so the open paper windows do not see them either), and P&G's primary id reads as a note from 2022-10-19. Trials 2 and 4 read that store.

What the release restores and what stays missing (spec amendment, "Point in time"):

- Listing rows derived for the first time are stamped at their filing's acceptance, so the names above are listed from their 2019 cover pages on, and every registered trial goes stale; whether H1 is re-run is the owner's call, recorded on the release's `after` entry.
- P&G's primary id keeps reading as the note on every session from 2022-10-19 until the release, because the retraction is stamped at the correcting run (#859); spec open question 4.
- Names reclassified by the title fallback (Shockwave, HD Supply, MGM Growth Properties, Sculptor, BioLife) reach past sessions only through the classification rebuild (T182); spec open question 5.
