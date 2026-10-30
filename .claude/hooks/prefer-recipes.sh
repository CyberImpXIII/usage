#!/usr/bin/env bash
# hooks: applies_to=all
# Blocks an interactive browser call against a site that already has a working
# recipe. PreToolUse hook on the Claude-in-Chrome tools, WebFetch and Bash; see
# ../settings.json. Tests: bash .claude/hooks/test-prefer-recipes.sh
#
# THIS FILE IS INSTALLED INTO EVERY REPO'S .claude/hooks/, because a hook only
# fires when Claude Code's project dir is the one holding it -- so a rule
# enforced in only one folder is not enforced when a session starts in another.
# The source is tools/hooks (source/hooks/); setup installs from it, and
# `hooks copies` there fails when a copy differs from it in meaning or a repo
# lacks one. Change the source, never a copy. The `# hooks:` line above is
# where this file says it applies (tools/hooks vocab.json).
#
# Copies rather than symlinks: a hook whose command is missing exits non-zero,
# which Claude Code reads as a block, so a dangling link would refuse every
# matching call -- far worse than the duplication.
#
# WHY THIS IS A HOOK AND NOT A RULE: it was already a rule, in two files.
# claudeTest/CLAUDE.md opens with "Before reaching for a generic approach
# (interactive browser tools, one-off scripts, manual steps), check whether a
# tool in this folder already does the job", and site-scrapers/CLAUDE.md rule 1
# is "Check before assuming: node query.js site <target>". The whole README
# section "Why this saves tokens" measures the gap: a known site is one Bash
# call returning one JSON object, where the interactive path is a
# tabs_context_mcp call, a navigate, one or more screenshots (the single most
# expensive line item), a read_page accessibility dump that can run to tens of
# thousands of characters, and several chunked javascript_tool extractions --
# 8+ round trips for the same data.
#
# The failure mode is not ignorance, it is habit: opening a browser is the
# obvious move, and the check that would have avoided it is one the agent has
# to remember to make FIRST, before the expensive thing. That is exactly the
# shape rule 4 already lost to, which is why that one became a hook too.
#
# It blocks rather than warns because on a `working` recipe the alternative is
# strictly better and always available. When a browser is genuinely needed --
# building a second recipe for the same host, confirming a wall, an attended
# handoff -- `./dev.sh browser-ok` opens a short window. That is a conscious
# act, which is the point; a block with no way past it would be worse than the
# habit it corrects.
#
# A BROWSER DRIVEN THROUGH BASH (tl-562, PLAN-services.md §4 step 9.9 step 4):
# a CLI that drives a browser, or a request to its local HTTP API, reaches a
# site as WebFetch does. Which commands, addresses and clearances count is
# DATA, in ../lib/browser-drivers.sh; this file names none of them. A Bash call
# whose command (read with bash_segments, ../lib/write-targets.sh, so a name
# inside a quoted string, a grep or a commit message is not a call) drives a
# tool:
#   - with no `cleared` row: BLOCKED, whatever the host, and browser-ok does
#     not open it (Jacob 2026-10-23, tl-565 `keep`: blocked until step 1
#     clears one);
#   - cleared: blocked exactly as WebFetch is, on a URL in that command that a
#     `working` recipe covers, browser-ok honoured.
# Only INSIDE the workspace this copy sits in (the folder holding the
# site-scrapers it finds): a session whose project dir is elsewhere runs both
# tools as installed (Jacob: "I intend to use it in projects outside this
# enviroment"), so a user-scope registration would not carry the block out.
# Misses: the lib's header lists them (eval, xargs, an alias, a script that
# calls the API itself).
#
# FAILS OPEN. A hook that breaks every browser call would be far worse than the
# problem it solves, so anything unexpected here allows the call.

set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)" || exit 0

