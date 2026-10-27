"""Shared fixture for the dashboard shell tests (T274, #2170).

Not a `conftest.py`: a second conftest would shadow `tests/conftest.py` for the
`from conftest import ...` lines these tests use. Each dashboard test module imports
`nav_calls_fixture` (autouse, registered as `nav_calls`) from here.

The shell's pages sit in a top navigation (`st.navigation(position="top")`), and
`streamlit.testing.v1.AppTest` cannot click a top-nav item or `switch_page` to a
callable page. So every dashboard test runs with `st.navigation` wrapped: the real
call still registers the pages (Streamlit validates its arguments), the call is
recorded in the `nav_calls` fixture, and `.run()` runs the page named by the
`DASHBOARD_TEST_PAGE` environment variable (default "Home"). A test selects a page
with `monkeypatch.setenv("DASHBOARD_TEST_PAGE", "Backtest")` and `at.run()`.
"""

from __future__ import annotations

import os
from typing import Any

import pytest
import streamlit as st

NavCall = tuple[dict[str, list[Any]], dict[str, Any]]

PAGE_ENV = "DASHBOARD_TEST_PAGE"


@pytest.fixture(autouse=True, name="nav_calls")
def nav_calls_fixture(monkeypatch: pytest.MonkeyPatch) -> list[NavCall]:
    """Wrap `st.navigation` for the test and return the calls made to it."""
    calls: list[NavCall] = []
    real_navigation = st.navigation

    class _Chosen:
        def __init__(self, target: Any) -> None:
            self._target = target

        def run(self) -> None:
            self._target._page()  # the page's closure; `Page.run` refuses a direct call

    def _spy(pages: dict[str, list[Any]], **kwargs: Any) -> _Chosen:
        calls.append((pages, kwargs))
        real_navigation(pages, **kwargs)
        wanted = os.environ.get(PAGE_ENV, "Home")
        [target] = [p for group in pages.values() for p in group if p.title == wanted]
        return _Chosen(target)

    monkeypatch.setattr(st, "navigation", _spy)
    return calls


def nav_titles(calls: list[NavCall]) -> list[str]:
    """Every page title the shell registered in its last `st.navigation` call."""
    [*_, (pages, _kwargs)] = calls
    return [page.title for group in pages.values() for page in group]
