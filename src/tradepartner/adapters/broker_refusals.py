"""The map from a broker's per-order refusal to its typed error, shared by
`AlpacaBroker` and `FakeBroker` so the fake refuses exactly as the adapter does
(spec amendment 2026-10-18 item 2, T202, #1828). Pure: it reads an
`AlpacaTradingError` and the request, and returns the typed error or `None`."""

from __future__ import annotations

import json
import re
from typing import Protocol

from tradepartner.adapters.broker import OrderRequest
from tradepartner.errors import (
    AssetNotFoundError,
    AssetNotTradableError,
    NotFractionableError,
    ReconciliationError,
)


class Refusal(Protocol):
    """The shape of an `AlpacaTradingError` this map reads (it takes the shape, not
    the class, so the raw client stays imported only where the boundary test allows)."""

    status_code: int | None
    body: str
    retried: bool


#: HTTP status and Alpaca code of an unknown asset (#1729).
NOT_FOUND = 404
ASSET_NOT_FOUND_CODE = 40410000
#: HTTP status of the broker's per-order 403 refusals, and Alpaca's code on them.
FORBIDDEN = 403
FORBIDDEN_CODE = 40310000
#: A 403 40310000 the book's own open orders cause (#1949, sandbox batch 5): a
#: reconciliation fault on either side, never a skip (owner decision 2026-10-19).
_WASH_TRADE_MESSAGE = "potential wash trade detected"


def error_code(error: Refusal) -> object:
    """Alpaca's `code` in a refusal's JSON body, or `None`."""
    try:
        body = json.loads(error.body)
    except ValueError:
        return None
    return body.get("code") if isinstance(body, dict) else None


def error_message(error: Refusal) -> str:
    """The `message` in a refusal's JSON body, or an empty string."""
    try:
        body = json.loads(error.body)
    except ValueError:
        return ""
    message = body.get("message") if isinstance(body, dict) else None
    return message if isinstance(message, str) else ""


def typed_refusal(error: Refusal, request: OrderRequest) -> Exception | None:
    """The typed error for the submit refusals the wrapper's allowlist knows, else
    `None` (the caller raises `error` as is). The map is exact: a 404 only with code
    40410000, a 403 only with code 40310000 and `asset "<the order's symbol>" is not
    tradable|fractionable`, `insufficient qty available` or `potential wash trade
    detected` (the last two reconciliation faults), and never for a retried
    submit (an earlier attempt may have reached the broker, so the run halts and
    resume settles it). By refusal, not by side: the wrapper decides what a sell's
    refusal means."""
    if error.retried:
        return None
    coid = request.client_order_id
    code = error_code(error)
    if error.status_code == NOT_FOUND and code == ASSET_NOT_FOUND_CODE:
        return AssetNotFoundError(f"the broker does not know the asset of order {coid!r}")
    if error.status_code != FORBIDDEN or code != FORBIDDEN_CODE:
        return None
    message = error_message(error)
    if _WASH_TRADE_MESSAGE in message:
        return ReconciliationError(
            f"the broker refused order {coid!r} as a potential wash trade: "
            "the book's own open orders conflict (use complex orders)"
        )
    if "insufficient qty available" in message:
        return ReconciliationError(f"the broker holds less than order {coid!r} sells")
    symbol = re.escape(request.symbol)
    if re.fullmatch(rf'asset "{symbol}" is not fractionable', message):
        return NotFractionableError(f"the broker refused order {coid!r}: asset not fractionable")
    if re.fullmatch(rf'asset "{symbol}" is not tradable', message):
        return AssetNotTradableError(f"the broker refused order {coid!r}: asset not tradable")
    return None
