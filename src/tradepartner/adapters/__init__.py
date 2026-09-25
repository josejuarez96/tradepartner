"""Adapters: the only place external systems (price/filing sources, the
broker) touch this codebase (ADR 0003). Each interface (`PriceSource`,
`FilingSource`, `Broker`) gets a fixture adapter for tests and a real
adapter behind a thin raw-fetch client; adapters resolve securities
through the security master, never by bare ticker.
"""

from __future__ import annotations
