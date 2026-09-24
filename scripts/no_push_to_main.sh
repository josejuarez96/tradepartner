#!/usr/bin/env bash
# pre-push guard: refuse any push whose remote ref is main.
# pre-commit passes the pushed remote ref in PRE_COMMIT_REMOTE_BRANCH.
if [[ "${PRE_COMMIT_REMOTE_BRANCH:-}" == "refs/heads/main" ]]; then
  echo "✋ Direct pushes to main are not allowed. Push a branch and open a PR." >&2
  exit 1
fi
