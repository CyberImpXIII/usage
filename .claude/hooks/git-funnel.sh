#!/usr/bin/env bash
# hooks: applies_to=all
# Funnels git's writes through the one verb (PLAN-services.md §3 S2a, F3 layer
# 1). PreToolUse hook on Bash; see ../settings.json.
# Tests: bash .claude/hooks/test-git-funnel.sh
#
# REFUSED (exit 2, the reason names the verb), once the verb is installed in
# the project dir ($CLAUDE_PROJECT_DIR/.claude/bin/commit.sh, executable):
#   git add, git commit, git push, git rebase, git reset --hard,
#   git checkout with `--`, `.` or `:/` (discards files), git restore (unless
#   only --staged), git stash (except `stash list` / `stash show`).
# Read-only git (status, log, diff, show, rev-parse, ...) and every other git
# verb pass. git's global options (-C x, -c k=v, --git-dir x, ...) are read.
# Without the verb installed, nothing is refused: refusing with nowhere to go
# would strand the commit (fail open while setup rolls the verb out).
#
# ALWAYS REFUSED: a command that assigns GIT_FUNNEL or FUNNEL_AGENT* itself.
# GIT_FUNNEL is the token git's own pre-commit/pre-push hooks (layer 2,
# setup's) look for; FUNNEL_AGENT* is the stamp this hook sets.
#
# A CALL TO THE VERB (a word ending `commit.sh`, or `dev.sh commit`) comes
# back as updatedInput with `export FUNNEL_AGENT='<agent_type>'
# FUNNEL_AGENT_ID='<agent_id>';` in front (a main thread: MAIN_THREAD_NAME and
# its session_id, as git-stamp.sh stamps), so the verb writes the same
# Agent:/Agent-Id: trailers push-gate.sh reads. No permission decision is
# taken: the permission system still decides. A name or id it cannot quote
# safely: no stamp, the verb says "no Agent trailer".
#
# THE READER is bash_segments (../lib/write-targets.sh): quotes parsed,
# `$( )` contents and a heredoc or `-c` string fed to a shell read as
# commands. NOT SEEN: `"$(git commit)"` inside double quotes, eval, xargs, an
# alias, a script that runs git. Those are what layer 2 (git's own hooks,
# refusing when CLAUDECODE is set and GIT_FUNNEL is not) is for.
#
# INSTALLED INTO EVERY REPO'S .claude/hooks/, with ../lib/write-targets.sh.
# The source is tools/hooks (source/hooks/); change the source, never a copy.
#
# FAILS OPEN. Malformed input, no jq, the library missing, a command it cannot
# read: exit 0, no output. Exit 2 only for a refusal above.

set -uo pipefail
export LC_ALL=C

MAIN_THREAD_NAME=dispatcher

input=$(cat 2>/dev/null) || exit 0
case "$input" in *git*|*commit*|*FUNNEL*) ;; *) exit 0 ;; esac
command -v jq >/dev/null 2>&1 || exit 0
S=$(printf '%s' "$input" | jq -j '.tool_input.command | strings' 2>/dev/null && printf x) || exit 0
S=${S%x}
[ -n "$S" ] || exit 0
ids=$(printf '%s' "$input" | jq -r '[.agent_type // "", .agent_id // "", .session_id // "", .cwd // ""] | join("\u001f")' 2>/dev/null) || exit 0
IFS=$'\x1f' read -r agent_type agent_id session_id cwd <<< "$ids"

lib="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)/../lib/write-targets.sh"
[ -f "$lib" ] || exit 0
. "$lib" 2>/dev/null || exit 0
type bash_segments >/dev/null 2>&1 || exit 0

root=${CLAUDE_PROJECT_DIR:-$cwd}
VERB_REL=.claude/bin/commit.sh
enforce=0
[ -n "$root" ] && [ -x "$root/$VERB_REL" ] && enforce=1

denied=()     # one reason per refusal
spoof=0
calls=0       # segments that call the verb

