#!/usr/bin/env bash
# hooks: applies_to=all dest=.claude/bin
# The ONE way an agent commits (PLAN-services.md §3 S2a, F3): check, stage by
# name, commit, push per the repo's record -- each step deterministic, every
# outcome one word from a fixed set. git-funnel.sh (the PreToolUse hook)
# refuses a typed `git add/commit/push` and names this verb instead.
# Tests: bash tools/hooks/source/tests/test-commit.sh
#
#   commit.sh [-C <repo>] "<message>" <path>...
#       1. refuse a path outside the repo, an option (-A, --all, -a), `.`,
#          `./`, `:/` or a glob (path-refused); nothing changed at those paths:
#          nothing-staged, before any check runs;
#       2. run the repo's `./dev.sh check`, at most $COMMIT_CHECK_TIMEOUT
#          seconds (default 1800); on failure or timeout print its last 20
#          lines and stop: check-failed, NOTHING staged or committed;
#       3. `git add -- <paths>` (only those);
#       4. `git commit -F <message> -- <paths>` (only those, whatever else is
#          staged), with trailers `Agent:` and `Agent-Id:` from FUNNEL_AGENT /
#          FUNNEL_AGENT_ID (git-funnel.sh sets them from the hook input; absent,
#          none are written and the line says so) and the attribution line:
#          $COMMIT_ATTRIBUTION, else `Co-Authored-By: Claude
#          <noreply@anthropic.com>` for an agent's commit; none when the
#          message already carries a Co-Authored-By line. A git hook refusing
#          it: commit-refused (the paths stay staged);
#       5. push only when `origin` exists AND `git config --bool funnel.push`
#          is true (the repo's push record, written by setup). Unset: not
#          pushed, and the line says why. Rejected by the remote: push-refused;
#          any other push failure: push-failed. Either way the commit stands;
#       6. print ONE line: outcome, hash, the files, pushed or not.
#   commit.sh [-C <repo>] --status
#       one line per changed path (git status --porcelain), then the last
#       commit's hash, subject and Agent trailer.
#   commit.sh --outcomes [--json]
#       the fixed outcome set and each one's exit code (tools/hooks vocab.json
#       holds the same table; tests/test_vocab_verbs.py holds the two equal).
#
# Every git call it makes carries GIT_FUNNEL=commit: the token the git-side
# pre-commit and pre-push hooks (setup's) look for before letting a commit
# through when CLAUDECODE is set. Typing that variable is refused by
# git-funnel.sh.
#
# INSTALLED INTO EVERY REPO'S .claude/bin/ (setup); the source is tools/hooks
# source/bin/. Change the source, never a copy.

set -uo pipefail
export LC_ALL=C

OUTCOMES=(
  "committed:0:committed (and pushed, or not, as the line says)"
  "usage:2:the command line is not one this verb takes"
  "check-failed:3:./dev.sh check failed, timed out or is missing; nothing staged or committed"
  "nothing-staged:4:the named paths hold no change; nothing committed"
  "push-refused:5:committed; the remote rejected the push"
  "push-failed:6:committed; the push did not complete (network, auth, no branch)"
  "path-refused:7:a path is outside the repo, an option, ., :/ or a glob; nothing done"
  "commit-refused:8:git refused the commit (a git hook); the paths stay staged"
)
CHECK_TIMEOUT=${COMMIT_CHECK_TIMEOUT:-1800}
DIGEST_LINES=20

