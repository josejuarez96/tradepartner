# AI design tells to avoid

**Status:** reference for the end-user app (`web/`, spike `spike/design-ui`) · **Date:** 2026-10-10 · **Why:** four prototype directions in a row read as "AI-made" to the owner. This page lists what gives a UI away, maps every past version onto it, and audits the current build.

**Main source:** the `avoid-ai-design` catalog (67 tells, each sourced; MIT): <https://github.com/funboy322/avoid-ai-design>, file `references/ai-tells-catalog.md`. It draws on Adrian Krebs's study of ~1,400 Show HN sites (<https://adriankrebs.ch/blog/design-slop/>), Anthropic's `frontend-design` skill (<https://github.com/anthropics/skills/tree/main/skills/frontend-design>), and a ranking mined from ~3M Reddit posts about AI-built sites (<https://github.com/JCarterJohnson/vibecoded-design-tells>). Most sources are blogs and opinion; treat them as heuristics, not measurements. **A tell is a default nobody chose, not proof a model made the page. Judge combinations, not single hits.**

## The one idea behind all of it

Models return the median of their training data. Avoiding the median is not enough, because the *escapes* converged too. So there are two layers:

- **First-order defaults** (the median): purple/indigo gradients, Inter, a centred hero over three icon cards, **shadcn/Tailwind left as installed**.
- **Second-order defaults** (the "tasteful" escapes, named in Anthropic's own skill): (1) warm cream + a serif + a terracotta/rust accent, the Claude look; (2) near-black + one acid-green signal, usually with monospace; (3) broadsheet hairlines and zero radius on something that isn't a publication; (4) the SaaS card kit: identical rounded cards, one radius, one soft shadow; (5) template chrome: tracked ALL-CAPS eyebrows, `A · B · C` meta strings, mono for small labels, `→` on every link.

The test for any choice: **would I have made it for any similar app? If yes, it's a default.** The fix is to derive the look from the product's own world (its artifacts, materials, vernacular), write it down once, and build every page from it.

## Our four versions, mapped

| Version | Tells it hit | Why it read as AI |
|---|---|---|
| v1 (light, warm paper, Inter) | T1 Inter everywhere · K2 bordered rounded cards · C7-ish soft palette · L6 one centred column | First-order median |
| v2 (dark terminal) | **SD2** near-black + neon green · T5/SD4 uppercase tracked labels · K12 status dots and pill tags · K6 logo glyph in a rounded square · T1 Inter | The second-order "technical" escape |
| v3 (ledger) | **SD1** warm cream/bone + serif + rust · **SD5/T3 one italic coloured phrase in the headline** · **SD3** broadsheet hairlines, near-zero radius · **SD4** mono for every label, `·` meta strings, `→` on links · T2 Geist + Newsreader | Almost every second-order cluster at once; the Claude look |
| v4 (shadcn, current) | **K1** untouched shadcn theme (P0) · **K8** default radius · **T2/T4** Geist untouched · **SD8** emerald as the accent · **K2/SD4-kit** four identical summary cards, grey border on every card · **L4** "big number, small label, supporting stats" row · **K6/I3** stock Lucide icons in tinted rounded squares · **K12** a badge on every row · SD4d `·` meta strings · subtitle under every heading | The first-order median again: shadcn as installed |

## Checklist for TradePartner

Ranked by who notices (P0 anyone, P1 a designer/developer, P2 polish). IDs match the catalog.

### Never ship (P0)
- [ ] **K1** shadcn/Tailwind theme as installed: stock neutral palette, stock `--primary`, stock radius, unstyled Card/Button/Badge. Theme it from the product first.
- [ ] **T1** Inter or the system stack as the only face.
- [ ] **C1/C6** indigo-to-violet gradients, gradient text.
- [ ] **L1/L2** centred hero with a pill badge; rows of identical icon-topped cards.
- [ ] **X1** a second screen that re-picks fonts, colours or radius instead of using the tokens.

### Avoid unless the subject earns it (P1)
- [ ] **SD1** cream/parchment ground + serif + rust/terracotta accent.
- [ ] **SD2** near-black + one neon (acid-green, lime, vermilion) accent, often with mono.
- [ ] **SD4 / T5** template chrome: ALL-CAPS tracked eyebrows; `A · B · C` meta strings; mono for labels that aren't scanned data; `→` glued to links.
- [ ] **SD5 / T3** one word or phrase in the headline set apart by colour or italic.
- [ ] **SD6** decorative 01 / 02 / 03 on things that aren't a sequence.
- [ ] **T2 / T4** the "tasteful free font" set as the only gesture: Geist, Space Grotesk, Instrument Serif, Fraunces, Syne, Sora, Cal Sans.
- [ ] **K2** the same radius, padding, border and soft shadow on every surface; a 1px grey box around every card.
- [ ] **K6 / I3** Lucide icons in tinted rounded squares; the worn set (Sparkles, Zap, Rocket, TrendingUp-as-logo, CheckCircle).
- [ ] **K4** coloured left-border accent cards.
- [ ] **L4** a stat strip of round numbers; "big number + small label + supporting stats" as the default hero.
- [ ] **C7** dark mode nobody asked for. (For us, dark is earned only if we decide the owner uses it at night; then theme it as a palette, not one neon on black.)
- [ ] **C9** muted text under 4.5:1.
- [ ] **K7** missing hover, focus, active, disabled, loading and error states.
- [ ] **M6** motion that ignores reduced-motion.
- [ ] **CP1 / CP3** vague aspirational copy; arrows stapled to labels.

### Polish (P2)
- [ ] **SD8** emerald/teal as the "not purple" accent when nothing asked for green. (We need a gain colour; choose its exact value from the subject, not Tailwind's emerald.)
- [ ] **K11 / K12** cards inside cards; a chip on every row with no real state behind it.
- [ ] **S1** the same gap and padding everywhere; no rhythm.
- [ ] **M1 / M4 / M5** fade-up on every section, bounce easing, count-up numbers.
- [ ] **CP5 / CP6** Title Case everywhere; "Get started", "Learn more", "Submit".
- [ ] A subtitle under every heading ("Only you can do these. Each one unblocks something."): Anthropic's skill counts unnecessary labels above or below content among the commonest tells.

### Owner's rules (from review, 2026-10-10)
- [ ] **Say it once.** No label repeated in the same block: not the title, a code badge, the first words of the description and a "Next step" line all naming the same thing. Internal codes (B10, H1) appear once, as a reference, never as a badge beside the name. If two lines mean the same, cut one. This goes for "Ahead" next to a "+2.3 pts" badge, an icon next to the word it illustrates, and a subtitle that rewords its heading.
- [ ] **No coloured bars to decode.** Segmented or multi-colour horizontal bars (evidence splits, progress strips) need a legend to read. Say it in words with the numbers beside it: "Mostly supported (5 studies: 3 for, 2 mixed)", "73% could be luck", "Exam 48 of 63 trading days".

### Don't over-correct
- Restraint done on purpose is not "timid". Minimal is fine when it's a decision.
- Glass, bento grids and mesh backgrounds are over-blamed; the tells people actually recognise are shadcn/Tailwind defaults and the indigo gradient.
- A third order is forming: hand-drawn type, grain, scrapbook "anti-AI" looks. They're the next reflex when reached for just to look human.

## Audit of the current build (v4)

Scanner (`avoid-ai-design detect v0.4.0`, code-certain tells only), run on `web/src` and `web/index.html`:

| Finding | Where |
|---|---|
| P0 K1 untouched shadcn base theme | `src/index.css` |
| P1 K8 default `--radius` (0.625rem) | `src/index.css` |
| P1 T2 Geist (tasteful-free-font cluster) | `src/components/ReturnChart.tsx`, `src/index.css` |
| P2 SD8 emerald as the accent | `src/index.css` (gain colour) |
| P2 SD4d `A · B · C` meta strings | `src/screens/Research.tsx` |

Render review (needs eyes, from the screenshots in `screens/`):

| Tell | Where on screen |
|---|---|
| K2 / SD4 SaaS card kit | Overview: four identical summary cards; every section boxed with the same border and radius |
| L4 big-number stat row | Overview summary cards |
| K6 / I3 stock Lucide in tinted squares | Research "Waiting on you" icons; the TrendingUp logo |
| K12 chip on every row | Books table "Running" badges; B-code badges on every idea |
| Subtitle under every heading | Research card descriptions; "3 running" under Books |
| K11 cards in cards | Research idea cards inside the tab panel; Stat boxes inside the side panel |

Conclusion: v4 still sits on the first-order median. **A re-skin won't fix it**; the catalog's own finding is that most of the sameness lives in structure, not colour.

## How we avoid it from here

1. **Ground the direction in TradePartner's own world before writing code.** Candidate source material from the repo itself: the trial registry, the pre-registered hypothesis file (prior, stop rule, retire condition), the exam (holdout spent once), the brokerage statement and trade confirm, the plan's task lines. Pick one structural idea from that, not a "look".
2. **Write it down once** in a `DESIGN.md` (Google's open format, <https://github.com/google-labs-code/design.md>): 4 to 6 colours with roles, the faces, the layout idea, one signature detail. Every screen is built from it (prevents X1 drift).
3. **Review the plan against this checklist before building.** For each choice, ask "would I have made this for any similar app?" The owner confirms the plan.
4. **Re-scan every change.** The scanner exits 2 on any P0/P1, so it can gate the prototype (`node detect.mjs web/src`); the render-only tells get a screenshot check.
