# usage TODO

## Own bugs

- **No GitHub repo yet.** Built 2026-10-05 as a local repo with no `origin`;
  creating it (`setup --github`, PLAN-usage-reporting.md §5 phase 1) waits on
  Jacob's yes. Until then commits stay local and nothing is pushed.
- **The tool is inert until phase 2.** Nothing is registered and no store exists:
  Jacob wires the output of `usage registrations` (status line, Stop,
  SubagentStop, StopFailure) and runs `usage init`. Until then `status` and
  `gate` answer "no sample", and `gate` holds.
- **`state/` grows with each transcript** (one file per transcript, a per-message
  map inside). Nothing prunes it. Harmless at our volume; a `usage prune` is the
  fix if it ever matters.

## Open decisions

- **Where the registrations reach a settings proposal.** Plan §7 says setup's
  component writes them into `settings.proposed.json`; setup refuses a path
  that is not a repo, and the user-level settings these belong in are not a
  repo. Built: the tool only prints them (`usage registrations`); the plan's
  author decides who merges them (setup component, the delegation layer's
  wiring file, or Jacob by hand). Hinges on: which owner holds user-level
  settings proposals.
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
- **Same owner and setup's owner, 2026-10-05:** the registrations (decision
  above), and `CHECKS_ROSTER_NAMES` for this repo's check, so no-roster's names
  half runs here instead of reporting UNCHECKED.
