#!/usr/bin/env bash
# Tests for git-funnel.sh. Run: bash .claude/hooks/test-git-funnel.sh
#
# Each refused git verb is refused with the verb installed and passes without
# it (the fail-open rollout); each has a read-only or look-alike twin that
# passes; a verb call comes back stamped and is then RUN end to end through
# the real commit.sh when it sits in the source tree beside this test. Plus
# fail-open: malformed input, no library.

set -uo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOOK="$DIR/git-funnel.sh"
fails=0
T=$(mktemp -d "${TMPDIR:-/tmp}/git-funnel-test.XXXXXX") || exit 1
trap 'rm -rf "$T"' EXIT
pass() { printf '  ok    %s\n' "$1"; }
fail() { printf '  FAIL  %s\n' "$1"; fails=$((fails + 1)); }

# Two project dirs: ON holds the verb, OFF does not.
ON=$T/on; OFF=$T/off
mkdir -p "$ON/.claude/bin" "$OFF/.claude"
printf '#!/bin/sh\nexit 0\n' >"$ON/.claude/bin/commit.sh"; chmod +x "$ON/.claude/bin/commit.sh"

input() {   # command [agent_type]
  if [ -n "${2:-}" ]; then
    jq -nc --arg c "$1" --arg t "$2" '{hook_event_name:"PreToolUse",session_id:"sess-1",cwd:"/x",tool_name:"Bash",
      tool_input:{command:$c,description:"d"},agent_type:$t,agent_id:"agent-1"}'
  else
    jq -nc --arg c "$1" '{hook_event_name:"PreToolUse",session_id:"sess-1",cwd:"/x",tool_name:"Bash",tool_input:{command:$c}}'
  fi
}
run() {   # project command [agent_type] -> $code $out $err
  local e; e=$(mktemp "$T/err.XXXXXX")
  out=$(input "$2" "${3:-}" | CLAUDE_PROJECT_DIR=$1 bash "$HOOK" 2>"$e"); code=$?
  err=$(cat "$e"); rm -f "$e"
}
refused() {   # label command
  run "$ON" "$2"
  if [ "$code" = 2 ] && [ -z "$out" ] && [[ $err == *".claude/bin/commit.sh"* ]]; then pass "refused: $1"
  else fail "refused: $1 -- exit $code, out [$out], err [${err:0:120}]"; fi
  run "$OFF" "$2"
  if [ "$code" = 0 ] && [ -z "$out" ]; then pass "  ...passes where the verb is not installed"
  else fail "  ...without the verb: exit $code, out [$out]"; fi
}
passes() {   # label command
  run "$ON" "$2"
  if [ "$code" = 0 ] && [ -z "$out" ]; then pass "passes: $1"; else fail "passes: $1 -- exit $code, err [${err:0:120}]"; fi
}

echo "refused, each naming the verb (and passing without it):"
refused "git add"                     'git add a.txt'
refused "git commit"                  'git commit -m x'
refused "git push"                    'git push origin main'
refused "git rebase"                  'git rebase main'
refused "git reset --hard"            'git reset --hard HEAD~1'
refused "git checkout -- <path>"      'git checkout -- a.txt'
refused "git checkout ."              'git checkout .'
refused "git restore <path>"          'git restore a.txt'
refused "git restore -SW"             'git restore -SW a.txt'
refused "git stash"                   'git stash'
refused "git stash push"              'git stash push -m x'
refused "git -C <dir> commit"         'git -C tools/hooks commit -m x'
refused "git -c k=v commit"           'git -c user.name=x commit -m y'
refused "/usr/bin/git push"           '/usr/bin/git push'
refused "after && and inside \$( )"   'cd x && echo $(git commit -m y)'
refused "a bash -c string"            "bash -c 'git push'"
refused "a heredoc fed to sh"         $'sh <<EOF\ngit add a\nEOF'
refused "env VAR=x git commit"        'env A=b git commit -m x'

echo "passes (read-only git, look-alikes):"
passes "git status"                   'git status --porcelain'
passes "git log"                      'git log -1 --format=%B'
passes "git diff / show / rev-parse"  'git diff HEAD; git show HEAD; git rev-parse HEAD'
passes "git -C <dir> status"          'git -C tools/hooks status'
passes "git reset (soft, unstages)"   'git reset HEAD a.txt'
passes "git checkout <branch>"        'git checkout main'
passes "git restore --staged"         'git restore --staged a.txt'
passes "git stash list / show"        'git stash list; git stash show'
passes "a quoted git commit is text"  'echo "git commit -m x"'
passes "a heredoc to cat is text"     $'cat <<EOF\ngit push\nEOF'
passes "grep for the word"            'grep -n "git push" CLAUDE.md'
passes "a commit message naming git"  'echo done # git commit'

