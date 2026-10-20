# usage TODO

## Own bugs

- **The tool is inert until phase 2.** Nothing is registered and no store exists.
  Jacob's steps: `./usage registrations --export` (writes
  `~/.claude/settings.json.proposed`, never the live file), review it, copy it
  over `~/.claude/settings.json`, then `./usage init`. Until then `status` and
  `gate` answer "no sample", and `gate` holds.
- **`.githooks/` is installed but inert**: `core.hooksPath` unset, and setting
  it before `./dev.sh check` runs `.githooks/check-pass record` would make the
  pre-commit refuse every commit (setup's `githooks needs-jacob` line). The
  parts runner (Q7) records nothing for check-pass yet; wiring it is setup's
  githooks component plus a `record` step here, not done. (The settings half
  is done: `.claude/settings.json` == setup's proposal, committed eed5a47, Q6b.)
- **`./dev.sh check` runs in parts (Q7, 2026-10-13)** through devtools/parts.py
  (setup's render of tools/checks' runner; 1.1.0 at ba7f6a6, 1.2.0 since
  a6e197d, byte-equal; test_parts `RunnerCopy` goes red whenever checks moves
  on: `setup tools/usage --only parts --rebuild`, which needs a clean tree and
  commits its render; red again 2026-10-19, checks parts.py 1.4.0 at 0be97db,
  reported to setup by tl-433: the tree is not clean, 4 .claude/ files changed
  by no session of this repo, likely a setup render, unverified). A gate that did not run at all (guard with no hook,
  shared with no tools/checks) exits 3 from devtools/check.py: runner 1.2.0's
  `UNCHK`, never ok, not red, re-run by --resume. A shared check UNCHECKED on
  its own (repro, stores-exported, ...) is a line of the shared part, exit 0.
  Lost: `--json` now has one `shared` entry, not one per shared check (the
  names are in `.progress/check.json.logs/shared.log`).
- **The hooks part is slow under load**: 900s timed out at load ~40 on
  2026-10-13 (run 2, no output: the hook tests print only at the end); now
  2400s. Probe: its time in a quiet run, `jq .last_duration_s` is suite-wide,
  so read the `hooks` line of `./dev.sh check`.
- **settings.json lacks 3 registrations again (2026-10-13)**: after setup's
  3934630 render, settings.proposed.json adds git-funnel.sh and store-guard.sh
  (x2); `cp .claude/settings.proposed.json .claude/settings.json` is Jacob's
  step, not taken here.
- **TODO import skipped (Q7, 2026-10-13)**: this TODO.md was not imported into
  the todo store: todo's importer is being fixed (dispatcher's brief).
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

- **Does `can-run --set` last only the 24h TTL? (2026-10-19, tl-355; ask
  Jacob.)** §16 step 2 puts every record under `probes.model_ttl_hours`, so a
  `--set claude-fable-5-1 ok` must be renewed daily or can-run answers
  `unknown` (and probes nothing). A plan rarely changes daily; a separate
  `set_ttl_hours` is one reviewed usage.json commit. Hinges on: Jacob.
- **`ok` needs a fresh `--set ok` AND a probe reply (2026-10-19, research
  rs-6).** A `claude -p` reply means ok or usage credits, so without the set no
  probe runs. If Jacob wants can-run to answer `ok` with no set, it needs a
  source that tells credits from included (none known). Hinges on: Jacob.

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

- **can-run's refusal texts are the docs', never run (2026-10-19, rs-6).**
  "issue with the selected model" -> refused, "spend limit" -> credits, a
  reset time -> hold; their exit code and stream (stdout result vs stderr)
  are undocumented, so both are read. Verified by a run: a haiku reply
  (stream-json, `message.model` = `claude-haiku-4-5-20251001`, read `reply`).
  Probe: one `usage can-run` on a plan without Fable, and one with credits
  spent (Jacob's plan includes Fable, so neither can be run here).
- **A first-turn hit may set no hold (unconfirmed).** The StopFailure input
  names no model, so the hold reads the transcript's last claude assistant
  message; if the transcript lags the failure, or the session's first request
  is the one refused, there is none and only the hits row is written (no
  failure traced: nothing failed). Probe: the next real `rate_limit` hit's
  transcript tail vs `models.json`.
- **Two concurrent `can-run` probes are not locked against each other**: each
  runs its own `claude -p` and the later write wins. Benign (both are fresh),
  costs one extra probe. A lock held across a 120 s call would block the hit
  reporter, so none is taken.
- **The probe timeout path (`probes.probe_timeout_s`) is untested**: holding it
  needs a test-only override of usage.json; the no-CLI test covers the same
  `unknown, nothing recorded` branch, not the timeout itself.

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

- **setup / hooks, 2026-10-09: hook-copy drift** -- settled 2026-10-13 (Q6b):
  setup's re-render committed c368cf1; `hooks copies --gate tools/usage` ok,
  25 of 25 agree in meaning; shared:hooks-installed ok.
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

## Usage bars in the hub (PLAN-routing-tree.md §27, tl-433)

- **Step 1 done 2026-10-19**: bars.json (session, week, fable-week), `usage
  bars [--json]`, the `bars` check gate, tests/test_bars.py.
- **The Fable probe, 2026-10-19 (the §27 report)**: one live sample,
  `jq '[.rate_limits | paths(scalars) | join(".")]' ~/.claude/usage/limits.json`
  (sampled_at 1792440880, under a minute old): `five_hour.resets_at`,
  `five_hour.used_percentage`, `seven_day.resets_at`,
  `seven_day.used_percentage`, all numbers. No per-model (Fable) window and no
  `spend_limit`. So fable-week is `{"pending": ...}`, always unknown with that
  reason, not live. `usage bars` (text) prints the latest keys and any no row
  names on every run: re-probe there. One sample on Opus; a sample taken while
  Fable runs could differ (unconfirmed: probe the same jq after a Fable session).
- **Open, step 2 (transcripts)**: a weekly share-by-model verb printing a JSON
  object; then fable-week's source becomes `{"service": "transcripts", "verb":
  ..., "key": ..., "timeout_s": ...}` and the `bars` gate checks the verb in
  transcripts' cli.json (it has none yet: the gate would fail such a row).
- **Open**: the plan says the registry is "the one place the widget and `usage
  status` read"; `status` still reads calc.latest() directly. Both read the
  same accessor and `AgreesWithStatus` holds them equal (number and staleness),
  so this is wording, not drift. A service verb's `key` is checked only at run
  time (unknown, with the reason), never by the gate: no service declares the
  keys it prints.

## SubagentStop's missing transcript is a skip, not a failure (tl-466, 2026-10-20)

- **Probed 2026-10-20**: failures.tsv held 1,093 rows since 2026-10-18, every
  one `stop SubagentStop FileNotFoundError` on
  `<session>/subagents/agent-<id>.jsonl`, 1,093 distinct paths; `ls` of each,
  hours to two days later: 0 exist (their folder holds 79 other agents'
  files). So these agents never write the file: nothing to read, nothing lost
  by the reporter. Now `stop()` counts one skip in `state/skips.json` (`usage
  failures` prints it) and returns; a main Stop with no transcript stays a
  failure. Counterfactual: the new `MissingTranscript` test, run on HEAD's
  reporters.py, fails on the failures.tsv row. Unconfirmed: whether those
  agents spend tokens no transcript records (which agents are they? the hook
  input's `agent_type` is not in failures.tsv; probe: `jq` the skipped
  SubagentStop inputs, which nothing records today).
- **failures.tsv is full (own bug, Jacob's step)**: 262,346 bytes, past the
  256 KiB cap, so since 2026-10-20T02:24:52Z no failure is recorded at all,
  and `usage status` says "write failures: 1093" until it is moved aside.
  Moving it is a write to the gated live store, not taken here:
  `mv ~/.claude/usage/failures.tsv ~/.claude/usage-failures-2026-10-20.tsv`.
