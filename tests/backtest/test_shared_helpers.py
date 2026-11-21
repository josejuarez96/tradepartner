"""Helpers the sweep and lab stacks share with the hypothesis stack have one definition (#3664)."""

from __future__ import annotations

from tradepartner.backtest import frozen, hypothesis, lab, run, sweep


def test_each_helper_has_one_definition() -> None:
    assert sweep._flatten is hypothesis._flatten
    assert sweep._DATE_KEYS is hypothesis._DATE_KEYS
    assert sweep._canonical_json is frozen._canonical_json
    assert lab._on_store is run._on_store
    assert lab.DEFAULT_RUN_BY is run.DEFAULT_RUN_BY
