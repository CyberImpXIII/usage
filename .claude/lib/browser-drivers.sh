#!/usr/bin/env bash
# hooks: applies_to=all dest=.claude/lib
# SOURCED, not executed. The browser drivers a Bash call can run, and which of
# them are cleared: the DATA prefer-recipes.sh reads for its Bash branch
# (tl-562, PLAN-services.md §4 step 9.9 step 4). The hook holds no tool name,
# address or clearance of its own; every one is a row here.
# Its source is tools/hooks (source/lib/); `hooks copies` there fails when a
# copy differs from it. Change the source, never a copy.
# Tests: bash tools/hooks/source/tests/test-browser-drivers.sh (the rows);
# source/hooks/test-prefer-recipes.sh (how the hook uses them).
#
#   browser_drivers   prints one row per line, three tab-separated fields:
#     kind  tool  value
#     cli      <tool> <command>    a command word that drives <tool> (its
#                                  basename, after VAR=x, sudo, env, ...)
#     api      <tool> <host:port>  an address of <tool>'s local HTTP API; a
#                                  call from an `http-client` naming it drives
#                                  <tool>
#     http-client -   <command>    a command whose call to an `api` address
#                                  is a request to that API
#     launcher -      <command>    runs a package by name (npx pinchtab,
#                                  uvx browser-use): the hook reads the first
#                                  operand after it, `@version`/`[extra]` cut
#     launcher-verb - <word>       a word between a launcher and the package
#                                  (`uv run`, `pipx run`, `pnpm dlx`), skipped
#     cleared  <tool> <record>     <tool> is cleared by PLAN-services.md §4
#                                  step 9.9's step 1 and Jacob's yes on step 2;
#                                  <record> is where that is written (a todo
#                                  id, a plan line). No row: NOT cleared, and
#                                  every Bash call driving it is blocked in
#                                  this workspace, browser-ok or not. Once
#                                  cleared, it is blocked only on a host a
#                                  `working` recipe covers, as WebFetch is.
#
# No tool is cleared on 2026-10-23: rs-27 answered step 1 "attach mode only"
# for pinchtab, read and not run, and step 2's wrapper test (which settles it)
# has not run. A clearance row is added here, by this repo, when that record
# exists, and never to open a door for one run (that is `./dev.sh browser-ok`,
# which a cleared tool honours and an uncleared one does not).
#
# Misses, on purpose or not yet seen: pinchtab instances on other ports
# (`--server http://localhost:9868` is still the pinchtab CLI, so it is
# caught; a curl to 9868 is not); a script that imports browser_use or
# calls the API from Python or Node; a command built at run time (eval,
# xargs, an alias). Add a row when one is seen in use.

browser_drivers() {
  printf '%s\t%s\t%s\n' \
    cli           pinchtab    pinchtab \
    cli           browser-use browser-use \
    api           pinchtab    127.0.0.1:9867 \
    api           pinchtab    localhost:9867 \
    api           pinchtab    '[::1]:9867' \
    http-client   -           curl \
    http-client   -           wget \
    http-client   -           http \
    http-client   -           https \
    http-client   -           xh \
    launcher      -           npx \
    launcher      -           bunx \
    launcher      -           uvx \
    launcher      -           pipx \
    launcher      -           uv \
    launcher      -           pnpm \
    launcher      -           npm \
    launcher-verb -           run \
    launcher-verb -           tool \
    launcher-verb -           dlx \
    launcher-verb -           exec \
    launcher-verb -           x
}
