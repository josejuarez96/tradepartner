# TradePartner design: direction C, "terminal"

**Chosen by the owner on 2026-10-10** from four samples of the same screen (A product, B consumer, **C terminal**, D statement; screenshots in [`screens/samples/`](screens/samples/)). Every screen is built from this file; a new screen never re-picks fonts, colours or spacing ([ai-tells.md](ai-tells.md) X1).

## Idea
A dark, dense instrument panel for one person watching their own strategies. Numbers first, chart as the hero, panels separated by hairlines rather than floating cards. Calm because nothing moves or glows; dense because the owner reads it, not a visitor.

## Tokens (source: `web/src/index.css`)
| Role | Dark (default) | Light | Use |
|---|---|---|---|
| Ground | `#0c0e12` | `#f6f7f9` | page and panels |
| Raised | `#12151b` | `#ffffff` | top bar, hovered row, drawer |
| Rule | `#20252e` | `#e2e5ea` | 1px gaps between panels, table rows |
| Ink | `#e6e8eb` | `#14171c` | text |
| Muted ink | `#8b929c` | `#5b6470` | labels, secondary text |
| Gain | `#3ecf8e` | `#137a48` | money made, ahead of the S&P; your line when the period ended up |
| Loss | `#f2645a` | `#c2362c` | money lost, behind the S&P |
| Attention | `#f0b03f` | `#955800` | waiting on you |
| Benchmark | `#5d6570` | `#9aa1ab` | the S&P line only |

- **Type:** IBM Plex Sans for words, IBM Plex Mono for every number. Sizes 11, 12.5 (base), 14, 18, 24. Labels in sentence case, never all caps.
- **Shape:** panels are square-cornered regions divided by 1px rules (a grid with a 1px gap over the rule colour). Controls round at 4px. No shadows.
- **Space:** 4px base; panels pad 12px by 14px; table rows 32 to 36px.

## Rules
- Colour only for gain, loss and attention; always paired with a sign, symbol or word.
- Say it once (no badge, icon or subtitle repeating its text).
- Graphics explain themselves: symbol tiles for evidence, a zoned scale for the luck check, countable steps for exams.
- Phone: the quote strip becomes a 2×2 grid, panels stack, navigation moves to a bottom tab bar, touch targets ≥ 44px.
- Re-scan with the AI-tells scanner after every change; no P0 or P1 findings except those this file chose on purpose (dark ground: C7, chosen; Plex is not in the flagged font set).
