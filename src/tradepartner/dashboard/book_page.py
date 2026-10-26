"""The Books page: a placeholder the nav routes to (T274, #2170).

T274 creates this module with its final `render(conn, settings)` signature so the
shell registers the route once; a later task fills in the body without editing
`app.py`. It computes nothing and writes nothing.
"""

from __future__ import annotations

import duckdb
import streamlit as st

from tradepartner.config import Settings


def render(conn: duckdb.DuckDBPyConnection, settings: Settings) -> None:
    """Draw the Books page from the shell's one read-only connection (placeholder)."""
    del conn, settings
    st.header("Books")
    st.write("Coming next.")
