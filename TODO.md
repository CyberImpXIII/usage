# usage TODO

## Own bugs

- **The tool is inert until phase 2.** Nothing is registered and no store exists.
  Jacob's steps: `./usage registrations --export` (writes
  `~/.claude/settings.json.proposed`, never the live file), review it, copy it
  over `~/.claude/settings.json`, then `./usage init`. Until then `status` and
  `gate` answer "no sample", and `gate` holds.
- **`shared:hooks-installed` is red on 3 drifted copies** (setup run,
  PLAN-repo-setup §7.12 step 2, 2026-10-05): setup installed the 11 hooks,
  their tests and `.claude/lib/{ledger,write-targets}.sh`, and left
  `.claude/hooks/{troubleshooting,test-troubleshooting,test-prefer-recipes}.sh`
  byte-unchanged as `drift` (logic differs from tools/hooks/source;
  `hooks copies` agrees). Replacing them (`--rebuild`) waits on Jacob.
- **Setup's other outputs wait on Jacob** (same run): `.claude/settings.json`
  lacks 12 registrations; the proposal is `.claude/settings.proposed.json`
  (gitignored), applied by `cp` over settings.json, Jacob's step. `.githooks/`
  is installed but inert: `core.hooksPath` unset, and setting it before
  `./dev.sh` runs `.githooks/check-pass record` would make the pre-commit
  refuse every commit (setup's `githooks needs-jacob` line).
- **`state/` grows with each transcript** (one file per transcript, a per-message
  map inside). Nothing prunes it. Harmless at our volume; a `usage prune` is the
  fix if it ever matters.

## Open decisions

- **`cli.json` vs two tools/checks rules (open, reported 2026-10-05).** §9 wants
  the verbs declared in `cli.json`; the store is outside the repo
  (`~/.claude/usage/`), which `schema/cli.schema.json` cannot express
  (paths-inside). Declared as `~/.claude/usage/<ledger>`: checks' `inside()`
  accepts a `~`-path as repo-relative, so accessor passes and its part-3 audit
  (no file outside usagelib/ names a ledger) really runs. If checks starts
  rejecting `~`, accessor goes red here, which is the right signal. Second: the
  in-progress (uncommitted on 2026-10-05 03:20) `stores-exported` check fails any
  cli.json without a `verify` verb; this store is per-machine run state with no
  data-repo export (PLAN-repo-setup.md §7.11 lists run state as not exported), so
  `./dev.sh check` is red on `shared:stores-exported` until checks can tell run
  state from an exported store. Hinges on: checks' owner (schema field for an
  outside / run-state store). Not worked around with an empty `verify` verb.
- **Mutation testing waits on a shared `checks mutants`** (tools/todo td-9)
  rather than a third copy of mutate.py here.
- **`--json` for `status` and `gate`** is not built; the hub (phase 4) decides
  whether it wants text or JSON. Hinges on: the hub plan's item 11.
- **Per-model weighting** is not applied: a token of any model counts the same
  toward %/token. Revisit if calibrate shows the ratio swinging with the model
  mix.

## Unconfirmed suspicions

- **The §1 unknowns, unsettled until the first real sample** (`limits.json`
  keeps the raw `rate_limits` object whole to settle them): whether a separate
  weekly limit (Fable) appears as its own window; what exactly each window
  carries; whether cloud sessions report `rate_limits` at all; whether
  `resets_at` stays constant within a window (the instance split assumes a drop
  in %, or passing `resets_at`, marks a new window). Probe: `jq .rate_limits
  ~/.claude/usage/limits.json` after phase 2.
- **A subagent's last message may be missed**: the hooks docs say
  `transcript_path` can lag the conversation. The next Stop for that
  transcript picks it up only if one comes; a SubagentStop has no next. Probe:
  compare `usage tokens --by agent` with the transcript reader's per-agent
  totals for a day after phase 2.
- **%/token overestimates while some usage goes unrecorded** (sessions on
  another machine, claude.ai chat, sessions started before the hooks were
  wired): the window rises for tokens this ledger never saw. The gate errs
  toward holding, which is the safe side. Probe: `usage calibrate` after a
  week (phase 3).
- **This scan is a second copy of the transcript reader's dedupe rule** (count
  each message once, at the max of each usage field across its streamed lines,
  id or uuid, skip `<synthetic>`). The fixture's totals were recorded once from
  that reader by hand (tests/fixtures/transcript/expected.json, 2026-10-05:
  in 18, out 755, cache read 82000, cache write 6850, 5 messages); a change
  there would not fail here. Settle: the cross-check reported below.

## Reported to other owners

- **The `.claude/` layer's owner, 2026-10-05:** add a test there that runs its
  transcript reader (`tokens.jq`) on `tools/usage/tests/fixtures/transcript/`
  (turn1-3 concatenated) and compares with `expected.json`'s `by_model`, so the
  two copies of the dedupe rule cannot drift. This tool may not read that layer.
- **Same owner and setup's owner, 2026-10-05:** `CHECKS_ROSTER_NAMES` for this
  repo's check, so no-roster's names half runs here instead of reporting
  UNCHECKED. (The registrations question is settled: §9, `--export`.)
- **tools/checks' owner, 2026-10-05 (via the dispatcher):** (1) `cli.schema.json`
  has no way to declare a store outside the repo; (2) `contract.inside()` returns
  True for `~/.claude/usage/x` (a `~` path read as repo-relative); (3)
  `stores-exported` (uncommitted then) requires `verify` on every cli.json, with
  no way to say "run state, not exported". Decision above.
