#!/usr/bin/env bash
# StopFailure hook: on `error: rate_limit` only, appends one row to
# ~/.claude/usage/hits.tsv with the last status-line sample before it
# (usagelib/reporters.py, failure). Any other error writes nothing. Fails open:
# always exits 0 and prints nothing; a row it could not write is recorded in
# failures.tsv (usagelib/hook.py; `usage failures`).
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd) || exit 0
python3 "$here/usagelib/hook.py" failure >/dev/null 2>&1
exit 0
