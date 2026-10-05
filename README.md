# usage

How close the plan's rate-limit windows are, recorded as they move, so a job
can be held before it would hit a limit rather than failing at it. Built from
PLAN-usage-reporting.md (phase 1); the thresholds are its §8, approved
2026-10-05.

Three small scripts write, one CLI reads, and the store lives outside every
repo. No script runs a model: the cost is a few milliseconds of Python per event.

| script | registered as | writes |
|---|---|---|
| `usage-statusline.sh [command]` | the status line | `limits.json` (the latest sample and the raw `rate_limits` object) and a row in `samples.tsv` when a value changes or every `sample_heartbeat_minutes`; prints `5h 42% (resets 14:05) \| 7d 18% (resets Mon 09:00)`, with ` warn` / ` HOLD` past a threshold. Given a command, runs it on the same input and keeps its output in front of ours. |
| `usage-stop.sh` | `Stop`, `SubagentStop` | one row per model in `tokens.tsv`: the turn's delta since the last run for that transcript (SubagentStop reads `agent_transcript_path`) |
| `usage-failure.sh` | `StopFailure`, matcher `rate_limit` | one row in `hits.tsv` with the last sample before it; any other error writes nothing |

## Commands

```
usage status                   latest 5h and 7d %, resets, time since the sample
usage burn [--window 5h|7d]    % per hour over the last N samples (default 12), and tokens per hour from tokens.tsv
usage project [--threshold P]  at the current burn, when each window reaches P (default: hold)
usage gate <tokens>            exit 0 if <tokens> fit before the window's reset under the burn so far;
                               exit 1 with "hold until <time>" otherwise. The hub's question.
usage hits [--since D]         rate-limit hits with the sample that preceded each; flags one below the hold threshold
usage tokens [--since D] [--by agent|model|session]   tokens per agent type, model or session, from the ledger instead of the transcripts
usage calibrate                proposes thresholds from the hits so far; never applies them
usage check [--json]           the gates (devtools/check.py; the same as ./dev.sh check)
usage init                     create the store folder (~/.claude/usage/, or $USAGE_STORE); the reporters write nothing without it
usage registrations            the status line and three hook registrations, as JSON, for a settings proposal (never written here)
```

`tests/test_docs.py` holds this block equal to the CLI's help and to its
dispatch table, both ways, and checks every command the plan lists exists.

## The gate

`usage gate <tokens>` answers one question: does a job of this many tokens fit?

- **%/token** is measured, not assumed: the rise of a window's percentage over
  the `turn` tokens recorded in the same span, from the current window when it
  shows both, else from every window on record. With neither, the answer is
  `unknown, hold` (exit 1).
- A job's tokens are input + output + cache read + cache write, the same sum the
  ratio is measured in.
- **hold** (exit 1, `hold until <reset, UTC>`): a window is already at `hold`,
  or would reach it after the job. **pass, warn** (exit 0): past `warn` after the
  job. **pass** (exit 0): below both.
- **A stale sample holds:** no sample, or one older than `sample_stale_minutes`,
  gives `unknown, hold` (exit 1). A stale number is a wrong answer.

Weighting by model is not applied: a token of any model counts the same toward
the ratio (TODO.md).

## The store

`~/.claude/usage/` (`$USAGE_STORE` overrides it; the tests use that). Created
only by `usage init`: without the folder the reporters write nothing, so the
tool is inert until someone sets it up.

| file | columns / content |
|---|---|
| `samples.tsv` | `time` (UTC ISO), `five_hour_pct`, `seven_day_pct`, `five_hour_resets_at`, `seven_day_resets_at` (epoch seconds, as received) |
| `tokens.tsv` | `time`, `session`, `agent_type` (from the hook input, empty on the main thread), `model`, `input`, `output`, `cache_read`, `cache_write`, `basis`: `turn`, or `backlog` for messages older than `sample_stale_minutes` when first read (a transcript first seen mid-session), which never count toward a ratio |
| `hits.tsv` | `time`, `session`, `agent_type`, `five_hour_pct`, `seven_day_pct`, `sample_time` (when that sample was taken) |
| `limits.json` | the latest sample, when it was taken, and the raw `rate_limits` object whole |
| `state/` | one file per transcript: the byte offset read so far and each message's counted usage |

Each message is counted once, at the maximum of each usage field across its
streamed lines; `<synthetic>` and non-JSON lines are skipped; a partial last line
waits for its newline. Every write holds `.lock` (flock).

## Fails open

Truncated or absent input, a missing store, a read-only or full disk: each
script exits 0 and writes nothing, and the status line still prints a chained
command's output. `tests/test_reporters.py` (`FailsOpen`) runs each case, with
the counterfactual that the same input writes to a good store.

## Registration

`usage registrations` prints the status line and the three hooks as JSON, with
absolute paths to this folder. This tool never writes a settings file; whoever
owns the settings proposal merges it, and Jacob wires it. Then `usage init`.

## Configuration

`usage.json`: `warn` 75, `hold` 90, `sample_stale_minutes` 15 (approved),
`burn_samples`, `sample_heartbeat_minutes`, `calibrate_margin`. Read only through
`usagelib/config.py`'s `setting()`; `tests/test_config.py` holds the keys equal to
the names read. `usage calibrate` proposes `hold` = the lowest hit that arrived
below `hold` with a fresh sample, floored, minus `calibrate_margin`, and keeps
`warn`'s gap; it never edits the file.

## Checks

`./dev.sh check [--json] [GATE...]`: `test` (the suite), `files` (scripts present,
executable, parse), `hooks` (the shared hook copies pass their tests), `shared`
(`tools/checks`, UNCHECKED when that sibling is absent). `checks.json` allows the
names `hooks` (the settings key and the `.claude/hooks/` folder) and `checks`
(the sibling tool) in no-roster's names half, which runs only when the caller
exports `CHECKS_ROSTER_NAMES`; run with the full roster on 2026-10-05, it was clean.
