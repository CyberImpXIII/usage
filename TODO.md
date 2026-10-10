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

- **The store is gated in code, not yet live (V1 partial).** `usage init`
  writes `<store>/cli.json` (V1, 2026-10-09). Setup installed
  `.claude/hooks/store-guard.sh` here (5954975), and the `guard` gate ran it:
  OK, 2 of 2 (Write refused with exit 2 after init, 0 before; `echo >>`
  likewise). Live, on 2026-10-09: `~/.claude/usage` does not exist (phase 2),
  and store-guard.sh is named in no settings.json (this repo's, the
  workspace's, `~/.claude`), nor installed in the workspace's `.claude/hooks/`.
  So V1 is **partial**: it waits on Jacob's `./usage init` and the hook's
  registration (V2, setup; settings are Jacob's).
- **hits.tsv gained two columns (2026-10-09)**: `five_hour_resets_at`,
  `seven_day_resets_at`, for `hits --json`'s `resets_at`. An existing hits.tsv
  with the old 6-column header is now refused (HeaderMismatch, recorded in
  failures.tsv), never appended to under the wrong header. No store exists yet
  (phase 2), so nothing migrates; if one does, rename the old file aside. The
  export format went 1 -> 2, so an old export is refused by import/verify.
- **Not traced**: the interpreter failing to start (`python3` missing: the .sh
  wrapper exits 0 with nothing written) and both trace places refusing
  (store and its parent read-only). Each still fails open (`FailureTrace`).
  `failures.tsv` stops at 256 KiB; nothing rotates it.

## Open decisions

- **`size_tokens` is a first value, not a measurement (2026-10-09, item 7).**
  usage.json `size_tokens` S 4,000,000, M 12,000,000 (input + output + cache
  read + cache write, the gate's sum) came from one session's totals (main opus
  ~4.75M, see the dedupe note below), not from sized items. Probe: once items
  carry sizes and handoffs, `usage tokens --by session` per dispatch grouped by
  the item's size; a reviewed commit moves the values. Hinges on: sized items
  existing, and the hub (which calls `gate --todo`).
- **`hits --json`'s `resets_at` is strict (2026-10-09)**: given only when the
  hit's sample is fresh, exactly one window is at or past `hold` and its reset
  is after the hit; else null (both high, neither, stale, no reset). A refinement
  (e.g. the window nearer 100%, or the StopFailure error text if it ever names
  the window) waits on real hits. Hinges on: the first `usage hits` after phase 2.
- **`cli.json` vs two tools/checks rules (open, reported 2026-10-05).** §9 wants
  the verbs declared in `cli.json`; the store is outside the repo
  (`~/.claude/usage/`), which `schema/cli.schema.json` cannot express
  (paths-inside). Declared as `~/.claude/usage/<ledger>`: checks' `inside()`
  accepts a `~`-path as repo-relative, so accessor passes and its part-3 audit
  (no file outside usagelib/ names a ledger) really runs. If checks starts
  rejecting `~`, accessor goes red here, which is the right signal. Still open
  2026-10-09 (V1): it did not block; the store's own cli.json (absolute) is
  what the store hook reads.
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
- **Rule in one place: should `scan()` call tools/transcripts' reader instead
  of copying it? (open, raised 2026-10-05).** Against: scan is incremental
  (byte offset + per-id `seen`, emitting deltas so a later Stop adds only what
  grew), runs inside a fail-open hook under the store lock, and splits
  turn/backlog; tokens.jq reads a whole transcript and keeps no state. Calling
  it would need a sibling-repo path from a hook (the fixed-path seam CLAUDE.md
  records as a failure) and re-reading the whole file each Stop. For: one
  rule, no drift. Leaning: keep the copy, gated -- tools/transcripts'
  `tests/test_cli.py UsageSeam` runs tokens.jq on this repo's fixture and
  compares with expected.json (ran 2026-10-06 from tools/transcripts: 1 test,
  OK, not skipped). It gates the fixture only, not scan() on live transcripts.
  Hinges on: Jacob and the transcripts owner.
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
- **The second copy of the dedupe rule: SAME rule, checked 2026-10-05 (no
  longer a suspicion; the drift risk stays, see the open decision).**
  `reporters.scan()` and tools/transcripts/lib/tokens.jq both count each
  message once, keyed `message.id` else `uuid`, at the max of each field,
  model starting `claude`. Run: a scratch script calling `scan(f, {}, 0)` and
  `tokens.jq --arg by model` on each file, summing scan's bases per model.
  Session 5e711721 (main + 4 subagent transcripts): all 5 identical per model,
  e.g. main opus-5 in 110 out 56426 cr 4588794 cw 105595, haiku in 161 out
  8543 cr 1447259 cw 96534. Every finished transcript in the project (44):
  0 differ; summing every line without dedupe differs in all 44, so the
  comparison discriminates. The fixture still gives expected.json's totals.
  Differences by reading only, never seen in data: `message.id == ""` (jq
  groups all of those under "", scan falls back to uuid); a float or string
  token count (scan's `int()` truncates or throws, failing open; jq keeps it);
  a model change within one id (jq takes the first line's, scan each line's).
  expected.json's comment now points at tools/transcripts/lib/tokens.jq
  (2026-10-06).

## Reported to other owners

- **setup / hooks, 2026-10-09 (via the dispatcher): `./dev.sh check` FAILs
  `shared:hooks-installed`** with drift in `.claude/lib/extra-stores.sh`,
  `.claude/hooks/store-guard.sh`, `test-store-guard.sh`,
  `test-no-inline-blobs.sh`: tools/hooks moved on (b7a8c6b, ec06599) after
  this repo's re-render at eb09868 (5954975). The re-render is setup's;
  not copied by hand here. Also an uncommitted `.claude/settings.json` (not
  ours, same diff shape in tools/hooks, setup, checks): left untouched.
- **todo, 2026-10-09 (via the dispatcher): no CLI prints the size
  vocabulary**, so `VocabSeam` (size_tokens == todo's `ready` sizes) reads
  tools/todo/vocab.json directly, read only. A `todo vocab sizes --json`
  would let it go through the CLI.

- **harness, 2026-10-09 (via the dispatcher): `usage hits --json` has landed**
  (the ask in .claude/TODO.md). `tests/test_questions.py HitsJson
  test_the_consumer_reads_it` runs `.claude/lib/usage-hits.sh` against this
  CLI: OK. No store is exit 1, never `[]`.
- **dispatcher, 2026-10-09:** the V1 brief said "no origin yet, so no push";
  origin exists (github.com/CyberImpXIII/usage) and the commit was pushed.
- **The `.claude/` layer's owner and setup's owner, 2026-10-05:** `CHECKS_ROSTER_NAMES` for this
  repo's check, so no-roster's names half runs here instead of reporting
  UNCHECKED. (The registrations question is settled: §9, `--export`.)
- **tools/checks' owner, 2026-10-05 (via the dispatcher):** (1) `cli.schema.json`
  has no way to declare a store outside the repo; (2) `contract.inside()` returns
  True for `~/.claude/usage/x` (a `~` path read as repo-relative). (3), "no way
  to say run state, not exported", is moot here since the ledgers are exported
  (2026-10-05); it still matters for a store that is wholly run state.
