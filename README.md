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
  usage registrations [--export [FILE]]   the status line and three hook registrations, as JSON; --export copies FILE
                                 (default ~/.claude/settings.json) with them added to FILE.proposed and never writes FILE
  usage export [--json]          write the ledgers (samples, tokens, hits; never limits.json or state/) into $DATA_REPO/usage/
  usage import [--json]          recreate the ledgers from $DATA_REPO/usage/ when the store holds no row, then verify
  usage verify [--json]          compare the store with $DATA_REPO/usage/: same, differs or missing per item; exit 1 unless all same
```

`cli.json` declares the same commands as its verbs (PLAN-routing-tree.md §14.8;
tools/checks' `accessor` runs `usage help` and requires each verb listed).
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

## The export (data repo)

PLAN-repo-setup.md §7.11: the store stays the one source, and `$DATA_REPO/usage/`
holds its serialised form, so a fresh machine can get its history back
(`usage import`) and `tools/checks`' `stores-exported` can tell whether the
export is current (`usage verify --json`). `usagelib/datarepo.py` holds the
contract; in short:

| store file | exported? | why |
|---|---|---|
| `samples.tsv`, `tokens.tsv`, `hits.tsv` | yes, as the reader reads them | history: what %/token, `burn` and `calibrate` are measured from, and nothing can regenerate it |
| `limits.json` | no | the latest sample, stale in minutes; a restored copy would be a wrong answer |
| `state/` | no | this machine's transcript offsets, keyed by local paths |
| `.lock` | no | the write lock |

Every cell must fit its column's shape, and the free-text columns (session,
agent_type, model) may hold no run of 20+ letters and digits: a misfit stops the
export or the import, named by file, line and column, never by value. Import
never writes into a store whose ledgers hold a row. Nothing here sets
`DATA_REPO`: the caller supplies it. `tests/test_datarepo.py` holds each of
these, in scratch folders only.

## Fails open

Truncated or absent input, a missing store, a read-only or full disk: each
script exits 0 and writes nothing, and the status line still prints a chained
command's output. `tests/test_reporters.py` (`FailsOpen`) runs each case, with
the counterfactual that the same input writes to a good store.

## Registration

`usage registrations` prints the status line and the three hooks as JSON, with
absolute paths to this folder (`usagelib/export.py`'s `registrations()`, the one
source). `usage registrations --export [FILE]` (PLAN-usage-reporting.md §9) reads
FILE, default `~/.claude/settings.json`, adds them, and writes a static copy
beside it, `FILE.proposed`. **FILE itself is never written**: Jacob reviews the
proposal, copies it over FILE, then runs `usage init`.

What the export guarantees, or it writes nothing and exits 1:
- FILE parses as one JSON object with no key given twice (a parser would keep
  only one, so the copy would silently lose the other);
- every key of FILE is in the copy with its value; lists only grow at the end.
  The one rewrite: an existing foreign `statusLine.command` is chained behind
  ours (`usage-statusline.sh '<old command>'`, which runs it and keeps its line),
  checked exactly;
- each registration is in the copy once. An already-registered FILE (the
  applied proposal) exports byte-identical; a registration of the same script
  from another location, or twice, is refused rather than guessed at;
- `FILE.proposed`, if present, is a regular file (a link is refused).

An existing proposal is left alone when identical (`unchanged`) and replaced
otherwise (`replaced`): it is derived from FILE, and an older one would undo
whatever changed in FILE since. On an error an earlier proposal is left as it
was, and the message says so. The copy keeps FILE's permission bits, is
re-indented to two spaces, and the output names keys, never values.
`tests/test_export.py` holds each of these.

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
