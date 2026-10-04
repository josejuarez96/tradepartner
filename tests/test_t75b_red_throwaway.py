"""Deliberately failing test for the T75b ruleset check (AC16); this PR is closed, never merged."""


def test_t75b_red_throwaway() -> None:
    """Fail on purpose so the PR's `checks` run is red."""
    raise AssertionError("T75b AC16: deliberate red check; close this PR, never merge it")