echo "the forgery is refused everywhere:"
for c in 'GIT_FUNNEL=commit git commit -m x' 'export FUNNEL_AGENT=me; x/commit.sh m a' 'env FUNNEL_AGENT_ID=1 ./dev.sh commit m a'; do
  run "$OFF" "$c"
  if [ "$code" = 2 ] && [[ $err == *"forgery"* ]]; then pass "refused (even without the verb): $c"; else fail "forgery: $c -- exit $code"; fi
done
run "$OFF" 'echo "set GIT_FUNNEL=commit in docs"'
[ "$code" = 0 ] && pass "  ...a quoted mention passes" || fail "  quoted mention: exit $code"

echo "a call to the verb is stamped:"
run "$ON" '.claude/bin/commit.sh "msg" a.txt' hooks
want="export FUNNEL_AGENT='hooks' FUNNEL_AGENT_ID='agent-1'; .claude/bin/commit.sh \"msg\" a.txt"
got=$(printf '%s' "$out" | jq -r '.hookSpecificOutput.updatedInput.command' 2>/dev/null)
[ "$code" = 0 ] && [ "$got" = "$want" ] && pass "an agent's call: its agent_type and agent_id" || fail "stamp: [$got]"
[ "$(printf '%s' "$out" | jq -r '.hookSpecificOutput.updatedInput.description')" = d ] \
  && pass "  (the rest of tool_input kept: description)" || fail "  tool_input changed"
run "$ON" './dev.sh commit "msg" a.txt'
got=$(printf '%s' "$out" | jq -r '.hookSpecificOutput.updatedInput.command' 2>/dev/null)
[ "$got" = "export FUNNEL_AGENT='dispatcher' FUNNEL_AGENT_ID='sess-1'; ./dev.sh commit \"msg\" a.txt" ] \
  && pass "dev.sh commit, main thread: dispatcher and the session id" || fail "main thread stamp: [$got]"
run "$ON" './dev.sh check'
[ -z "$out" ] && pass "dev.sh check is not a verb call" || fail "dev.sh check stamped"
run "$ON" '.claude/bin/commit.sh m a' "bad'name"
[ "$code" = 0 ] && [ -z "$out" ] && pass "an agent name it cannot quote: no stamp, runs as written" || fail "unsafe name: [$out]"

# End to end: the stamped command, run, writes the trailers git reads back.
VERB="$DIR/../bin/commit.sh"
if [ -x "$VERB" ]; then
  R=$T/repo; git init -q "$R"
  printf '#!/bin/sh\nexit 0\n' >"$R/dev.sh"; chmod +x "$R/dev.sh"; echo x >"$R/a.txt"
  git -C "$R" -c user.name=t -c user.email=t@t add dev.sh; git -C "$R" -c user.name=t -c user.email=t@t commit -qm init
  run "$ON" "$VERB -C $R m a.txt" hooks
  cmd=$(printf '%s' "$out" | jq -r '.hookSpecificOutput.updatedInput.command')
  GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t bash -c "$cmd" >/dev/null 2>&1
  tr=$(git -C "$R" log -1 --format='%(trailers:key=Agent-Id,valueonly)' | tr -d '\n')
  [ "$tr" = agent-1 ] && pass "end to end: the verb wrote Agent-Id: agent-1" || fail "end to end: trailer [$tr]"
else
  echo "  skip  end to end: no ../bin/commit.sh beside this copy (an installed copy)"
fi

echo "fails open:"
for given in '' '{}' 'not json' '{"tool_input":{"command":"git commit -m x"}' ; do
  o=$(printf '%s' "$given" | CLAUDE_PROJECT_DIR=$ON bash "$HOOK" 2>/dev/null); c=$?
  [ "$c" = 0 ] && [ -z "$o" ] && pass "malformed input: $(printf %q "$given")" || fail "malformed $(printf %q "$given"): exit $c"
done
mkdir -p "$T/nolib/hooks"; cp "$HOOK" "$T/nolib/hooks/"
input 'git commit -m x' | CLAUDE_PROJECT_DIR=$ON bash "$T/nolib/hooks/git-funnel.sh" >/dev/null 2>&1
[ $? = 0 ] && pass "library missing: exit 0" || fail "library missing: blocked"

[ "$fails" = 0 ] && echo "git-funnel: all cases passed" || echo "git-funnel: $fails FAILED"
[ "$fails" = 0 ]