# Find site-scrapers by walking UP from this hook, not by assuming a depth.
#
# The first version used "../.." with a one-level fallback, which worked from
# site-scrapers/ and from the tools folder and nowhere else. Installed in
# emailTools/.claude/hooks/ it resolved to a path that does not exist, exited 0,
# and enforced nothing -- a guard that is present, reports no error, and does
# not run. Each tool folder is its own repo at its own depth, so the layout has
# to be discovered.
#
# Identified by its package.json NAME, the one thing only site-scrapers has.
# "dev.sh and engine.js" was not unique: scriptingTools/data-bridge has both
# (and a query.js), so its copy of this hook resolved to data-bridge, found no
# recipes, and allowed everything -- present, silent, enforcing nothing.
is_ss() { [ -f "$1/dev.sh" ] && grep -qE '"name"[[:space:]]*:[[:space:]]*"site-scrapers"' "$1/package.json" 2>/dev/null; }
find_repo() {
  local d="$HERE"
  while [ "$d" != "/" ] && [ -n "$d" ]; do
    # A sibling checkout, which is how every other tool folder sees it.
    if is_ss "$d/site-scrapers"; then printf '%s\n' "$d/site-scrapers"; return 0; fi
    # Or we are inside it already (whatever the clone's folder is called).
    if is_ss "$d"; then printf '%s\n' "$d"; return 0; fi
    d="$(dirname "$d")"
  done
  return 1
}

# Host only, lowercased, www stripped. Anything that is not an http(s) URL --
# about:blank, a file path, a bare search term -- has no host.
url_host() {
  printf '%s' "$1" | sed -nE 's#^[Hh][Tt][Tt][Pp][Ss]?://([^/?#]+).*#\1#p' | sed -E 's/^www\.//; s/:[0-9]+$//' | tr '[:upper:]' '[:lower:]'
}

