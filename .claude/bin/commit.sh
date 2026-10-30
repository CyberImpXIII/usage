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
#          is true (the repo's push record, written by setup). Unset, false or
#          not a boolean: not pushed, and the line says which. Detached HEAD:
#          push-failed. Rejected by the remote: push-refused; any other push
#          failure: push-failed. Either way the commit stands;
#       6. print ONE line: outcome, hash, the files, pushed or not.
#   commit.sh [-C <repo>] --push
#       push HEAD to origin/<its branch> under the same record as step 5, and
#       nothing else: no check, no staging, no commit (tl-571: a commit made
#       while the record was unset otherwise reaches origin only with the next
#       commit). Outcomes: pushed (sent, or already there: the line says
#       which, with the range and count); push-skipped when there is no
#       origin, the record is unset, false or not a boolean, or HEAD is
#       detached (the line says which); push-refused; push-failed.
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
  "push-refused:5:the remote rejected the push (after a commit, the commit stands)"
  "push-failed:6:the push did not complete (network, auth, detached HEAD); a commit stands"
  "path-refused:7:a path is outside the repo, an option, ., :/ or a glob; nothing done"
  "commit-refused:8:git refused the commit (a git hook); the paths stay staged"
  "pushed:0:--push: HEAD is on origin/<branch> (sent, or already there, as the line says)"
  "push-skipped:9:--push: nothing pushed (no origin, record unset/false/not boolean, detached HEAD)"
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
  echo "usage: commit.sh [-C <repo>] \"<message>\" <path>...   |   commit.sh [-C <repo>] --push   |   commit.sh [-C <repo>] --status   |   commit.sh --outcomes [--json]" >&2
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

# The push, one routine for both modes. push_blocker prints why HEAD is not
# to be pushed (empty: it is) and sets $branch; push_head runs the push and
# sets $pushed_line and $push_out (pushed | push-refused | push-failed) from
# git's --porcelain ref line, not from the exit code alone.
branch=""
push_blocker() {
  git remote get-url origin >/dev/null 2>&1 || { echo "no origin"; return; }
  if ! git config --get funnel.push >/dev/null 2>&1; then
    echo "no push record: git config funnel.push is unset"; return
  fi
  local rec
  rec=$(git config --bool --get funnel.push 2>/dev/null) || { echo "the push record is not a boolean: git config funnel.push"; return; }
  [ "$rec" = true ] || { echo "the push record says no: git config funnel.push is $rec"; return; }
  branch=$(git symbolic-ref -q --short HEAD) || { echo "detached HEAD"; return; }
}
push_head() {   # log-file
  local plog=$1 rc line flag summary n
  GIT_FUNNEL=commit git push --porcelain origin "HEAD:refs/heads/$branch" >"$plog" 2>&1; rc=$?
  line=$(grep -m1 $'\tHEAD:refs/heads/'"$branch"$'\t' "$plog")
  flag=${line:0:1}; summary=${line##*$'\t'}
  case "$rc:$flag" in
    0:=) push_out=pushed; pushed_line="origin/$branch already at $(git rev-parse --short HEAD); nothing sent" ;;
    0:" "|0:+|0:\*)
      push_out=pushed
      case "$summary" in
        *..*) n=$(git rev-list --count "${summary/.../..}" 2>/dev/null) || n="?"
              pushed_line="pushed origin/$branch $summary, $n commit(s)" ;;
        *) pushed_line="pushed origin/$branch $summary" ;;
      esac ;;
    *:!) push_out=push-refused; pushed_line="origin/$branch rejected the push: $summary" ;;
    *) push_out=push-failed; pushed_line="push to origin/$branch failed (git push exited $rc)" ;;
  esac
}

if [ "${1:-}" = "--push" ]; then
  [ $# -eq 1 ] || usage
  head=$(git rev-parse --short HEAD 2>/dev/null) || finish push-skipped "- no commit yet: nothing to push"
  why=$(push_blocker)
  [ -z "$why" ] || finish push-skipped "$head not pushed ($why)"
  branch=$(git symbolic-ref -q --short HEAD)
  plog=$(mktemp "${TMPDIR:-/tmp}/commit-push.XXXXXX") || finish push-failed "$head - cannot make a log file"
  trap 'rm -f "$plog"' EXIT
  push_head "$plog"
  [ "$push_out" = pushed ] || tail -n "$DIGEST_LINES" "$plog"
  finish "$push_out" "$head $pushed_line"
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
why=$(push_blocker)
case "$why" in
  "") ;;
  "detached HEAD") finish push-failed "$hash $files; not pushed (detached HEAD)" ;;
  *) finish committed "$hash $files; not pushed ($why)" ;;
esac
branch=$(git symbolic-ref -q --short HEAD)
push_head "$log"
[ "$push_out" = pushed ] && finish committed "$hash $files; $pushed_line"
tail -n "$DIGEST_LINES" "$log"
finish "$push_out" "$hash $files; $pushed_line"
