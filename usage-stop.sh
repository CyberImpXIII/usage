#!/usr/bin/env bash
# Stop / SubagentStop hook: appends the turn's token delta per agent to
# ~/.claude/usage/tokens.tsv (usagelib/reporters.py, stop). Fails open: always
# exits 0 and prints nothing, whatever the input or the store's state; a row it
# could not write is recorded in failures.tsv (usagelib/hook.py; `usage failures`).
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd) || exit 0
python3 "$here/usagelib/hook.py" stop >/dev/null 2>&1
exit 0
