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

# The check's pass record (remediation 2.3, 2026-10-30; the same steps as
# tools/setup's dev.sh cmd_check). A green run of this repo's whole suite
# records the tree it ran on for the git pre-commit hook (.githooks/check-pass,
# setup's githooks component), on stderr so --json stays one document. Never
# recorded: --only, CHECK_PARTS, a tree that changed meanwhile (check-pass
# refuses), or a run in which a part said UNCHECKED (green by exit code, never a
# pass). Not recording never turns a green check red: the commit is what is
# refused.
#   pass_tree <check args>           the tree to record, taken before the first part
#   pass_record <code> <tree> <check args>   records it when code is 0; returns code
pass_tree() {
  local a top
  [ -n "${CHECK_PARTS:-}" ] && return 0
  for a in "$@"; do [ "$a" = --only ] && return 0; done
  top=$(git rev-parse --show-toplevel 2>/dev/null) || return 0
  [ -x "$top/.githooks/check-pass" ] && "$top/.githooks/check-pass" tree 2>/dev/null
  return 0
}
pass_record() {
  local code=$1 tree=$2 a resume=0 top pf state rf args
  shift 2
  { [ "$code" -eq 0 ] && [ -n "$tree" ]; } || return "$code"
  for a in "$@"; do [ "$a" = --resume ] && resume=1; done
  top=$(git rev-parse --show-toplevel)
  pf=${PROGRESS_FILE:-$top/.progress/check.json}
  state=$(jq -r 'if (.passed_steps | length) == .total and .state == "passed" then "all" else "partial" end' "$pf" 2>/dev/null)
  if [ "$state" != all ]; then
    echo "check: green, but not every part passed (one said UNCHECKED, or $pf is unreadable): the pass was not recorded" >&2
    return "$code"
  fi
  args=(--from "$tree")
  if [ $resume = 1 ]; then
    rf=$(jq -r 'if .resumed_from == null then "-" else (.resumed_from | capture("^(?<r>[0-9]+/[0-9]+) at ").r) end' "$pf" 2>/dev/null)
    [ -n "$rf" ] || { echo "check: green, but the resume point in $pf could not be read: the pass was not recorded" >&2; return "$code"; }
    [ "$rf" != - ] && args+=(--resumed-from "$rf")
  fi
  (cd "$top" && .githooks/check-pass record "${args[@]}" >&2) || echo "check: green, but the pass was not recorded (see above)" >&2
  return "$code"
}
cmd_check() {
  local tree; tree=$(pass_tree "$@")
  python3 "$here/devtools/parts.py" run "${CHECK_PARTS:-$here/check.parts.json}" "$@"
  pass_record $? "$tree" "$@"; exit $?
}
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
