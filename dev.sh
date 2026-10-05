#!/usr/bin/env bash
# SETUP-STUB: installed by the setup tool. Replace this file with the repo's real
# pre-commit command; setup reports the repo as `needs owner` while this line is here.
#
# The contract: `./dev.sh check` runs every gate this repo has and exits non-zero
# if any fails; `./dev.sh check --json` prints one JSON document on stdout and
# nothing else, exit 0 iff "ok": {"ok": bool, "checks": [{"name", "status" (ok,
# fail, unchecked, error), "counts": {"failed": N}, "failures": [{"message",
# "file", "line", "role"}]}]}, the one schema every repo's check prints.
# This stub never fakes green: it fails until the owner fills it in, and its
# --json is that schema's red report, one check (`stub-unfilled`) pointing at line 2.
set -uo pipefail

usage() {
  cat <<'EOF'
./dev.sh <command>

  check      every gate this repo has (run this before committing); --json: the one schema
EOF
}

cmd_check() {
  if [ "${1:-}" = "--json" ]; then
    printf '%s\n' '{"ok": false, "checks": [{"name": "stub-unfilled", "status": "fail", "counts": {"failed": 1}, "failures": [{"message": "not implemented: fill in the contract (./dev.sh is still the setup stub; replace it with the real gates of this repo)", "file": "dev.sh", "line": 2, "role": "code"}]}]}'
  else
    echo "not implemented: fill in the contract (./dev.sh check is the setup stub)"
  fi
  return 1
}

case "${1:-}" in
  check) shift; cmd_check "$@" ;;
  ""|-h|--help|help) usage ;;
  *) echo "unknown command: $1"; usage; exit 2 ;;
esac
