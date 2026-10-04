- merge_train.py #694: UNCONFIRMED comment on an unverified landing, accurate reason after a failed MERGED post, draft/merge-queue refusals stop at once, prune deletes refs/merge-train/* when no build is in flight.
### Fixed
- merge_train.py: a PR that landed but failed the tree or parent check gets an `UNCONFIRMED` comment; a failed `MERGED` post no longer reports the landing as unverified; draft and merge-queue refusals stop at once; `prune` deletes the local `refs/merge-train/*` refs (#694).
