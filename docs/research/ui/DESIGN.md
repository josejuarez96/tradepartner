# TradePartner design: tokens, type, and the workstation with a modern twist

**Updated 2026-10-10 (fourth session).** Tokens and type are unchanged since the owner chose direction E (five samples, screenshots in [`screens/samples/`](screens/samples/); C rejected as "a wall of text"). The layout is new: the app is now built around the owner's four routines ([interaction.md](interaction.md)), and the look is the one he chose for the lab, "a research workstation with a modern twist", calmed for the phone. Every screen is built from this file; a new screen never re-picks fonts, colours or spacing ([ai-tells.md](ai-tells.md) X1).

## Tokens (source: `web/src/index.css`)
| Role | Dark (default) | Light | Use |
|---|---|---|---|
| Ground | `#0c0e12` | `#f6f7f9` | page |
| Raised | `#12151b` | `#ffffff` | panels, top bar, dialogs, hovered row |
| Rule | `#20252e` | `#e2e5ea` | 1px borders and row rules |
| Ink | `#e6e8eb` | `#14171c` | text |
| Muted ink | `#8b929c` | `#5b6470` | labels, secondary text |
| Gain | `#3ecf8e` | `#137a48` | ✓ marks, money made, ahead of SPY |
| Loss | `#f2645a` | `#c2362c` | ✕ marks, money lost; the kill switch button |
| Attention | `#f0b03f` | `#955800` | waiting on you, something wrong, a refusal code |
| Benchmark | `#5d6570` | `#9aa1ab` | the S&P line only |

- **Type:** IBM Plex Sans for words, IBM Plex Mono for every number **and every engine name** (`params_sha256`, `promote_at_least`, `refused_window`, frozen keys, commands). Base 14; screen titles 22; section headings 15 to 18. Sentence case, never all caps.
- **Shape:** radius 10px on panels, 6px on controls. No shadows (the popover and dialog shadows are switched off), no gradients, no glass.
- **Space:** 4px base; 16px side gutter on phone, 28px on desktop; touch targets at least 44px on phone; rows 48 to 56px.

## Layout: the workstation, calmed

The owner pointed at desktop backtesters (Build Alpha, RealTest, AmiBroker, QuantConnect's project page): settings on the left, the run, results in tabs. The app takes that shape and keeps the calm of E:

- **Soft panels, few of them.** A panel (raised ground, 1px rule, 10px radius, a small heading inside) marks a unit of attention: Waiting on you, Books against their backtests, the Frozen specification, the verdict on a result. Rows inside a panel are separated by rules, never boxed. A screen has two or three panels at most; everything else sits on the ground.
- **A strategy's page is the backtester.** Left: the frozen specification, read-only, sticky on desktop and folded to one line on the phone. Right: tabs for the stages the strategy has reached (Evidence, Runs, Result, Holdout, Paper, Decisions). The Run button sits top right, like the backtester's.
- **State always on screen, in the engine's terms.** Under each strategy's title: the five-stage stepper, then one mono line with the family's N, SR* and holdout spends, each with its ⓘ.
- **The workstation view itself** (tiled panels, transport, scrubber; `#strategies/h1-momentum-12-1/bench`) is "how it was computed": behind a disclosure on the Result tab, never the first screen.

### Today: proposal, the owner can overturn it
Today is the same look made calmer for a one-minute check on the phone: **no panel at all when the machine is healthy** (one sentence, "Nothing wrong.", and a folded "Each job" list), a panel only for what is waiting and for the books, a single red-outlined kill switch button at the end. When something is wrong the health line becomes a panel with an attention border listing only the jobs that are not ok. On desktop the same blocks sit in two columns. The alternative is E's airier Robinhood frame (big number, big chart); it was dropped because a portfolio total and chart answer "how am I doing", which the owner's morning check does not ask; it asks "anything wrong, anything waiting, is each book tracking its backtest".

## Rules
- Colour only for gain, loss and attention, always paired with a mark (✓ ✕ –), a sign or a word.
- Say it once: no badge, icon or subtitle repeating its text; codes (B10, H1) once, at the foot of a detail page; thresholds shown once per screen (the spec panel hides them while the Result tab leads with them).
- Graphics explain themselves: pass marks on tiles; the tracking scale (shaded tolerance zone, a marker, zero drawn); countable steps for forward holdouts and holdout spends; the variants against `promote_at_least` with the floor labelled on the track.
- Terms are the engine's and the literature's (DSR, SR*, N, holdout, params_sha256), taught by an ⓘ popover (`components/Term.tsx`, the glossary of interaction.md §9) at the point of decision. Never renamed.
- Every prototype-only write carries the same footer (`PrototypeWrite`): the command it would run, and that it waits on the ADR 0018 amendment.
- Phone: navigation in a bottom tab bar, nothing scrolls sideways (the Runs table scrolls inside its own box), dialogs become full-width sheets with the primary button first.
