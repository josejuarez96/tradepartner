# TradePartner: Agent Instructions

Personal, local US-equity trading research system: point-in-time data, honest backtests, paper trading, then a small live account. Owner: Jose (solo). Agents build; the owner reviews, and his word lands every class-B PR.

**Start every session:** read [docs/STATUS.md](docs/STATUS.md), then run `uv run python scripts/fragments.py show` (unfolded done entries) and `uv run python scripts/team.py status` (claims, ready work).

## Non-negotiables
1. **Never commit to or push `main`.** Branch `<type>/<issue#>-<slug>` from the latest main and open a PR. Never force-push a shared branch or use `--no-verify`. Merges follow [git-workflow rule 7](docs/ways-of-working/git-workflow.md#the-rules-in-one-screen), the standing approval (never ask for it in chat): the orchestrator lands class A; class B and `hold` PRs merge only on the owner's word for that PR; a [merge train](docs/ways-of-working/git-workflow.md#the-merge-train) only on his ask. Team windows and subagents never merge.
2. **No code without an approved plan task**, unless the issue is size S or the branch is `spike/` ([development-process](docs/ways-of-working/development-process.md)).
3. **Stay in scope.** Anything else goes in a new issue (`gh issue create`), not this PR.
4. **Secrets live only in `.env`** (gitignored; you may not read it). New variables go in `.env.example`. Never log secrets or send them to an LLM.
5. **Code computes numbers.** LLM output never reaches order placement without passing deterministic risk rules.
6. **Point-in-time data.** Every stored fact has a tz-aware UTC `known_at`. No look-ahead. Include delisted securities.
7. **Every backtest run is logged** in the trial registry. Never touch the holdout without an explicit flag.
8. **When unsure, stop and ask.** Write open questions in the PR or spec; don't guess requirements.
9. **Claim before you build:** `uv run python scripts/team.py claim <Tn|issue#>` before any branch (`spike/` is exempt). GitHub holds the claim; STATUS is a snapshot. "Held by another team" means pick something else. New session: `team.py start <name>` once, then work only in the directory it prints; never enter another team's directory or the main checkout, even to look. Never `release --force` or `--owner-task` unless Jose says so. See the [command card](docs/ways-of-working/teams.md#command-card).

## Commands
```bash
uv sync; uv run pytest <files>; uv run mypy   # env, tests, types (strict, src/)
uv run ruff check . && uv run ruff format .   # lint + format
uv run pre-commit run --all-files             # all hooks
uv add [--dev] <pkg>                          # only if the plan lists it
```
Before every push: lint, format, mypy and the test files you touched. CI runs the full suite on code, test, script, CI and dependency changes, and on every push to main.

## Code standards
- Python 3.12, `src/tradepartner/` layout, type hints everywhere (mypy strict), docstrings on public functions.
- Datetimes are tz-aware UTC. Use a trading calendar, never "weekdays".
- Thresholds and limits come from config, never hardcoded. Signals and metrics are pure functions; I/O stays at the edges.
- Tests: pytest. A bug fix starts with a failing test. Data code gets a "no look-ahead" test.
- Conventional Commits (`feat(data): …`) with a `Co-Authored-By` trailer.

## Before readying a PR
1. Fill in the PR template completely.
2. Run, in pass 1, every reviewer the plan line's `Review:` names and `scripts/ready_pr.py` requires for your paths (`QUANT_PREFIXES`, `SAFETY_PREFIXES` are the source; roughly: `quant-auditor` for data, backtests, signals; `safety-reviewer` for broker, orders, secrets, LLM inputs). [Review passes](docs/ways-of-working/agents.md#review-passes).
3. Put your STATUS line and CHANGELOG bullet in one fragment (`fragments.py add`, [command card](docs/ways-of-working/teams.md#command-card)); never edit `STATUS.md` or `CHANGELOG.md`.
4. Run `/ready-pr` (`uv run python scripts/ready_pr.py <pr> --timeout-min 45`). It merges main in, runs the checks, waits for CI, marks ready. Never mark ready by hand.

## Where things are
[Charter](docs/charter.md) (purpose, scope, constraints) · [roadmap](docs/roadmap.md) (phases, exit criteria) · [decisions](docs/decisions/) · `docs/specs/`, `docs/plans/` (what to build, how) · [research](docs/research/) (evidence grades) · what to test next: [hypothesis-backlog](docs/research/hypothesis-backlog.md) and [claims.toml](docs/research/claims.toml), not the long reports, and the [research loop](docs/ways-of-working/research-program.md) · [agents](docs/ways-of-working/agents.md) · [teams](docs/ways-of-working/teams.md) (working beside other windows) · [doc types and templates](docs/README.md).
