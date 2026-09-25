# TradePartner: Agent Instructions

Personal, local US-equity trading research system: point-in-time data, honest backtests, paper trading, then a small live account. Owner: Jose (solo). Agents build; the owner reviews and approves merges.

**Start every session by reading [docs/STATUS.md](docs/STATUS.md)**, then run `uv run python scripts/team.py status`. STATUS tells you the phase; the board tells you who holds what and what is ready.

## Non-negotiables
1. **Never commit to or push `main`.** Branch `<type>/<issue#>-<slug>` from the latest main and open a PR. Never force-push shared branches or use `--no-verify`. Merge a PR only when the owner explicitly tells you to merge that specific PR (squash, CI green); otherwise never merge. Subagents never merge. See [git-workflow.md](docs/ways-of-working/git-workflow.md).
2. **No code without an approved plan task** unless the issue is size S, or the branch is `spike/`. See [development-process.md](docs/ways-of-working/development-process.md).
3. **Stay in scope.** Do the task you were given. Put anything else in a new issue (`gh issue create`), not in this PR.
4. **Secrets live only in `.env`** (gitignored, and you may not read it). Add new variables to `.env.example`. Never log secrets or send them to an LLM.
5. **Code computes numbers. LLM output never reaches order placement** without passing deterministic risk rules.
6. **Point-in-time data.** Every stored fact has a `known_at` (UTC, tz-aware) timestamp. No look-ahead. Include delisted securities.
7. **Every backtest run is logged** in the trial registry. Never touch the holdout without an explicit flag.
8. **When unsure, stop and ask.** Write open questions in the PR or spec. Don't guess at requirements.
9. **Claim before you build.** `uv run python scripts/team.py claim <Tn|issue#>` before any branch. GitHub holds the claim; STATUS.md is a snapshot. "Held by another team" means pick something else. New session: `uv run python scripts/team.py start <name>` once, then work only inside the directory it prints. Never enter another team's directory or the main checkout, not even to look. `spike/` branches are exempt from claims. Never `release --force` or `--owner-task` unless Jose says so. See [teams.md](docs/ways-of-working/teams.md).

## Commands
```bash
uv sync                                   # install / update env
uv run pytest                             # tests
uv run ruff check . && uv run ruff format .  # lint + format
uv run mypy                               # types (strict, src/)
uv run pre-commit run --all-files         # all hooks
uv add <pkg> / uv add --dev <pkg>         # deps (only if the plan lists them)
uv run python scripts/team.py start <name>  # new session: own directory + register, once
uv run python scripts/team.py status      # who holds what, ready frontier
uv run python scripts/team.py claim T5    # or an issue number; release to give back
```
Run lint, format, mypy and pytest before every push.

## Code standards
- Python 3.12, `src/tradepartner/` layout, type hints everywhere (mypy strict), docstrings on public functions.
- Datetimes are always timezone-aware UTC. Use a trading calendar, never "weekdays".
- Thresholds and limits come from config, never hardcoded. Pure functions for signals and metrics, with I/O at the edges.
- Tests: pytest. A bug fix starts with a failing test. Data code gets a "no look-ahead" test.
- Conventional Commits (`feat(data): …`) with a `Co-Authored-By` trailer.

## Where things are
| Need | Go to |
|---|---|
| Why the project exists, scope, constraints | [docs/charter.md](docs/charter.md) |
| Phases and exit criteria | [docs/roadmap.md](docs/roadmap.md) |
| Decisions already made | [docs/decisions/](docs/decisions/) |
| What to build / how | `docs/specs/`, `docs/plans/` |
| Research and evidence grades | [docs/research/](docs/research/) |
| Build agents and when to use them | [docs/ways-of-working/agents.md](docs/ways-of-working/agents.md) |
| Working alongside other chat windows | [docs/ways-of-working/teams.md](docs/ways-of-working/teams.md) |
| Doc types and templates | [docs/README.md](docs/README.md) |

## Before opening or readying a PR
Fill in the PR template completely. Run `quant-auditor` if the PR touches data, backtests or signals. Run `safety-reviewer` if it touches the broker, orders, secrets or LLM inputs. Record your Done line and CHANGELOG bullet as fragments (`uv run python scripts/fragments.py add <issue> ...`), never by editing `STATUS.md` or `CHANGELOG.md`. Then `/ready-pr` (`uv run python scripts/ready_pr.py <pr>`): it merges main in, runs every check, waits for CI and marks the PR ready. Never mark ready by hand, never merge.
