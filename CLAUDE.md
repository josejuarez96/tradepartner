# TradePartner: Agent Instructions

Personal, local US-equity trading research system: point-in-time data, honest backtests, paper trading, then a small live account. Owner: Jose (solo). Agents build; the owner reviews, and his word lands every class-B PR (git-workflow rule 7).

**Start every session by reading [docs/STATUS.md](docs/STATUS.md)** and `uv run python scripts/fragments.py show` (recently done entries not folded in yet), then run `uv run python scripts/team.py status`. STATUS tells you the phase; the board tells you who holds what and what is ready.

## Non-negotiables
1. **Never commit to or push `main`.** Branch `<type>/<issue#>-<slug>` from the latest main and open a PR. Never force-push shared branches or use `--no-verify`. Merges follow the two classes of [git-workflow rule 7](docs/ways-of-working/git-workflow.md), which is the standing approval (written 2026-10-04; never ask for it in chat): **class A**, a PR whose files are all under `src/`, `tests/`, `docs/research|retros|runbooks` or `changelog.d/`, that is ready, green on its head and `PASS` on every required verdict, is landed by the orchestrator session after a trial merge against current `main`, by hand (`gh pr merge --squash --match-head-commit`, never `--admin`) until the train is the class-A path; **class B**, every other PR (`docs/specs|plans|decisions|ways-of-working`, `.claude/`, `.github/`, `CLAUDE.md`, `scripts/{ready_pr,merge_train,team}.py`, `pyproject.toml`, `uv.lock`, anything else) and any PR labelled `hold`, is merged only when the owner says to merge that specific PR. Build a merge train only when the owner asks, and run `merge_train.py merge <batch id>` only when he says "merge train `<batch id>`" to you, for that id. Team windows and subagents never merge.
2. **No code without an approved plan task** unless the issue is size S, or the branch is `spike/`. See [development-process.md](docs/ways-of-working/development-process.md).
3. **Stay in scope.** Do the task you were given. Put anything else in a new issue (`gh issue create`), not in this PR.
4. **Secrets live only in `.env`** (gitignored, and you may not read it). Add new variables to `.env.example`. Never log secrets or send them to an LLM.
5. **Code computes numbers. LLM output never reaches order placement** without passing deterministic risk rules.
6. **Point-in-time data.** Every stored fact has a `known_at` (UTC, tz-aware) timestamp. No look-ahead. Include delisted securities.
7. **Every backtest run is logged** in the trial registry. Never touch the holdout without an explicit flag.
8. **When unsure, stop and ask.** Write open questions in the PR or spec. Don't guess at requirements.
9. **Claim before you build.** `uv run python scripts/team.py claim <Tn|issue#>` before any branch. GitHub holds the claim; STATUS.md is a snapshot. "Held by another team" means pick something else. New session: `uv run python scripts/team.py start <name>` once, then work only inside the directory it prints. Never enter another team's directory or the main checkout, not even to look. `spike/` branches are exempt from claims. Never `release --force` or `--owner-task` unless Jose says so. See the [command card](docs/ways-of-working/teams.md#command-card) in teams.md.

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
Run lint, format and mypy before every push, and the targeted pytest `ready_pr` picks (`--full-tests` for all) when the change touches `src/`, `tests/`, `scripts/`, `.github/`, `pyproject.toml`, `uv.lock` or `.python-version`. CI runs the full suite on such PRs and on every push to main.

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
| What to test next, and what the research says | [hypothesis-backlog.md](docs/research/hypothesis-backlog.md), [claims.toml](docs/research/claims.toml) (read these, not the long reports); the loop: [research-program.md](docs/ways-of-working/research-program.md) |
| Build agents and when to use them | [docs/ways-of-working/agents.md](docs/ways-of-working/agents.md) |
| Working alongside other chat windows | [docs/ways-of-working/teams.md](docs/ways-of-working/teams.md) |
| Doc types and templates | [docs/README.md](docs/README.md) |

## Before opening or readying a PR
Fill in the PR template completely. Run `quant-auditor` if the PR touches data, backtests or signals. Run `safety-reviewer` if it touches the broker, orders, secrets or LLM inputs. Record your STATUS line and CHANGELOG bullets in one fragment (`uv run python scripts/fragments.py add <issue> --slug <slug> --status "..." --added "..."`), never by editing `STATUS.md` or `CHANGELOG.md`. Then `/ready-pr` (`uv run python scripts/ready_pr.py <pr>`): it merges main in, runs every check, waits for CI and marks the PR ready. Never mark ready by hand; never merge outside git-workflow rule 7 (a team window never merges).
