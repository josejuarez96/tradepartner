# TradePartner

A personal, local trading research system: evidence-graded strategy research, point-in-time data, honest backtests, paper trading, and possibly an LLM analyst layer.

> Personal learning project. Not financial, tax, or legal advice.

## Status
See [docs/STATUS.md](docs/STATUS.md) for the current phase, what's in progress and what's next.

## Quick start
```bash
brew install uv            # once
uv sync                    # creates .venv, installs deps
uv run pre-commit install  # git hooks: block commit/push to main, secret scan, lint
uv run pytest
```

## Where things live
| Path | What |
|---|---|
| [docs/](docs/README.md) | All documentation; start at the docs map |
| [docs/ways-of-working/](docs/ways-of-working/) | How we build: git, process, agents |
| [CLAUDE.md](CLAUDE.md) | Operating instructions for coding agents |
| `src/tradepartner/` | Application code |
| `tests/` | Tests |
| `data/` | Local data (gitignored) |

## How we work
Every change goes through issue → branch → PR → owner merge. See [git workflow](docs/ways-of-working/git-workflow.md), [development process](docs/ways-of-working/development-process.md) and [agents](docs/ways-of-working/agents.md).
