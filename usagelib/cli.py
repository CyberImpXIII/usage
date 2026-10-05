"""The `usage` CLI: reads the store, never the reporters' input.

HELP is the documented command list; README.md carries the same block, and
tests/test_docs.py holds README == HELP == the dispatch table == cli.json's
verbs, both ways. Its lines are indented two spaces because tools/checks reads
a CLI's commands from the indented lines of its help (accessor's
verb-implemented rule).
"""
import json
import os
import shlex
import subprocess
import sys
import time

from pathlib import Path

from . import calc, config, export, store

HELP = """\
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
"""


def _local(epoch_s, fmt="%a %H:%M"):
    return time.strftime(fmt, time.localtime(epoch_s)) if epoch_s is not None else "?"


def _opts(args, allowed):
    """--name value pairs -> dict; anything else is a usage error (exit 2)."""
    out, i = {}, 0
    while i < len(args):
        a = args[i]
        if a not in allowed or i + 1 >= len(args):
            raise SystemExit(_usage_error(f"unexpected argument: {a}"))
        out[a] = args[i + 1]
        i += 2
    return out


def _usage_error(msg):
    sys.stderr.write(f"usage: {msg}\n\n{HELP}")
    return 2


def _since(v):
    if v is None:
        return None
    if store.epoch(v + "T00:00:00Z") is None:
        raise SystemExit(_usage_error(f"--since wants YYYY-MM-DD, got {v!r}"))
    return v


def cmd_status(args):
    _opts(args, ())
    sample, at = calc.latest()
    if sample is None:
        print("no sample yet: the status line has not run with rate_limits (or the store is missing: usage init)")
        return 1
    age = (config.now() - at) / 60 if at is not None else None
    stale = config.setting("sample_stale_minutes")
    for label, key in calc.WINDOW_KEYS.items():
        p = sample.get(f"{key}_pct")
        r = sample.get(f"{key}_resets_at")
        if p is None:
            print(f"{label}: not reported")
            continue
        print(f"{label}: {p:.1f}%  resets {_local(r)}  (warn {config.setting('warn')}, hold {config.setting('hold')})")
    if age is None:
        print("sampled: unknown time")
    else:
        print(f"sampled {age:.0f} min ago" + (f" -- STALE (after {stale} min)" if age > stale else ""))
    return 0


def cmd_burn(args):
    o = _opts(args, ("--window",))
    wins = [o["--window"]] if "--window" in o else list(calc.WINDOW_KEYS)
    for w in wins:
        if w not in calc.WINDOW_KEYS:
            return _usage_error(f"--window is 5h or 7d, got {w!r}")
    for w in wins:
        b = calc.burn(w)
        if b is None:
            print(f"{w}: unknown (fewer than 2 samples in this window)")
            continue
        print(f"{w}: {b['pct_per_hour']:.2f} %/h, {b['tokens_per_hour']:,.0f} tokens/h over {b['n']} samples "
              f"({_local(b['t0'], '%H:%M')}-{_local(b['t1'], '%H:%M')}), now {b['last_pct']:.1f}%")
    return 0


def cmd_project(args):
    o = _opts(args, ("--threshold",))
    try:
        p = float(o.get("--threshold", config.setting("hold")))
    except ValueError:
        return _usage_error("--threshold wants a number")
    for w in calc.WINDOW_KEYS:
        kind, at = calc.project(w, p)
        text = {"at": f"reaches {p:g}% at {_local(at)} ({store.iso(at)})",
                "already": f"already at or past {p:g}%",
                "never": f"not rising: never reaches {p:g}% at this burn",
                "resets-first": f"resets first, at {_local(at)} ({store.iso(at) if at else '?'})",
                "unknown": "unknown (fewer than 2 samples in this window)"}[kind]
        print(f"{w}: {text}")
    return 0


def cmd_gate(args):
    if len(args) != 1 or not args[0].isdigit():
        return _usage_error("gate <tokens>, a whole number")
    code, line, detail = calc.gate(int(args[0]))
    print(line)
    for d in detail:
        print(f"  {d}")
    return code


