"""Dashboard design tokens (docs/design/dashboard.md, #165; plan T21).

The one place colours are defined. `.streamlit/config.toml` mirrors them for
Streamlit's own widgets (`streamlit_theme` builds that mapping, and a test
keeps the file equal to it), and charts read them through `style`, so a
chart and a tile never disagree on a colour. Pages name a role (`accent`,
`critical`, a status), never a colour.

Charts are Altair, which ships with Streamlit; the standard allows Plotly or
Altair, and Plotly is not a dependency.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final, Literal

import altair as alt
import streamlit as st

Mode = Literal["light", "dark"]
Status = Literal["good", "warning", "critical"]
BadgeColor = Literal["green", "orange", "red"]


@dataclass(frozen=True)
class Palette:
    """One theme's colour roles; `series` are the categorical slots in fixed order."""

    background: str
    surface: str
    border: str
    text: str
    text_secondary: str
    series: tuple[str, str, str, str]
    good: str
    good_bg: str
    warning: str
    warning_bg: str
    critical: str
    critical_bg: str

    @property
    def accent(self) -> str:
        """Series 1: the thing being examined."""
        return self.series[0]


PALETTES: Final[dict[Mode, Palette]] = {
    "light": Palette(
        background="#f6f7f4",
        surface="#ffffff",
        border="#d6dad3",
        text="#1c2026",
        text_secondary="#5a6470",
        series=("#2a78d6", "#eb6834", "#1baf7a", "#eda100"),
        good="#2c7a4b",
        good_bg="#ddf0e3",
        warning="#b0741c",
        warning_bg="#f8ebd2",
        critical="#b23f36",
        critical_bg="#f7dedb",
    ),
    "dark": Palette(
        background="#12161a",
        surface="#1a2027",
        border="#303841",
        text="#e7eaed",
        text_secondary="#9aa5b1",
        series=("#3987e5", "#d95926", "#199e70", "#c98500"),
        good="#6cc08f",
        good_bg="#17301f",
        warning="#d9a650",
        warning_bg="#3a2e15",
        critical="#e07b73",
        critical_bg="#3b1f1d",
    ),
}

#: Streamlit's named colour per status (mapped to the tokens in config.toml), and its icon.
STATUS_COLOR: Final[dict[Status, BadgeColor]] = {
    "good": "green",
    "warning": "orange",
    "critical": "red",
}
STATUS_ICON: Final[dict[Status, str]] = {
    "good": ":material/check_circle:",
    "warning": ":material/warning:",
    "critical": ":material/error:",
}

_CARD_RADIUS: Final = "8px"
_BAR_END_RADIUS: Final = 4
_MUTED_OPACITY: Final = 0.6


def streamlit_theme(palette: Palette) -> dict[str, Any]:
    """The `[theme.<mode>]` table of `.streamlit/config.toml` for `palette`."""
    return {
        "primaryColor": palette.accent,
        "backgroundColor": palette.background,
        "secondaryBackgroundColor": palette.surface,
        "textColor": palette.text,
        "borderColor": palette.border,
        "baseRadius": _CARD_RADIUS,
        "chartCategoricalColors": list(palette.series),
        "greenColor": palette.good,
        "greenBackgroundColor": palette.good_bg,
        "orangeColor": palette.warning,
        "orangeBackgroundColor": palette.warning_bg,
        "redColor": palette.critical,
        "redBackgroundColor": palette.critical_bg,
    }


def palette() -> Palette:
    """The palette of the theme the viewer has selected (light when unknown)."""
    mode = st.context.theme.type
    return PALETTES["dark" if mode == "dark" else "light"]


def status_badge(label: str, status: Status) -> None:
    """A status chip: icon, label and the status colour, never colour alone."""
    st.badge(label, icon=STATUS_ICON[status], color=STATUS_COLOR[status])


def bar_mark(palette: Palette) -> dict[str, Any]:
    """Bar mark properties: accent fill, rounded data end."""
    return {"color": palette.accent, "cornerRadiusEnd": _BAR_END_RADIUS}


def threshold_mark(palette: Palette) -> dict[str, Any]:
    """A threshold rule: critical ink, dashed."""
    return {"color": palette.critical, "strokeDash": [4, 4], "strokeWidth": 2}


def series_encodings(
    palette: Palette, names: list[str], highlight: str, field: str = "series"
) -> dict[str, Any]:
    """Line-chart encodings for one examined series among comparisons: `highlight` in
    the accent, solid; every other name (benchmarks) in secondary ink at the muted
    opacity, dashed. Colour follows the name, never its rank; a legend is shown."""
    others = sorted(n for n in names if n != highlight)
    domain = [highlight, *others]
    solid: list[int] = []
    return {
        "color": alt.Color(
            f"{field}:N",
            scale=alt.Scale(
                domain=domain, range=[palette.accent, *[palette.text_secondary] * len(others)]
            ),
            legend=alt.Legend(title=None, orient="top"),
        ),
        "strokeDash": alt.StrokeDash(
            f"{field}:N",
            scale=alt.Scale(domain=domain, range=[solid, *[[4, 4]] * len(others)]),
            legend=None,
        ),
        "opacity": alt.Opacity(
            f"{field}:N",
            scale=alt.Scale(domain=domain, range=[1, *[_MUTED_OPACITY] * len(others)]),
            legend=None,
        ),
    }


def style[Chart: (alt.Chart, alt.LayerChart)](chart: Chart, palette: Palette) -> Chart:
    """Apply the tokens to an Altair chart: recessive grid, secondary-ink axes, no frame."""
    styled: Chart = (
        chart.configure(background="transparent")
        .configure_axis(
            labelColor=palette.text_secondary,
            titleColor=palette.text_secondary,
            gridColor=palette.border,
            gridOpacity=_MUTED_OPACITY,
            domainColor=palette.border,
        )
        .configure_view(stroke=None)
        .configure_range(category=list(palette.series))
    )
    return styled
