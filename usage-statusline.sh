#!/usr/bin/env bash
# The status line: records the plan's rate-limit windows (limits.json, and a row
# in samples.tsv when they change or the heartbeat is due) and prints one line,
# "5h 42% (resets 14:05) | 7d 18% (resets Mon 09:00)", nothing when the input
# carries no rate_limits.
#
#   usage-statusline.sh [command]   command: an existing status line, run with the
#                                   same stdin; its output is printed unchanged and
#                                   ours is appended to its last line after " | "
#
# Fails open: exit 0 always; if our part fails, the chained output still prints.
# A sample it could not write is recorded in failures.tsv and the line ends
# " | usage: not recorded" (usagelib/reporters.py; `usage failures`).
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)
input=$(cat 2>/dev/null)
chained=""
if [ $# -gt 0 ]; then
  chained=$(printf '%s' "$input" | sh -c "$*" 2>/dev/null)
fi
ours=""
if [ -n "$here" ]; then
  ours=$(printf '%s' "$input" | python3 "$here/usagelib/hook.py" statusline 2>/dev/null) || ours=""
fi
if [ -n "$chained" ] && [ -n "$ours" ]; then
  printf '%s | %s\n' "$chained" "$ours"
elif [ -n "$chained" ]; then
  printf '%s\n' "$chained"
elif [ -n "$ours" ]; then
  printf '%s\n' "$ours"
fi
exit 0
