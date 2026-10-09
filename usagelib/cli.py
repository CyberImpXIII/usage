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

from . import calc, config, datarepo, export, store

HELP = """\
  usage status                   latest 5h and 7d %, resets, time since the sample
  usage burn [--window 5h|7d]    % per hour over the last N samples (default 12), and tokens per hour from tokens.tsv
  usage project [--threshold P]  at the current burn, when each window reaches P (default: hold)
  usage gate <tokens>            exit 0 if <tokens> fit before the window's reset under the burn so far;
                                 exit 1 with "hold until <time>" otherwise. The hub's question.
  usage hits [--since D] [--json]   rate-limit hits with the sample that preceded each; flags one below the hold threshold.
                                 --json: [{"t", "session", "resets_at", ...}], resets_at null unless one window was at hold
  usage failures [--json]        the reporters' failed writes (rows they could not record), from failures.tsv in or beside the store
  usage tokens [--since D] [--by agent|model|session]   tokens per agent type, model or session, from the ledger instead of the transcripts
  usage calibrate                proposes thresholds from the hits so far; never applies them
  usage check [--json]           the gates (devtools/check.py; the same as ./dev.sh check)
  usage init                     create the store folder (~/.claude/usage/, or $USAGE_STORE) and its cli.json (the store hook's
                                 gate: a hand write to a ledger is refused); the reporters write nothing without the folder
  usage registrations [--export [FILE]]   the status line and three hook registrations, as JSON; --export copies FILE
                                 (default ~/.claude/settings.json) with them added to FILE.proposed and never writes FILE
  usage export [--json]          write the ledgers (samples, tokens, hits; never limits.json or state/) into $DATA_REPO/usage/
  usage import [--json]          recreate the ledgers from $DATA_REPO/usage/ when the store holds no row, then verify
  usage verify [--json]          compare the store with $DATA_REPO/usage/: same, differs or missing per item; exit 1 unless all same
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
        _store_notes()
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
    _store_notes()
    return 0


def _store_notes():
    """What `status` adds about the store itself: an ungated store, and
    recorded write failures (rows the reporters lost)."""
    gate = store.contract_state(CLI_PATH, COMMANDS)
    if gate in ("missing", "differs"):
        print(f"store gate: {store.CONTRACT} {gate} in {config.store_dir()} -- run `usage init`")
    fails = store.read_failures()
    if fails:
        print(f"write failures: {len(fails)} recorded, latest {fails[-1]['time']} ({fails[-1]['reporter']}) "
              "-- `usage failures`")


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


HIT_JSON = ("t", "session", "resets_at", "window", "time", "agent_type", "five_hour_pct", "seven_day_pct",
            "sample_time", "flag")


def cmd_hits(args):
    as_json = "--json" in args
    o = _opts([a for a in args if a != "--json"], ("--since",))
    since = _since(o.get("--since"))
    if as_json:
        # no store is "not known", never "no hits": exit 1, nothing on stdout
        if not config.store_dir().is_dir():
            sys.stderr.write(f"usage hits: no store at {config.store_dir()} (usage init): hits not known\n")
            return 1
        print(json.dumps([{k: h[k] for k in HIT_JSON} for h in calc.hits(since)]))
        return 0
    rows = calc.hits(since)
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


def cmd_failures(args):
    as_json = _json_flag(args)
    rows = store.read_failures()
    if as_json:
        print(json.dumps(rows, indent=2))
        return 0
    if not rows:
        print("no write failures recorded")
        return 0
    for r in rows:
        print(f"{r['time']}  {r['reporter'] or '-':10}  {r['event'] or '-':13}  {r['error']}")
    places = sorted({r["where"] for r in rows})
    print(f"{len(rows)} failure(s), in {', '.join(places)}")
    return 0


def cmd_init(args):
    _opts(args, ())
    d = config.store_dir()
    d.mkdir(parents=True, exist_ok=True)
    print(f"store: {d}")
    print(f"gate: {d / store.CONTRACT} {store.write_contract(CLI_PATH, COMMANDS)}")
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


# --- export / import / verify (PLAN-repo-setup.md §7.11; contract: usagelib/datarepo.py)

def _json_flag(args):
    if args not in ([], ["--json"]):
        raise SystemExit(_usage_error(f"takes nothing or --json, got {' '.join(args)!r}"))
    return bool(args)


def _fail(as_json, verb, msg, findings=()):
    """Exit 2. Findings name places, never values (datarepo.problems)."""
    if as_json:
        print(json.dumps({"error": msg, **({"findings": list(findings)} if findings else {})}, indent=2))
    else:
        sys.stderr.write(f"usage {verb}: {msg}\n" + "".join(f"  {f}\n" for f in list(findings)[:10]))
        if len(findings) > 10:
            sys.stderr.write(f"  ... {len(findings) - 10} more\n")
    return 2


def _data_dir(as_json, verb):
    repo = os.environ.get("DATA_REPO")
    if not repo:
        return None, _fail(as_json, verb, "DATA_REPO is not set: no data repo (the caller supplies it)")
    if not os.path.isdir(repo):
        return None, _fail(as_json, verb, f"DATA_REPO is set but is not a folder: {repo}")
    return datarepo.export_dir(repo), 0


def _verify_report(as_json, report):
    items = report["items"]
    if as_json:
        print(json.dumps(report, indent=2))
    else:
        s = datarepo.summary(items)
        print(f"verify: {len(items)} items: " + ", ".join(f"{v} {k}" for k, v in s.items()))
        for i in items:
            if i["status"] != "same":
                print(f"  {i['status']:<11} {i['item']}" + (f"  ({i['detail']})" if i.get("detail") else ""))
    return 0 if all(i["status"] == "same" for i in items) else 1


def cmd_export(args):
    as_json = _json_flag(args)
    out, code = _data_dir(as_json, "export")
    if out is None:
        return code
    try:
        r = datarepo.write_export(out)
    except datarepo.ExportError as e:
        return _fail(as_json, "export", str(e), e.findings)
    if as_json:
        print(json.dumps(r, indent=2))
        return 0
    rows = ", ".join(f"{n} {k}" for n, k in r["rows"].items())
    print(f"export: {rows} rows -> {r['dir']}: {r['written']} files written, {r['unchanged']} unchanged, "
          f"{r['removed']} removed")
    for n, lines in r["skipped"].items():
        print(f"  {n}: {len(lines)} malformed line(s) not exported (the reader skips them too): "
              + ", ".join(map(str, lines[:10])))
    return 0


def cmd_import(args):
    as_json = _json_flag(args)
    out, code = _data_dir(as_json, "import")
    if out is None:
        return code
    try:
        r = datarepo.import_into(out)
    except datarepo.ExportError as e:
        return _fail(as_json, "import", str(e), e.findings)
    if not as_json:
        print(f"import: {', '.join(f'{n} {k}' for n, k in r['rows'].items())} rows from {r['dir']} -> {r['store']}")
    return _verify_report(as_json, datarepo.verify(out))


def cmd_verify(args):
    as_json = _json_flag(args)
    out, code = _data_dir(as_json, "verify")
    if out is None:
        return code
    return _verify_report(as_json, datarepo.verify(out))


COMMANDS = {
    "status": cmd_status, "burn": cmd_burn, "project": cmd_project, "gate": cmd_gate,
    "hits": cmd_hits, "failures": cmd_failures, "tokens": cmd_tokens, "calibrate": cmd_calibrate, "check": cmd_check,
    "init": cmd_init, "registrations": cmd_registrations,
    "export": cmd_export, "import": cmd_import, "verify": cmd_verify,
}
CLI_PATH = config.TOOL / "usage"  # absolute in the store's cli.json: the store is outside every repo


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
