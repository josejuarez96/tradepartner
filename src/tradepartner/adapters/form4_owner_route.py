"""Owner attribution of a multi-owner Form 4 read from its XML (plan T350, #2711).

SEC's bulk data set can pin one owner's roles on another in a joint filing (#2705,
#2711), so a Form 4 or 4/A with several reporting owners is routed to its XML before
any bulk transaction is stored. This module is the pure check on that XML's result: it
keeps the filing's own owner-to-transaction association and refuses, with a reason the
caller counts, any filing whose association cannot be taken from the XML as it stands.

A joint filing's `nonDerivativeTransaction` names no single owner: every reporting
owner of the document reports it. The association is therefore one row per
`(filer_cik, line_no)` for every owner and every transaction. Anything else (a row for
an unlisted owner, a repeated pair, a transaction some owner lacks, a repeated owner
CIK, or an owner set that is not the bulk record's) means attribution is missing or
guessed, and the accession is refused: nothing is assigned or duplicated by inference.
A 4/A is checked on its own accession, independent of the original.

No I/O; the caller supplies the parsed filing and the bulk record's owner CIKs.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable

from tradepartner.adapters.filings import SecFormFiling

AMBIGUOUS = "sec_forms_multi_owner_ambiguous"
OWNER_SET_MISMATCH = "sec_forms_multi_owner_set_mismatch"


def is_multi_owner(filer_ciks: Iterable[str]) -> bool:
    """Whether the distinct owner CIKs number more than one."""
    return len(set(filer_ciks)) > 1


def owner_associations(filing: SecFormFiling) -> dict[int, frozenset[str]]:
    """The owners attached to each transaction `line_no` of the XML filing."""
    owners: dict[int, set[str]] = defaultdict(set)
    for row in filing.transactions:
        owners[row.line_no].add(row.filer_cik)
    return {line_no: frozenset(ciks) for line_no, ciks in owners.items()}


def refusal(filing: SecFormFiling, bulk_filer_ciks: Iterable[str] | None = None) -> str | None:
    """The count key naming why a multi-owner XML filing is refused, or `None`.

    Only a filing with more than one reporting owner, in the XML or the bulk record, is
    checked. `bulk_filer_ciks` is the bulk record's owner list when one exists; a
    differing set is refused.
    """
    owners = set(filing.filer_ciks)
    bulk = tuple(bulk_filer_ciks) if bulk_filer_ciks is not None else None
    if not is_multi_owner(filing.filer_ciks) and not (bulk and is_multi_owner(bulk)):
        return None
    if len(owners) != len(filing.filer_ciks):
        return AMBIGUOUS
    if bulk is not None and set(bulk) != owners:
        return OWNER_SET_MISMATCH
    pairs = Counter((row.filer_cik, row.line_no) for row in filing.transactions)
    if any(count > 1 for count in pairs.values()):
        return AMBIGUOUS
    for attached in owner_associations(filing).values():
        if attached != owners:
            return AMBIGUOUS
    return None
