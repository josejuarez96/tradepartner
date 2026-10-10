# TradePartner design: direction E, "research-grade Robinhood"

**Chosen by the owner on 2026-10-10** after five samples of the same screen (A product, B consumer, C terminal, D statement, **E**; screenshots in [`screens/samples/`](screens/samples/)). C was rejected as "a wall of text"; E keeps C's colours and type. Every screen is built from this file; a new screen never re-picks fonts, colours or spacing ([ai-tells.md](ai-tells.md) X1).

## Idea
Robinhood's frame made research grade. One big number, one big clean chart, a side rail of short rows, lots of air. The research layer is what the chart and statistics carry: the backtest's expected range as a band, event dots (rebalances, book starts), the statistics a researcher checks, and a note on method. Each screen leads with a picture, not a table: Overview with your return against what the backtest expected, Research with a map of every tested idea by return and luck check. Detail opens on tap; rows stay one line.

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

- **Type:** IBM Plex Sans for words, IBM Plex Mono for every number. Base 14; section headings 18; screen hero 30 (words) or 40 (the total). Sentence case, never all caps.
- **Shape:** sections on the page itself, separated by 1px rules; a side rail divided by one vertical rule. Range buttons and percentage tags are rounded (pill / 6px). No shadows, no boxed cards except the one featured "needs you" item.
- **Space:** 4px base; page max 1180px, 28px side padding (16px on phone), main column plus a 340px rail with a 48px gap; rows 48 to 56px.

## Rules
- Colour only for gain, loss and attention; always paired with a sign, symbol or word.
- Say it once (no badge, icon or subtitle repeating its text).
- Graphics explain themselves: symbol tiles for evidence, a zoned scale for the luck check, countable steps for exams.
- Phone: the rail drops below the main column, navigation moves to a bottom tab bar, touch targets ≥ 44px, nothing scrolls sideways; chart labels never overlap (they are placed with collision checks).
- Re-scan with the AI-tells scanner after every change; no P0 or P1 findings except those this file chose on purpose (dark ground: C7, chosen; Plex is not in the flagged font set).
