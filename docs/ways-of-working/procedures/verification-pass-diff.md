# Procedure: the verification-pass diff

What a reviewer reads on its verification pass ([agents.md, Review passes](../agents.md#review-passes) rule 2): everything pushed to the PR's own files since pass 1, with `main`'s hunks told apart from the PR's. Commits a merge of `main` brought in were reviewed on their own PRs and are skipped.

1. **Fetch first.** `git fetch origin`. Stale, `origin/main` puts files only the merge touched in the second list below.
2. **Scope the diff to the PR's own files**, as they stood at pass 1 and as they stand now:

   ```bash
   { git diff --name-only -z origin/main...<pass-1 head>; git diff --name-only -z origin/main...HEAD; } \
     | sort -zu | xargs -0 -r git diff <pass-1 head>..HEAD --
   ```

   NUL-separated, so a path with a space stays one path; `-r` runs nothing when the list is empty. A file reset to `main`'s version after pass 1 is in the first list; a file added after pass 1 in the second.
3. **Tell `main`'s hunks from the PR's.** That diff shows how a conflict in those files was resolved and, where `main` changed one of them since pass 1, `main`'s hunks next to the resolution; those hunks are not the PR's changes.
   - `git log -p --first-parent --no-merges <pass-1 head>..HEAD -- <those files>` shows only the PR's own hunks (merge commits left out).
   - `git show --remerge-diff <sha>`, for each merge that `git log --first-parent --merges --format=%H <pass-1 head>..HEAD` lists, shows only what the resolution changed against git's automatic merge, a resolution that takes one side whole included.
   - A hunk in neither came from `main` and is skipped, except in a file on the order path or one that handles a secret, where every hunk is read.
4. **Confirm each pass-1 fix in the file as it stands now**, not only in the diff, so a fix lost to a resolution is caught.

`spec-critic` has no Bash: the window's brief names the PR's own commits since pass 1 (`git log --first-parent --no-merges <pass-1 head>..HEAD`) and any file whose merge conflict it resolved.
