"""TradePartner Streamlit dashboard (T21a: shell; T21 adds the data-health page).

The shell (`app.py`) owns the store-state logic and page navigation; a page
module (e.g. the future `health_page.py`) renders through the shell's own
short-lived, read-only connection and never opens the store itself.
"""

from __future__ import annotations
