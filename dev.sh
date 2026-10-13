#!/usr/bin/env bash
# The pre-commit command of tools/usage. `./dev.sh check` runs every part of
# check.parts.json (a part per test module, then devtools/check.py's gates:
# files, hooks, guard, shared) through the shared runner devtools/parts.py
# (PLAN-small-tasks §3, §7; setup's `parts` component renders it from
# tools/checks): each part has its own timeout, each result is written to
# .progress/check.json as it ends, a failing run names its parts, and
# `--resume` re-runs only the parts whose files changed since they passed.
# `--json` prints the one schema, one check per part. CHECK_PARTS=<file> runs
# that file's parts instead (tools/checks' check-partial hands it a suite of
# its own). tests/test_parts.py holds check.parts.json to the test modules and
# the gates, and usage() below to the case at the bottom.
set -uo pipefail
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

usage() {
  cat <<'EOF'
./dev.sh <command>

  check [--json] [--resume | --only <part>...]   every part of check.parts.json, each with its own timeout; ends "check: N of M passed; failed: ..."; non-zero if any fails; run before committing (--resume: only the parts whose files changed since they passed; CHECK_PARTS=<file>: that file's parts instead)
  test [<part>...]   the unit tests, all at once (python3 -m unittest); with parts: just those parts of check.parts.json (check --only <part>...), recorded in the progress file so a later check --resume skips them while their files are unchanged
EOF
}

cmd_check() { exec python3 "$here/devtools/parts.py" run "${CHECK_PARTS:-$here/check.parts.json}" "$@"; }
cmd_test() {
  if [ "$#" -gt 0 ]; then
    local only=() p
    for p in "$@"; do only+=(--only "$p"); done
    cmd_check "${only[@]}"
  fi
  cd "$here" && exec python3 -m unittest discover -s tests -t .
}

case "${1:-}" in
  check) shift; cmd_check "$@" ;;
  test) shift; cmd_test "$@" ;;
  ""|-h|--help|help) usage ;;
  *) echo "unknown command: $1"; usage; exit 2 ;;
esac