# A site's robots.txt and .well-known/ files are what a recipe author reads
# BEFORE scraping (terms, rate limits): never a page a recipe returns, so never
# refused (site-scrapers' report, 2026-10-13: usajobs.gov/robots.txt was).
url_exempt() {
  local path
  path=$(printf '%s' "$1" | sed -nE 's#^[Hh][Tt][Tt][Pp][Ss]?://[^/?#]+([^?#]*).*#\1#p')
  case "$path" in /robots.txt|/.well-known/*) return 0 ;; esac
  return 1
}

# A deliberate, time-limited override from `./dev.sh browser-ok`.
# SS_BROWSER_OK moves it, exactly as it moves `dev.sh browser-ok`'s write: the
# hook test points both at a private file instead of racing over the live one.
browser_ok_open() {
  local marker="${SS_BROWSER_OK:-$REPO/data/.browser-ok}" started mins
  [ -f "$marker" ] || return 1
  started=$(sed -n 1p "$marker" 2>/dev/null)
  mins=$(sed -n 2p "$marker" 2>/dev/null)
  case "$started$mins" in ''|*[!0-9]*) return 1 ;; esac
  [ $(( $(date +%s) - started )) -lt $(( mins * 60 )) ]
}

# recipes_for <url> <host>: the working recipe targets that cover this url,
# one per line; nothing when none does or nothing can say.
#
# site-scrapers is the authority on what its recipes cover: when its dev.sh
# answers `covers <url>` with exit 0 and a FIRST LINE of exactly `covers v1`,
# the lines after it (one working recipe target each; none when no recipe
# covers THIS url -- the recipe's own template host and, for a {{url}}
# template, its subdomains) are the whole decision. Anything else (no such
# subcommand yet: usage and exit 1; a dev.sh that exits 0 on any word) falls
# back to the host match below, which blocks a covered host and every
# subdomain whatever the path (site-scrapers' report, 2026-10-13:
# developer.usajobs.gov was). The marker line is what keeps "no answer" from
# being read as "covers nothing".
recipes_for() {
  local url="$1" host="$2" covers working known
  if covers=$("$REPO/dev.sh" covers "$url" 2>/dev/null) && [ "${covers%%$'\n'*}" = "covers v1" ]; then
    case "$covers" in *$'\n'*) working=${covers#*$'\n'} ;; *) working="" ;; esac
    printf '%s\n' "$working" | grep -v '^[[:space:]]*$'
    return 0
  fi
  known=$("$REPO/dev.sh" known "$host" 2>/dev/null) || return 0
  [ -n "$known" ] || return 0

  # Only a `working` recipe is a reason to refuse. A broken, blocked or
  # needs-review one means the browser may well be the right tool right now --
  # blocking there would strand the one path left.
  #
  # And only a recipe that actually covers THIS host. `dev.sh known` matches
  # suffixes in both directions, which is right for "what do we know about this
  # domain" but wrong for blocking: a recipe on jobs.lever.co made this refuse
  # lever.co/about, Lever's marketing site, which has nothing to do with the job
  # board. A false positive in a blocking hook is worse than a miss -- it gets the
  # hook switched off, and then it protects nothing. So: the recipe's host must be
  # the host itself, or a parent of it (a recipe on lever.co may serve
  # jobs.lever.co; the reverse does not hold).
  #
  # The recipe's host is normalised exactly as $host is (lowercased, `www.`
  # stripped): `dev.sh known` strips www on both sides when it MATCHES but prints
  # the hostname as stored, and db.js upsertSite stores what it is given. Compared
  # raw, a recipe stored as `www.x.com` was listed by `known` and never blocked.
  printf '%s' "$known" | awk -F'\t' -v h="$host" '
    $2 != "working" { next }
    { split($1, parts, "#"); rh = tolower(parts[1]); sub(/^www\./, "", rh) }
    rh == h || substr(h, length(h) - length(rh)) == "." rh { print $1 }
  '
}

block_covered() {  # $1 host, $2 working targets, $3 what made the call (optional)
  {
    echo "BLOCKED: site-scrapers already has a working recipe for ${1}."
    [ -n "${3:-}" ] && echo "(This Bash call drives ${3}, a browser, so it is held as WebFetch is.)"
    echo
    echo "Use it instead of a browser — one Bash call, one JSON object, no"
    echo "screenshots and no DOM dump:"
    echo
    printf '%s\n' "$2" | sed 's#^#  ./scrape.sh #; s#$# '"'"'{...params}'"'"'#'
    echo
    echo "  node query.js site <target>     # the recipe, its params and its notes"
    echo "  node lab.js peek <target> '{}'  # run it and show samples + null counts"
    echo
    echo "Check the JSON's \"success\" field, not the exit code. A success:false run"
    echo "can still carry records when partialResults is true."
    echo
    echo "This is blocked rather than discouraged because it was already a rule in"
    echo "two CLAUDE.md files, and the interactive path costs 8+ round trips for"
    echo "data one call returns."
    echo
    echo "If you genuinely need the browser here — building another recipe for this"
    echo "host, confirming a wall, an attended handoff — open a window first:"
    echo
    echo "  ./dev.sh browser-ok        # 15 minutes, or pass minutes"
  } >&2
  exit 2
}

input=$(cat 2>/dev/null) || exit 0
tool=$(printf '%s' "$input" | jq -r '.tool_name // empty' 2>/dev/null) || exit 0
[ -n "$tool" ] || exit 0

# Only the tools that FETCH a page. tabs_context_mcp, read_console_messages and
# the rest carry no URL and say nothing about which site is being visited. Bash
# only when it drives a browser (below).
case "$tool" in
  WebFetch|mcp__claude-in-chrome__navigate|mcp__claude-in-chrome__tabs_create_mcp) ;;
  Bash)
    # --- the Bash branch: every Bash call comes here, so it leaves early. ---
    lib="$HERE/../lib/browser-drivers.sh"
    [ -f "$lib" ] || exit 0
    . "$lib" 2>/dev/null || exit 0
    type browser_drivers >/dev/null 2>&1 || exit 0
    rows=$(browser_drivers 2>/dev/null) || exit 0
    [ -n "$rows" ] || exit 0
    clis=() cli_tools=() apis=() api_tools=() clients=() launchers=() lverbs=() cleared=() alltools=()
    while IFS=$'\t' read -r k t v; do
      [ -n "$v" ] || continue
      case "$k" in
        cli) clis+=("$v"); cli_tools+=("$t")
             case " ${alltools[*]-} " in *" $t "*) ;; *) alltools+=("$t") ;; esac ;;
        api) apis+=("$v"); api_tools+=("$t") ;;
        http-client) clients+=("$v") ;;
        launcher) launchers+=("$v") ;;
        launcher-verb) lverbs+=("$v") ;;
        cleared) cleared+=("$t") ;;
      esac
    done <<< "$rows"
    [ ${#clis[@]} -gt 0 ] || [ ${#apis[@]} -gt 0 ] || exit 0
    # Cheap first: the raw payload must name a CLI or an API address at all.
    hit=0
    for v in ${clis[@]+"${clis[@]}"} ${apis[@]+"${apis[@]}"}; do
      case "$input" in *"$v"*) hit=1; break ;; esac
    done
    [ $hit = 1 ] || exit 0

    S=$(printf '%s' "$input" | jq -j '.tool_input.command | strings' 2>/dev/null && printf x) || exit 0
    S=${S%x}
    [ -n "$S" ] || exit 0
    cwd=$(printf '%s' "$input" | jq -r '.cwd // empty' 2>/dev/null) || cwd=""
    wt="$HERE/../lib/write-targets.sh"
    [ -f "$wt" ] || exit 0
    . "$wt" 2>/dev/null || exit 0
    type bash_segments >/dev/null 2>&1 || exit 0
    REPO="$(find_repo)" || exit 0
    [ -n "$REPO" ] || exit 0

    # This workspace only: the project dir must sit inside the folder holding
    # site-scrapers. Physical paths, so /tmp and /private/tmp agree.
    top=$(cd "$(dirname "$REPO")" 2>/dev/null && pwd -P) || exit 0
    proj=${CLAUDE_PROJECT_DIR:-${cwd:-$PWD}}
    proj=$(cd "$proj" 2>/dev/null && pwd -P) || exit 0
    case "$proj/" in "$top/"*) ;; *) exit 0 ;; esac

    in_list() {  # $1 word, rest: list
      local w=$1 x; shift
      for x in "$@"; do [ "$x" = "$w" ] && return 0; done
      return 1
    }
    cli_tool() {  # $1 a command word -> the tool it drives, or nothing
      local j
      for j in "${!clis[@]}"; do
        [ "${clis[$j]}" = "$1" ] && { printf '%s' "${cli_tools[$j]}"; return 0; }
      done
      return 1
    }

    drives=()   # the tool, per driving segment
    durls=()    # its URLs, newline-joined, per driving segment
    while IFS= read -r -d $'\x1e' seg; do
      words=()
      IFS=$'\x1f' read -r -d '' -a words < <(printf '%s\0' "$seg") || true
      n=${#words[@]}
      [ "$n" -gt 0 ] || continue
      i=0; verb=""
      while [ $i -lt "$n" ]; do
        w=${words[$i]}; i=$((i+1))
        case "$w" in
          sudo|command|env|nohup|time|exec|builtin|if|elif|while|until|then|else|do|'!'|'{') continue ;;
          timeout|gtimeout)   # timeout [-s SIG] [-k DUR] DURATION cmd ...
            while [ $i -lt "$n" ]; do
              case "${words[$i]}" in
                -s|-k|--signal|--kill-after) i=$((i+2)) ;;
                -*) i=$((i+1)) ;;
                *) i=$((i+1)); break ;;
              esac
            done
            continue ;;
          [A-Za-z_]*=*) continue ;;
          *) verb=$w; break ;;
        esac
      done
      [ -n "$verb" ] || continue
      base=${verb##*/}
      t=""
      if t=$(cli_tool "$base"); then :
      elif in_list "$base" ${launchers[@]+"${launchers[@]}"}; then
        t=""
        while [ $i -lt "$n" ]; do
          w=${words[$i]}; i=$((i+1))
          case "$w" in -*) continue ;; esac
          in_list "$w" ${lverbs[@]+"${lverbs[@]}"} && continue
          p=${w%%\[*}
          case "$p" in @*) ;; *) p=${p%%@*} ;; esac
          t=$(cli_tool "${p##*/}") || t=""
          break
        done
      elif in_list "$base" ${clients[@]+"${clients[@]}"}; then
        t=""
        for w in ${words[@]+"${words[@]:$i}"}; do
          for j in "${!apis[@]}"; do
            case "$w" in *"${apis[$j]}"*) t=${api_tools[$j]}; break 2 ;; esac
          done
        done
      fi
      [ -n "$t" ] || continue
      # The URLs it names, the API's own address left out.
      u=$(printf '%s\n' ${words[@]+"${words[@]:$i}"} | grep -oiE 'https?://[^]["'"'"'[:space:],{}<>()]+' 2>/dev/null) || u=""
      keep=""
      while IFS= read -r x; do
        [ -n "$x" ] || continue
        api=0
        for a in ${apis[@]+"${apis[@]}"}; do case "$x" in *"://$a"*) api=1 ;; esac; done
        [ $api = 1 ] || keep+="$x"$'\n'
      done <<< "$u"
      drives+=("$t"); durls+=("$keep")
    done < <(bash_segments "$S")
    [ ${#drives[@]} -gt 0 ] || exit 0

    # Not cleared: blocked, whatever the host and whatever browser-ok says.
    held=""
    for t in "${drives[@]}"; do
      in_list "$t" ${cleared[@]+"${cleared[@]}"} && continue
      case " $held " in *" $t "*) ;; *) held="${held:+$held }$t" ;; esac
    done
    if [ -n "$held" ]; then
      {
        echo "BLOCKED: this Bash call drives ${held}, not cleared in this workspace."
        echo
        echo "PLAN-services.md §4 step 9.9 (Jacob, 2026-10-23, tl-565 'keep'): no session"
        echo "here runs ${alltools[*]} until step 1 clears one and Jacob says yes"
        echo "to step 2; until then every Bash call driving one is held, on any host."
        echo "\`./dev.sh browser-ok\` does not open this."
        echo
        echo "Instead: a recipe (in site-scrapers: \`./dev.sh known <host>\`, then"
        echo "\`./scrape.sh <target> '{...params}'\`), WebFetch, or the Chrome tools."
        echo
        echo "A clearance is a \`cleared <tool> <record>\` row in browser-drivers.sh,"
        echo "added in tools/hooks (source/lib/) once that record exists. Outside"
        echo "$top the tools run as installed."
      } >&2
      exit 2
    fi

    # Cleared: held exactly as WebFetch is, on the first covered URL.
    for j in "${!drives[@]}"; do
      while IFS= read -r url; do
        [ -n "$url" ] || continue
        host=$(url_host "$url")
        [ -n "$host" ] || continue
        url_exempt "$url" && continue
        working=$(recipes_for "$url" "$host")
        [ -n "$working" ] || continue
        browser_ok_open && exit 0
        block_covered "$host" "$working" "${drives[$j]}"
      done <<< "${durls[$j]}"
    done
    exit 0 ;;
  *) exit 0 ;;
esac

REPO="$(find_repo)" || exit 0
[ -n "$REPO" ] || exit 0

url=$(printf '%s' "$input" | jq -r '.tool_input.url // .tool_input.prompt // empty' 2>/dev/null) || exit 0
[ -n "$url" ] || exit 0
host=$(url_host "$url")
[ -n "$host" ] || exit 0
browser_ok_open && exit 0
url_exempt "$url" && exit 0
working=$(recipes_for "$url" "$host")
[ -n "$working" ] || exit 0
block_covered "$host" "$working"
