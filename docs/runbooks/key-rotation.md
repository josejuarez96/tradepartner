# Runbook: rotating the Alpaca paper keys

Owner-run. Use it when a key may have leaked, when Alpaca issues new keys (for example after a paper-account reset), or on a routine schedule. It covers the paper-trading keys per book. No key value ever goes in this repo, a plist, a command line, a chat or an issue: the keys live only in `.env` in the main checkout (gitignored). You and agents never paste them; agents never read `.env`.

Code references: `config.py` (`AlpacaPaperKeyPair`, the per-book variables), `adapters/alpaca_trading_raw.py` (`no_credentials`), `execution/run.py` (the `account_id` check at the start of a run).

## Where the keys live

All in `.env` (names in `.env.example`):

| Book | Variables |
|---|---|
| `main` | `ALPACA_PAPER_API_KEY`, `ALPACA_PAPER_API_SECRET` |
| any other book, for example `daily` | `ALPACA_PAPER_BOOKS__DAILY__API_KEY`, `ALPACA_PAPER_BOOKS__DAILY__API_SECRET` (the token upper-cased) |
| market data (ingest, not orders) | `ALPACA_API_KEY`, `ALPACA_API_SECRET` |
| sandbox probes (never a book) | `ALPACA_SANDBOX_API_KEY`, `ALPACA_SANDBOX_API_SECRET` |

Rules the code enforces: each book has its own pair, from its own Alpaca paper account. A pair that repeats the data keys, `main`'s pair or another book's is refused, and a book with no pair cannot start (`no_credentials`). A book never falls back to `main`'s pair. `ALPACA_PAPER_BOOKS__MAIN__*` is never read. The jobs read `.env` on every run, so no job needs reloading after an edit.

## Rotate one book

Rotate one book at a time, so a mistake stops one book.

1. **Pick a quiet time**: not within the scheduled run window (the paper plist's time, scheduling.md) and not while `paper stop`, `resume` or `reconcile` is running.
2. **Check the book is calm.** `uv run tradepartner paper status --book <token>`. If the kill switch is engaged or a run is in progress, deal with that first.
3. **Make the new key at Alpaca.** In the Alpaca paper dashboard, open that book's own paper account and regenerate its API key. Regenerating a key should keep the account and its `account_id` (not verified in this repo; the reconcile step below proves it). Do not make a new account: the open window is tied to the account id, and a run on a different account halts with `account_id: the broker's account ... is not window ...'s` (`execution/run.py`).
4. **Edit `.env` by hand** in an editor: replace both the key and the secret for that book only. Keep the names exactly as in the table above. Save. Never use `echo` or `export` for it, so the value stays out of shell history.
5. **Check no stale override exists.** Process environment beats `.env` ([scheduling.md](scheduling.md), "PATH, working directory and `.env`"). For the book's two variable names, this prints nothing when none is set:
   ```bash
   [ -n "$(launchctl getenv ALPACA_PAPER_API_KEY)" ] && echo "override set: remove it"
   ```
   Run it for each variable you changed (it prints only a warning, never the value). Also check the job plist's `EnvironmentVariables` for the same names (`grep -c ALPACA ~/Library/LaunchAgents/com.tradepartner.*.plist` prints a count per file, never a value; it should be 0, since no secret belongs in a plist). Also check your shell has none exported (`env | grep -c ALPACA_PAPER` prints a count, not values).
6. **Prove the new keys work.** With a window open for the book:
   ```bash
   uv run tradepartner paper reconcile --book <token>
   ```
   It reads the broker with that book's keys and reconciles. `ok` means the keys and the account id match. Warning: a mismatch is a real finding, and it engages the kill switch for the book (resume procedure in scheduling.md), so run it when you can handle that. A `no_credentials` refusal means a half-set or mistyped pair; an auth error from Alpaca means the old key is still in use or the new one was copied wrong. `paper status` never contacts the broker, so it proves nothing about keys. With no open window, the first call that reads the broker is `paper start` (it calls `account()`), or the next scheduled `paper run`: check it per "Checking a run".
7. **Revoke the old key** at Alpaca only after step 6 passes.
8. **Note it.** Record the date, the book and the reason in one line on the issue or in your notes. Never the key.

## If a key leaked

Revoke it at Alpaca first, then rotate as above. The market-data key (`ALPACA_API_KEY` / `ALPACA_API_SECRET`) can belong to a live Alpaca account, because it is a data key, not a paper key. If it leaked, rotate it at Alpaca the same way (edit `.env`, check for overrides, then run `uv run tradepartner ingest` or wait for the next ingest), and if the key is from a live account, revoke it at once, before anything else: it may reach real money. Then check the broker's activity for orders you did not place, and `paper status --book <token>` for unexpected orders. Check that the key was not printed in a log: the code scrubs keys from its output, but a pasted command line or a plist is not scrubbed. The paper endpoint is paper-only by a guarded setting no variable can change (paper-trading spec req 2), so a leaked paper key cannot touch real money (the data key above is the one that might).

## After resetting a paper account

An Alpaca paper-account reset can issue new keys and a new account id. Follow [window-restart](window-restart.md), step 5, and the `abandon` section of scheduling.md. The new window records the new account id. A start is refused only if that account belongs to another book's window (`account_in_use`).

## Other secrets

The alert SMTP login and the two heartbeat URLs (`ALERT_*`) are also `.env`-only. Rotate them the same way (edit, check no override, send a test) from [scheduling.md](scheduling.md), "A halt, off the Mac". `ANTHROPIC_API_KEY`, the SEC user agent and the FRED key are outside this runbook.
