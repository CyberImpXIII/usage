#!/usr/bin/env bash
# The pre-commit command of tools/usage. `./dev.sh check` runs every gate
# (devtools/check.py: test, files, hooks, shared) and exits non-zero if any is
# fail or error; `./dev.sh check --json` prints the one schema and nothing else.
set -uo pipefail
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

usage() {
  cat <<'EOF'
./dev.sh <command>

  check [--json] [GATE...]   every gate (test, files, hooks, guard, shared), or the ones named; run before committing
EOF
}

case "${1:-}" in
  check) shift; exec python3 "$here/devtools/check.py" "$@" ;;
  ""|-h|--help|help) usage ;;
  *) echo "unknown command: $1"; usage; exit 2 ;;
esac