def cmd_hits(args):
    o = _opts(args, ("--since",))
    rows = calc.hits(_since(o.get("--since")))
    if not rows:
        print("no rate-limit hits recorded")
        return 0
    for h in rows:
        pct = f"{h['pct']:.1f}%" if h["pct"] is not None else "--"
        age = f"{h['age_min']:.0f} min before" if h["age_min"] is not None else "no sample"
        flag = {"below-hold": f"  BELOW HOLD {config.setting('hold')}%", "stale-sample": "  stale sample",
                "no-sample": "  no sample", "": ""}[h["flag"]]
        print(f"{h['time']}  {h['session'][:8] or '-':8}  {h['agent_type'] or '(none)':18}  {pct:>6} ({age}){flag}")
    return 0


def cmd_tokens(args):
    o = _opts(args, ("--since", "--by"))
    by = o.get("--by", "agent")
    if by not in ("agent", "model", "session"):
        return _usage_error(f"--by is agent, model or session, got {by!r}")
    acc = calc.tokens_by(by, _since(o.get("--since")))
    if not acc:
        print("no token rows recorded")
        return 0
    print(f"{by:20} {'rows':>5} {'input':>10} {'output':>10} {'c-read':>12} {'c-write':>10} {'total':>12}")
    for k, a in sorted(acc.items(), key=lambda kv: -kv[1]["total"]):
        print(f"{(k or '(none)')[:20]:20} {a['rows']:>5} {a['input']:>10,.0f} {a['output']:>10,.0f} "
              f"{a['cache_read']:>12,.0f} {a['cache_write']:>10,.0f} {a['total']:>12,.0f}")
    return 0


def cmd_calibrate(args):
    _opts(args, ())
    _, lines = calc.calibrate()
    for ln in lines:
        print(ln)
    return 0


def cmd_check(args):
    return subprocess.call([sys.executable, str(config.TOOL / "devtools" / "check.py"), *args])


def cmd_init(args):
    _opts(args, ())
    d = config.store_dir()
    d.mkdir(parents=True, exist_ok=True)
    print(f"store: {d}")
    return 0


def cmd_registrations(args):
    if not args:
        print(json.dumps(export.registrations(), indent=2))
        return 0
    if args[0] != "--export" or len(args) > 2:
        return _usage_error(f"registrations takes nothing or --export [FILE], got {' '.join(args)!r}")
    path = Path(args[1]).expanduser() if len(args) == 2 else export.default_file()
    proposal = path.with_name(path.name + export.SUFFIX)
    try:
        status, report, target = export.export(path)
    except export.ExportError as e:
        left = f"; the existing {proposal} was left as it was" if proposal.exists() or proposal.is_symlink() else ""
        sys.stderr.write(f"usage registrations --export: {path} {e}: no proposal written{left}\n")
        return 1
    print(f"{status}: {target}  (from {path}, which is not touched)")
    for line in report:
        print(f"  {line}")
    print(f"next (Jacob): diff {shlex.quote(str(path))} {shlex.quote(str(target))}; "
          f"cp {shlex.quote(str(target))} {shlex.quote(str(path))}; usage init")
    return 0


COMMANDS = {
    "status": cmd_status, "burn": cmd_burn, "project": cmd_project, "gate": cmd_gate,
    "hits": cmd_hits, "tokens": cmd_tokens, "calibrate": cmd_calibrate, "check": cmd_check,
    "init": cmd_init, "registrations": cmd_registrations,
}


def main(argv):
    if not argv or argv[0] in ("-h", "--help", "help"):
        sys.stdout.write(HELP)
        return 0
    fn = COMMANDS.get(argv[0])
    if fn is None:
        return _usage_error(f"unknown command {argv[0]!r}")
    if argv[0] == "check":
        return fn(argv[1:])
    try:
        return fn(argv[1:])
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else 2
    except BrokenPipeError:
        os._exit(0)
