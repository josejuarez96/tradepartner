"""Tests for the dashboard design tokens (T274: the book, benchmark and needs-you roles)."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

import pytest

from tradepartner.dashboard import theme
from tradepartner.dashboard.theme import PALETTES, Mode

_CONFIG_TOML = Path(__file__).resolve().parents[2] / ".streamlit" / "config.toml"


@pytest.mark.parametrize("mode", ["light", "dark"])
def test_book_role_is_the_accent_blue(mode: Mode) -> None:
    palette = PALETTES[mode]
    assert palette.book.color == palette.accent == palette.series[0]
    assert palette.book.dash == ()


@pytest.mark.parametrize("mode", ["light", "dark"])
def test_benchmark_role_is_dashed_secondary_ink(mode: Mode) -> None:
    palette = PALETTES[mode]
    assert palette.benchmark.color == palette.text_secondary
    assert palette.benchmark.dash == (4, 4)


@pytest.mark.parametrize("mode", ["light", "dark"])
def test_needs_you_is_the_orange_series_and_not_a_status_colour(mode: Mode) -> None:
    palette = PALETTES[mode]
    assert palette.needs_you.color == palette.series[1]
    assert palette.needs_you.color not in {palette.good, palette.warning, palette.critical}
    assert palette.needs_you.color != palette.book.color


@pytest.mark.parametrize("mode", ["light", "dark"])
def test_config_toml_stays_equal_to_the_palette(mode: Mode) -> None:
    config = tomllib.loads(_CONFIG_TOML.read_text(encoding="utf-8"))
    assert config["theme"][mode] == theme.streamlit_theme(PALETTES[mode])


def test_series_encodings_use_the_book_and_benchmark_roles() -> None:
    palette = PALETTES["light"]
    enc: dict[str, Any] = theme.series_encodings(palette, ["Book", "SPY"], highlight="Book")
    colors = enc["color"].to_dict()["scale"]["range"]
    dashes = enc["strokeDash"].to_dict()["scale"]["range"]
    assert colors == [palette.book.color, palette.benchmark.color]
    assert dashes == [[], list(palette.benchmark.dash)]
