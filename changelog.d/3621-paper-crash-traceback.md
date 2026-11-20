- Paper commands print the scrubbed traceback of a crash to stderr after the failure line, so paper.err.log holds the frames (#3621).
### Fixed
- paper run and every `_paper_call` command (reconcile, start, stop, kill, resume, settle, true-up, ...): a crash now prints its scrubbed traceback to stderr after the unchanged failure line; refusals print none; exit codes and stdout unchanged (#3621).