code_of() { local o; for o in "${OUTCOMES[@]}"; do [ "${o%%:*}" = "$1" ] && { o=${o#*:}; echo "${o%%:*}"; return; }; done; echo 1; }
finish() {   # outcome line...
  local out=$1; shift
  printf '%s %s\n' "$out" "$*"
  exit "$(code_of "$out")"
}
usage() {
  echo "usage: commit.sh [-C <repo>] \"<message>\" <path>...   |   commit.sh [-C <repo>] --status   |   commit.sh --outcomes [--json]" >&2
  finish usage "(see the header of this file)"
}

repo=""
if [ "${1:-}" = "-C" ]; then
  [ $# -ge 2 ] || usage
  repo=$2; shift 2
fi

case "${1:-}" in
  --outcomes)
    if [ "${2:-}" = "--json" ]; then
      first=1; printf '{'
      for o in "${OUTCOMES[@]}"; do
        n=${o%%:*}; r=${o#*:}; c=${r%%:*}
        [ $first = 1 ] || printf ','; first=0
        printf '"%s":%s' "$n" "$c"
      done
      printf '}\n'
    else
      for o in "${OUTCOMES[@]}"; do n=${o%%:*}; r=${o#*:}; printf '%-15s %s  %s\n' "$n" "${r%%:*}" "${r#*:}"; done
    fi
    exit 0 ;;
  ""|-h|--help) usage ;;
esac

if [ -n "$repo" ]; then cd "$repo" 2>/dev/null || finish path-refused "-C $repo: no such folder"; fi
top=$(git rev-parse --show-toplevel 2>/dev/null) || finish path-refused "$(pwd -P): not in a git repo"
top=$(cd "$top" && pwd -P)

if [ "${1:-}" = "--status" ]; then
  git status --porcelain=v1 2>/dev/null
  last=$(git log -1 --format='%h %s%x1f%(trailers:key=Agent,valueonly,separator=%x2C)' 2>/dev/null)
  if [ -n "$last" ]; then
    who=${last#*$'\x1f'}; who=${who%$'\n'}
    printf 'last: %s (Agent: %s)\n' "${last%%$'\x1f'*}" "${who:-none}"
  else echo "last: no commit yet"; fi
  exit 0
fi

msg=${1:-}; shift
[ -n "$msg" ] || usage
[ $# -gt 0 ] || usage
[ "${1:-}" = "--" ] && shift
[ $# -gt 0 ] || usage

# Lexical normalisation: no filesystem access, so a deleted path still resolves.
norm() {
  local p=$1 out=() part IFS=/
  for part in $p; do
    case "$part" in
      ""|.) ;;
      ..) [ ${#out[@]} -gt 0 ] && unset "out[$((${#out[@]}-1))]" ;;
      *) out+=("$part") ;;
    esac
  done
  printf '/%s' "${out[*]+${out[*]}}"
}
here=$(pwd -P)
paths=()
for p in "$@"; do
  case "$p" in
    -*) finish path-refused "$p: an option; name each path (no -A, --all, -a)" ;;
    .|./|:/*|:\(*) finish path-refused "$p: stages every change, other agents' included; name each path" ;;
    *'*'*|*'?'*|*'['*) finish path-refused "$p: a glob; name each path" ;;
  esac
  case "$p" in /*) abs=$(norm "$p") ;; *) abs=$(norm "$here/$p") ;; esac
  case "$abs" in
    "$top"/?*) paths+=("${abs#"$top"/}") ;;
    *) finish path-refused "$p: outside the repo $top" ;;
  esac
done
cd "$top" || finish path-refused "$top: cannot enter"

if [ -z "$(git status --porcelain=v1 -- "${paths[@]}" 2>/dev/null)" ]; then
  finish nothing-staged "- ${paths[*]}: no change"
fi

# 2. the check, bounded
if [ ! -x ./dev.sh ]; then finish check-failed "- no ./dev.sh in $top: cannot check, so nothing is committed"; fi
log=$(mktemp "${TMPDIR:-/tmp}/commit-check.XXXXXX") || finish check-failed "- cannot make a log file"
trap 'rm -f "$log" "${msgf:-}"' EXIT
env -u GIT_FUNNEL ./dev.sh check >"$log" 2>&1 &
pid=$!
deadline=$((SECONDS + CHECK_TIMEOUT))
while kill -0 "$pid" 2>/dev/null; do
  if [ $SECONDS -ge $deadline ]; then
    kill "$pid" 2>/dev/null; sleep 1; kill -9 "$pid" 2>/dev/null
    tail -n "$DIGEST_LINES" "$log"
    finish check-failed "- ./dev.sh check still running after ${CHECK_TIMEOUT}s (COMMIT_CHECK_TIMEOUT); nothing staged"
  fi
  sleep 1
done
wait "$pid"; rc=$?
if [ $rc -ne 0 ]; then
  tail -n "$DIGEST_LINES" "$log"
  finish check-failed "- ./dev.sh check exited $rc; nothing staged"
fi

# 3-4. stage by name, commit only those
export GIT_FUNNEL=commit
git add -- "${paths[@]}" >/dev/null 2>"$log" || { tail -n "$DIGEST_LINES" "$log"; finish path-refused "- git add refused: ${paths[*]}"; }
if git diff --cached --quiet -- "${paths[@]}" 2>/dev/null; then
  finish nothing-staged "- ${paths[*]}: no change once staged"
fi
msgf=$(mktemp "${TMPDIR:-/tmp}/commit-msg.XXXXXX") || finish commit-refused "- cannot make a message file"
printf '%s\n' "$msg" >"$msgf"
trailers=()
stamp="no Agent trailer (FUNNEL_AGENT unset: not run through git-funnel.sh)"
if [ -n "${FUNNEL_AGENT:-}" ]; then
  trailers+=(--trailer "Agent: $FUNNEL_AGENT")
  [ -n "${FUNNEL_AGENT_ID:-}" ] && trailers+=(--trailer "Agent-Id: $FUNNEL_AGENT_ID")
  stamp="Agent: $FUNNEL_AGENT"
fi
case "$msg" in
  *[Cc]o-[Aa]uthored-[Bb]y:*) ;;
  *) if [ -n "${COMMIT_ATTRIBUTION:-}" ]; then trailers+=(--trailer "$COMMIT_ATTRIBUTION")
     elif [ -n "${FUNNEL_AGENT:-}" ]; then trailers+=(--trailer "Co-Authored-By: Claude <noreply@anthropic.com>"); fi ;;
esac
if ! git commit -q -F "$msgf" ${trailers[@]+"${trailers[@]}"} -- "${paths[@]}" >"$log" 2>&1; then
  tail -n "$DIGEST_LINES" "$log"
  finish commit-refused "- git commit refused; ${paths[*]} still staged"
fi
hash=$(git rev-parse --short HEAD)
files="${#paths[@]} file(s): ${paths[*]}; $stamp"

# 5. push per the record
if ! git remote get-url origin >/dev/null 2>&1; then
  finish committed "$hash $files; not pushed (no origin)"
fi
if [ "$(git config --bool funnel.push 2>/dev/null)" != "true" ]; then
  finish committed "$hash $files; not pushed (no push record: git config funnel.push is not true)"
fi
branch=$(git symbolic-ref -q --short HEAD) || finish push-failed "$hash $files; not pushed (detached HEAD)"
if git push -q origin "HEAD:refs/heads/$branch" >"$log" 2>&1; then
  finish committed "$hash $files; pushed origin/$branch"
fi
tail -n "$DIGEST_LINES" "$log"
if grep -q 'rejected\]' "$log"; then finish push-refused "$hash $files; origin/$branch rejected the push"; fi
finish push-failed "$hash $files; push to origin/$branch failed"
