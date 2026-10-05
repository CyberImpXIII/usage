# usage TODO

## Own bugs

- **The tool is inert until phase 2.** Nothing is registered and no store exists.
  Jacob's steps: `./usage registrations --export` (writes
  `~/.claude/settings.json.proposed`, never the live file), review it, copy it
  over `~/.claude/settings.json`, then `./usage init`. Until then `status` and
  `gate` answer "no sample", and `gate` holds.
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
  rejecting `~`, accessor goes red here, which is the right signal.
- **Settled 2026-10-05: the ledgers ARE exported** (`usage export|import|verify`,
  usagelib/datarepo.py). samples/tokens/hits are history nothing can regenerate
  (calibrate's evidence, the %/token fallback; transcripts get pruned);
  limits.json, state/ and .lock are not. checks has no "declared-none" form for
  stores-exported (read source/stores-exported.py and its tests: only no cli.json
  is UNCHECKED), so that route did not exist anyway.
- **Who runs `usage export` after the reporters write? (open)** The ledgers grow
  every turn, so once DATA_REPO is set, stores-exported reads `differs` ("the
  store has N row(s) the export lacks") after any turn since the last export.
  §7.11's "export after every sanctioned write" (the write service) does not see
  hook writes, and a reporter must not write a data repo (fails open, no
  DATA_REPO in hook env). Options: a scheduled export, or export at session
  end by whatever commits the data repo. Hinges on: setup's `data`/`commit`
  components and Jacob. Not worked around by loosening verify.
- **Two machines sharing one `$DATA_REPO/usage/` (open).** Export is whole-file,
  so a second machine's export replaces the first's ledgers. Fine while usage
  runs on one machine; a per-host subfolder is the fix if it ever runs on two.
- **Mutation testing waits on a shared `checks mutants`** (tools/todo td-9)
  rather than a third copy of mutate.py here.
- **`--json` for `status` and `gate`** is not built; the hub (phase 4) decides
  whether it wants text or JSON. Hinges on: the hub plan's item 11.
- **Per-model weighting** is not applied: a token of any model counts the same
  toward %/token. Revisit if calibrate shows the ratio swinging with the model
  mix.

## Unconfirmed suspicions

- **The credential rule may refuse a real value (unconfirmed).** Export refuses
  any session/agent_type/model cell with a run of 20+ letters and digits (fails
  closed, named by file:line:column). Claude Code's session ids are hyphenated
  UUIDs (runs of 12) and models like `claude-opus-5-5` pass, but a hook input
  with an unhyphenated id would stop every export. Probe: the first real
  `usage export` after phase 2.

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
  True for `~/.claude/usage/x` (a `~` path read as repo-relative). (3), "no way
  to say run state, not exported", is moot here since the ledgers are exported
  (2026-10-05); it still matters for a store that is wholly run state.