judge_git() {   # the words after `git`
  local a=("$@") n=$# j=0 w sub="" rest=() x
  while [ $j -lt "$n" ]; do
    w=${a[$j]}
    case "$w" in
      -C|-c|--git-dir|--work-tree|--namespace|--exec-path|--config-env|--super-prefix) j=$((j+2)) ;;
      -*) j=$((j+1)) ;;
      *) sub=$w; j=$((j+1)); break ;;
    esac
  done
  [ -n "$sub" ] || return 0
  rest=(${a[@]+"${a[@]:$j}"})
  case "$sub" in
    add|commit|push|rebase) denied+=("git $sub") ;;
    reset)
      for x in ${rest[@]+"${rest[@]}"}; do [ "$x" = --hard ] && { denied+=("git reset --hard"); break; }; done ;;
    checkout)
      for x in ${rest[@]+"${rest[@]}"}; do
        case "$x" in --|.|./|:/*) denied+=("git checkout $x (discards changes)"); break ;; esac
      done ;;
    restore)
      local staged=0 worktree=0
      for x in ${rest[@]+"${rest[@]}"}; do
        case "$x" in
          --staged) staged=1 ;;
          --worktree) worktree=1 ;;
          --*) ;;
          -*) case "$x" in *S*) staged=1 ;; esac; case "$x" in *W*) worktree=1 ;; esac ;;
        esac
      done
      [ $staged = 1 ] && [ $worktree = 0 ] || denied+=("git restore (discards changes)") ;;
    stash)
      case "${rest[0]:-}" in list|show) ;; *) denied+=("git stash") ;; esac ;;
  esac
  return 0
}

while IFS= read -r -d $'\x1e' seg; do
  words=()
  IFS=$'\x1f' read -r -d '' -a words < <(printf '%s\0' "$seg") || true
  [ ${#words[@]} -gt 0 ] || continue
  for w in "${words[@]}"; do
    case "$w" in GIT_FUNNEL=*|FUNNEL_AGENT=*|FUNNEL_AGENT_*=*) spoof=1 ;; esac
  done
  i=0; n=${#words[@]}; verb=""
  while [ $i -lt "$n" ]; do
    w=${words[$i]}; i=$((i+1))
    case "$w" in
      sudo|command|env|nohup|time|exec|builtin|if|elif|while|until|then|else|do|'!'|'{') continue ;;
      [A-Za-z_]*=*) continue ;;
      *) verb=$w; break ;;
    esac
  done
  [ -n "$verb" ] || continue
  case "${verb##*/}" in
    git) judge_git ${words[@]+"${words[@]:$i}"} ;;
    commit.sh) calls=$((calls+1)) ;;
    dev.sh) [ "${words[$i]:-}" = commit ] && calls=$((calls+1)) ;;
  esac
done < <(bash_segments "$S")

if [ $spoof = 1 ]; then
  {
    echo "BLOCKED: this command sets GIT_FUNNEL or FUNNEL_AGENT itself."
    echo "GIT_FUNNEL is the token git's own hooks let through and FUNNEL_AGENT is the stamp git-funnel.sh"
    echo "sets from the hook input; typed, both are a forgery. Run the verb as it is:"
    echo "  \$CLAUDE_PROJECT_DIR/$VERB_REL [-C <repo>] \"<message>\" <path>..."
    echo "(PLAN-services.md §3 S2a F3; the hook is tools/hooks source/hooks/git-funnel.sh.)"
  } >&2
  exit 2
fi

if [ ${#denied[@]} -gt 0 ] && [ $enforce = 1 ]; then
  {
    echo "BLOCKED: $(printf '%s, ' "${denied[@]}" | sed 's/, $//') -- git's writes go through the one verb:"
    echo
    echo "  $root/$VERB_REL [-C <repo>] \"<message>\" <path>..."
    echo "      refuses -A/./:/ and paths outside the repo, runs the repo's ./dev.sh check (stops on"
    echo "      failure, nothing staged), stages and commits ONLY the named paths with your Agent"
    echo "      trailers, pushes when the repo's record (git config funnel.push) says so, and prints"
    echo "      one line: outcome, hash, files, pushed or not."
    echo "  $root/$VERB_REL [-C <repo>] --status     changed paths + who committed last"
    echo "  $root/$VERB_REL --outcomes               what each outcome means"
    echo
    echo "Read-only git (status, log, diff, show, rev-parse) is not affected. To throw work"
    echo "away (checkout --, restore, reset --hard, stash), say so in your report: that is the"
    echo "owner's call, not a step to script."
    echo "(PLAN-services.md §3 S2a F3; the hook is tools/hooks source/hooks/git-funnel.sh.)"
  } >&2
  exit 2
fi

[ $calls -gt 0 ] || exit 0
name=${agent_type:-$MAIN_THREAD_NAME}
id=${agent_id:-$session_id}
safe='^[A-Za-z0-9._@:/+-]+$'
[[ $name =~ $safe ]] || exit 0
[ -z "$id" ] || [[ $id =~ $safe ]] || exit 0
new="export FUNNEL_AGENT='$name' FUNNEL_AGENT_ID='$id'; $S"
out=$(printf '%s' "$input" | jq -c --arg c "$new" \
  '{hookSpecificOutput: {hookEventName: "PreToolUse", updatedInput: (.tool_input + {command: $c})}}' 2>/dev/null) || exit 0
printf '%s\n' "$out"
exit 0
