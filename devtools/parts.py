#!/usr/bin/env python3
"""parts.py -- the one shared suite runner (PLAN-small-tasks.md §3, §7 step 1).

A suite is a list of parts. Every part runs and reports, each with its own
timeout; each part's result is written to the progress file as it ends; a
--resume re-runs only the parts whose files changed since they passed (and
the ones that did not pass); and the run ends "N of M passed; failed: ...".
tools/checks/source/check-partial.py is the gate a runner is held to, and its
fixtures run THIS file, rendered into a scratch repo, with one property broken
at a time (tests/test_check_partial.py).

This file is a versioned, loadable dependency (PLAN-services.md S0.1): the
standard library only, no import from beside it, so a copy works alone
(tests/test_parts.py holds that). A repo's dev.sh runs it as a script.

  parts.py run <parts.json> [--resume | --only <name>...] [--json]
      Runs the parts in file order, one at a time. Text: one line per part as
      it ends (`ok`, `skip`, `FAIL` with up to 8 of its FAIL/ERROR lines,
      `ERROR` for a timeout), then `check: N of M passed; failed: <name>
      (<why>: <its first FAIL line>); ...`. With --resume the first line is
      `resume: re-running m of M: <names>; ...` or `full run: <why>`.
      --only <name> (repeatable) runs just the parts named, M being their
      count: the first line is `only: running m of M: <names>; keeping k ...`,
      the last ends `; not run: <n> (--only)`, and in --json every other part
      is `unchecked` (so the document is never a green suite). Its progress
      file keeps, in passed_steps and hashes, each other part that passed in
      a resumable progress file and whose hash is unchanged (never an
      `always` part), listed in `kept`, so a later --resume still skips them.
      An unknown name, or --only with --resume, is refused.
      A part that exits 3 is `unchecked` (it could not judge, e.g. a lone
      clone with no sibling repo): text `UNCHK <name>` and its last output
      line, the summary adds `; unchecked: <name> (<that line>)`; it is neither
      a pass nor a fail, is not kept for --resume, and does not make the run red.
      --json: stdout is one document in tools/checks/schema/check-json.schema.json,
      one check per part (a timeout `error`, exit 3 `unchecked` with `reason`
      `exit 3: <last line>`, any other non-zero exit `fail`, a skipped part `ok`
      with counts.skipped 1). A failure's `file`/`line` come from a FAIL/ERROR
      line shaped `FAIL <file>:<n> ...` when <file> is a file inside the parts
      folder (given relative to it); otherwise both are null, never a guess.
      Exit 0 iff no part run failed or errored, 1 if one did, 2 when the parts
      file or the options are refused (with --json, still one document).
  parts.py validate <parts.json>   each fault on one line, exit 1; or `ok: N parts`
  parts.py files <parts.json> <name>   what that part's hash reads, one line per
      path: `content|shape|except|missing|gone <path>`
  parts.py version                 this runner's version

THE PARTS FILE: {"parts": [part, ...], "defaults"?: {...}, "_comment"?}
  part: {"name", "run", "files", "timeout_s", "except"?, "shape"?, "role"?,
         "always"?, "_comment"?}
  name       [A-Za-z0-9._-]+, unique
  run        a sh command, run in the parts file's folder, stdin /dev/null,
             stdout and stderr to <logs>/<name>.log
  files      paths relative to that folder (never `~`): a file counts by its
             content and executable bit; a folder by every file under it (git's
             tracked and untracked non-ignored files when it is in a work tree,
             else every file outside .git). Each must exist.
  except     globs (`*` crosses /; a folder covers what is under it): files
             under a `files` folder the part does not read
  shape      globs: files whose presence and executable bit count, not content
  timeout_s  seconds (> 0); past it the part's whole process tree is stopped
             and killed, the part is `error`, and the next part runs
  role       whose view a failure belongs to in --json (default "tests")
  always     why this part is never skipped by --resume (its result depends on
             what no `files` list can name: another repo's live tree, the
             user's settings). A part with no files must say this.
  `defaults` holds files, except, shape (prepended to each part's), timeout_s
  and role (fallbacks). A path named exactly in `files` counts by content,
  whatever except and shape say. Any other key is refused: a typo such as
  `timeout` is a fault, never ignored.
  A part's hash covers its definition (all but timeout_s and role), this
  runner's VERSION, the runtime (PARTS_RUNTIME, else this Python's version)
  and every path its files reach. It is a CLAIM: an undeclared file the part
  does read makes --resume skip it wrongly. Declare wide, or say `always`.

THE PROGRESS FILE: $PROGRESS_FILE, default <parts folder>/.progress/<stem>.json,
the stem being the parts file's name up to its first dot (check.parts.json:
check), which is also the `cmd` field. The fields of the shared progress.sh (cmd pid started updated
step total label passed failed failed_names state last_duration_s
resumed_from), plus passed_steps (the parts that passed, skipped ones
included), hashes ({part: its hash when it passed}), runner (VERSION), and
only and kept (the names given to --only and the passes kept from the last
file; both null on a run without --only).
Rewritten whole (temp file, then rename) when the run starts, when each part
starts and when each part ends. The logs are in <progress file>.logs/ (a
temp folder when that cannot be made; each part's line names its log). The
progress file, its temp files and the logs are never part of any hash.
A resume is refused (a full run, said) when the file is missing, unreadable,
older than $PARTS_MAX_AGE seconds (86400), names no passed part, or a live run
is writing it. TERM, INT and HUP stop the running part's tree, mark the parts
not run `unchecked`, and end the file `failed`. A SIGKILL to the runner alone
cannot be caught: a part runs in the runner's process group, so a kill of the
group (what a caller's timeout does) takes the part with it.
"""
import datetime
import fnmatch
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time

VERSION = "1.2.0"
NAME = re.compile(r"[A-Za-z0-9._-]+\Z")
PART_KEYS = {"name", "run", "files", "except", "shape", "timeout_s", "role", "always", "_comment"}
DEFAULT_KEYS = {"files", "except", "shape", "timeout_s", "role", "_comment"}
LISTS = ("files", "except", "shape")
FAIL_LINE = re.compile(r"^\s*(FAIL|ERROR)\b")
LOCATION = re.compile(r"^\s*(?:FAIL|ERROR):?\s+([^\s:]+):([1-9][0-9]*)(?![0-9])")
MAX_FAIL_LINES = 8
EXIT_UNCHECKED = 3


class Refused(Exception):
    """The parts file cannot be run; each arg is one fault."""


class Stopped(Exception):
    """A TERM, INT or HUP arrived."""


def now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def seconds_since(iso):
    try:
        t = datetime.datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)
    except (TypeError, ValueError):
        return None
    return (datetime.datetime.now(datetime.timezone.utc) - t).total_seconds()


# ---- the parts file ---------------------------------------------------------

def _strings(value, where, key, faults):
    if not isinstance(value, list) or not all(isinstance(x, str) and x for x in value):
        faults.append(f"{where}: `{key}` is not a list of non-empty strings")
        return []
    for x in value:
        if x.startswith("~"):
            faults.append(f"{where}: `{key}` path {x!r} starts with ~ (not expanded; name it relative to the parts file)")
    return value


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0


def check(doc, base):
    """(parts, faults): each part merged with the defaults, and every fault found."""
    faults, parts = [], []
    if not isinstance(doc, dict):
        return [], ["the parts file is not a JSON object"]
    for k in sorted(set(doc) - {"parts", "defaults", "_comment"}):
        faults.append(f"unknown top-level key `{k}`")
    d = doc.get("defaults", {})
    if not isinstance(d, dict):
        faults.append("`defaults` is not an object")
        d = {}
    for k in sorted(set(d) - DEFAULT_KEYS):
        faults.append(f"defaults: unknown key `{k}`")
    dl = {k: _strings(d[k], "defaults", k, faults) if k in d else [] for k in LISTS}
    if "timeout_s" in d and not _number(d["timeout_s"]):
        faults.append("defaults: `timeout_s` is not a number above 0")
    if "role" in d and not (isinstance(d["role"], str) and d["role"]):
        faults.append("defaults: `role` is not a non-empty string")
    raw = doc.get("parts")
    if not isinstance(raw, list) or not raw:
        return [], faults + ["`parts` is not a non-empty list: a suite of nothing is not a pass"]
    seen = set()
    for i, p in enumerate(raw):
        where = f"parts[{i}]"
        if not isinstance(p, dict):
            faults.append(f"{where}: not an object")
            continue
        name = p.get("name")
        if not (isinstance(name, str) and NAME.match(name)):
            faults.append(f"{where}: `name` {name!r} is not [A-Za-z0-9._-]+")
        else:
            where = f"part {name}"
            if name in seen:
                faults.append(f"{where}: the name is used twice")
            seen.add(name)
        for k in sorted(set(p) - PART_KEYS):
            faults.append(f"{where}: unknown key `{k}`")
        if not (isinstance(p.get("run"), str) and p["run"].strip()):
            faults.append(f"{where}: `run` is not a non-empty command")
        merged = {"name": name, "run": p.get("run")}
        for k in LISTS:
            merged[k] = dl[k] + (_strings(p[k], where, k, faults) if k in p else [])
        t = p.get("timeout_s", d.get("timeout_s"))
        if not _number(t):
            faults.append(f"{where}: no `timeout_s` above 0 (in the part or defaults): every part has its own timeout")
        merged["timeout_s"] = t
        merged["role"] = p.get("role", d.get("role", "tests"))
        if not (isinstance(merged["role"], str) and merged["role"]):
            faults.append(f"{where}: `role` is not a non-empty string")
        if "always" in p and not (isinstance(p["always"], str) and p["always"].strip()):
            faults.append(f"{where}: `always` must say why (a non-empty string)")
        merged["always"] = p.get("always")
        if not merged["files"] and not merged["always"]:
            faults.append(f"{where}: no `files` and no `always`: --resume would skip it forever")
        for f in merged["files"]:
            if not f.startswith("~") and not os.path.lexists(os.path.join(base, f)):
                faults.append(f"{where}: `files` names {f}, which does not exist")
        parts.append(merged)
    return parts, faults


def load(path):
    """(base folder, parts); raises Refused with every fault."""
    try:
        with open(path) as fh:
            doc = json.load(fh)
    except OSError as e:
        raise Refused(f"cannot read the parts file {path}: {e.strerror}")
    except ValueError as e:
        raise Refused(f"the parts file {path} is not JSON: {e}")
    base = os.path.dirname(os.path.abspath(path))
    parts, faults = check(doc, base)
    if faults:
        raise Refused(*faults)
    return base, parts


# ---- what a part's hash reads ---------------------------------------------

def _match(path, globs):
    for g in globs:
        g = g.rstrip("/")
        if fnmatch.fnmatchcase(path, g) or path.startswith(g + "/"):
            return True
    return False


def _listdir(base, rel):
    """Every file under base/rel, relative to base."""
    top = os.path.join(base, rel)
    pre = "" if os.path.normpath(rel) == "." else os.path.normpath(rel) + "/"
    p = subprocess.run(["git", "-C", top, "ls-files", "-co", "--exclude-standard", "-z"],
                       capture_output=True, stdin=subprocess.DEVNULL)
    if p.returncode == 0:
        out = []
        for e in p.stdout.decode("utf-8", "surrogateescape").split("\0"):
            if not e:
                continue
            if e.endswith("/"):           # an untracked nested repo: walk it as a folder of its own
                out += _listdir(base, pre + e.rstrip("/"))
            else:
                out.append(pre + e)
        return out
    out = []
    for d, dirs, files in os.walk(top):
        dirs[:] = [x for x in dirs if x != ".git"]
        for f in files:
            out.append(os.path.relpath(os.path.join(d, f), base))
    return out


def _sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def walk(base, part, ignored=lambda absolute: False):
    """[(kind, path, detail)] for every path the part's files reach, sorted.
    kind: content (detail: x bit + sha256), shape (x bit), except, missing, gone."""
    named = [os.path.normpath(f) for f in part["files"]]
    explicit = {f for f in named if os.path.isfile(os.path.join(base, f))}
    rows = {}
    for f in named:
        full = os.path.join(base, f)
        if f in explicit:
            rows[f] = ("content", f, f"{'x' if os.access(full, os.X_OK) else '-'} {_sha(full)}")
        elif os.path.isdir(full):
            for p in _listdir(base, f):
                p = os.path.normpath(p)
                if p in explicit or p in rows or ignored(os.path.abspath(os.path.join(base, p))):
                    continue
                fp = os.path.join(base, p)
                if _match(p, part["except"]):
                    rows[p] = ("except", p, "")
                elif not os.path.isfile(fp):
                    rows[p] = ("gone", p, "")
                elif _match(p, part["shape"]):
                    rows[p] = ("shape", p, "x" if os.access(fp, os.X_OK) else "-")
                else:
                    rows[p] = ("content", p, f"{'x' if os.access(fp, os.X_OK) else '-'} {_sha(fp)}")
        else:
            rows[f] = ("missing", f, "")
    return [rows[k] for k in sorted(rows)]


def runtime():
    return os.environ.get("PARTS_RUNTIME") or f"python {sys.version.split()[0]}"


def part_hash(base, part, ignored=lambda absolute: False):
    h = hashlib.sha256()
    definition = {k: part[k] for k in ("run", "files", "except", "shape", "always")}
    h.update(json.dumps([VERSION, runtime(), definition], sort_keys=True).encode())
    for kind, path, detail in walk(base, part, ignored):
        if kind != "except":
            h.update(f"\n{kind} {detail} {path}".encode("utf-8", "surrogateescape"))
    return h.hexdigest()[:16]


# ---- the progress file -----------------------------------------------------

class Progress:
    def __init__(self, path, cmd):
        self.path = os.path.abspath(path)
        self.logs = self.path + ".logs"
        self.cmd = cmd
        self.warned = False
        self.state = None

    def log_dir(self):
        """<progress file>.logs, or (when that cannot be made) a temp folder: a
        part's log is how its FAIL lines are read, so it always has one. Each
        part's line names its log."""
        try:
            os.makedirs(self.logs, exist_ok=True)
        except OSError:
            self.logs = tempfile.mkdtemp(prefix="parts-logs-")
        return self.logs

    def ignored(self, absolute):
        """The progress file, its temp files and its logs are in no hash."""
        p, d = self.path, os.path.dirname(self.path)
        return (absolute == p or absolute.startswith(self.logs + os.sep)
                or (os.path.dirname(absolute) == d and os.path.basename(absolute).startswith("." + os.path.basename(p) + ".")))

    def old(self, me):
        """(passed {name: hash} or None, why None, when it was written)."""
        try:
            with open(self.path) as fh:
                doc = json.load(fh)
        except FileNotFoundError:
            return None, f"no progress file ({self.path})", None
        except (OSError, ValueError):
            return None, f"progress file unreadable ({self.path})", None
        if not isinstance(doc, dict):
            return None, f"progress file unreadable ({self.path})", None
        when, pid = doc.get("updated"), doc.get("pid")
        if doc.get("state") == "running" and isinstance(pid, int) and pid != me and alive(pid):
            return None, f"a run is writing the progress file now (pid {pid})", when
        age = seconds_since(when)
        limit = float(os.environ.get("PARTS_MAX_AGE") or 86400)
        if age is None:
            return None, "progress file has no readable `updated` time", when
        if age > limit:
            return None, f"progress file expired ({age / 3600:.0f}h old, at {when})", when
        steps, hashes = doc.get("passed_steps"), doc.get("hashes")
        if not isinstance(steps, list) or not isinstance(hashes, dict):
            return None, "progress file has no `passed_steps` and `hashes`", when
        passed = {n: hashes[n] for n in steps if isinstance(n, str) and isinstance(hashes.get(n), str)}
        if not passed:
            return None, "no part passed in the progress file", when
        return passed, None, when

    def last_duration(self):
        try:
            with open(self.path) as fh:
                doc = json.load(fh)
            if doc.get("state") in ("passed", "failed"):
                a, b = seconds_since(doc["started"]), seconds_since(doc["updated"])
                return int(a - b) if a is not None and b is not None else None
        except (OSError, ValueError, AttributeError, KeyError, TypeError):
            pass
        return None

    def start(self, total):
        self.state = {"cmd": self.cmd, "pid": os.getpid(), "started": now(), "updated": now(), "step": 0,
                      "total": total, "label": "", "passed": 0, "failed": 0, "failed_names": [],
                      "state": "running", "last_duration_s": self.last_duration(), "resumed_from": None,
                      "passed_steps": [], "hashes": {}, "runner": VERSION, "only": None, "kept": None}
        self.write()

    def label(self, name):
        self.state["label"] = name
        self.write()

    def step(self, name, ok, h):
        """ok None: the part said unchecked -- counted in step, neither passed
        nor failed, and not kept for --resume (it runs again)."""
        s = self.state
        s["step"] += 1
        s["label"] = name
        if ok is None:
            pass
        elif ok:
            s["passed"] += 1
            s["passed_steps"].append(name)
            s["hashes"][name] = h
        else:
            s["failed"] += 1
            if len(s["failed_names"]) < 5:
                s["failed_names"].append(name)
        self.write()

    def end(self, state):
        self.state["state"] = state
        self.write()

    def write(self):
        """Never changes the run's result; a write that fails is said once, on stderr."""
        self.state["updated"] = now()
        d = os.path.dirname(self.path)
        try:
            os.makedirs(d, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=d, prefix="." + os.path.basename(self.path) + ".")
            with os.fdopen(fd, "w") as fh:
                json.dump(self.state, fh)
            os.replace(tmp, self.path)
        except OSError as e:
            if not self.warned:
                print(f"parts: cannot write the progress file {self.path}: {e.strerror}; a resume will run in full",
                      file=sys.stderr)
                self.warned = True


def progress(base, parts_path):
    """The run's Progress: $PROGRESS_FILE, else <base>/.progress/<stem>.json;
    the stem (the parts file's name up to its first dot) is also its `cmd`."""
    stem = os.path.basename(parts_path).split(".", 1)[0] or "parts"
    return Progress(os.environ.get("PROGRESS_FILE") or os.path.join(base, ".progress", stem + ".json"), stem)


# ---- running one part ------------------------------------------------------

def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def descendants(pid):
    p = subprocess.run(["ps", "-A", "-o", "pid=,ppid="], capture_output=True, text=True, stdin=subprocess.DEVNULL)
    kids = {}
    for line in p.stdout.splitlines():
        a = line.split()
        if len(a) == 2 and a[0].isdigit() and a[1].isdigit():
            kids.setdefault(int(a[1]), []).append(int(a[0]))
    out, todo = [], [pid]
    while todo:
        for c in kids.get(todo.pop(), []):
            if c not in out:
                out.append(c)
                todo.append(c)
    return out


def kill_tree(pid):
    """Stop the part and everything under it (until a pass finds nothing new, so
    nothing spawns more), then kill them all."""
    tree, new = [], [pid]
    for _ in range(20):
        for p in new:
            try:
                os.kill(p, signal.SIGSTOP)
            except OSError:
                pass
        tree += new
        new = [p for p in descendants(pid) if p not in tree]
        if not new:
            break
    for p in tree:
        try:
            os.kill(p, signal.SIGKILL)
        except OSError:
            pass


def location(base, line):
    """(file, line) from a `FAIL <file>:<n>` / `ERROR <file>:<n>` line, the file
    repo-relative and existing under base; else (None, None): never a guess
    (a time such as `10:30`, or a path that is not there, gives nulls)."""
    m = LOCATION.match(line)
    if not m:
        return None, None
    path = m.group(1)
    full = os.path.realpath(os.path.join(base, path))
    root = os.path.realpath(base)
    if not full.startswith(root + os.sep) or not os.path.isfile(full):
        return None, None
    return os.path.relpath(full, root), int(m.group(2))


def fail_lines(log):
    try:
        with open(log, errors="replace") as fh:
            text = fh.read()
    except OSError:
        return [], ""
    lines = [l.rstrip() for l in text.splitlines()]
    fails = [l.strip()[:200] for l in lines if FAIL_LINE.match(l)][:MAX_FAIL_LINES]
    last = next((l.strip()[:200] for l in reversed(lines) if l.strip()), "")
    return fails, last


def run_part(base, part, logs):
    """(status ok|fail|unchecked|error, why, failure lines, seconds, log path).
    For `unchecked` the one line is the reason (the part's last output line)."""
    log = os.path.join(logs, part["name"] + ".log")
    t0 = time.monotonic()
    with open(log, "w") as out:
        p = subprocess.Popen(["/bin/sh", "-c", part["run"]], cwd=base, stdin=subprocess.DEVNULL,
                             stdout=out, stderr=subprocess.STDOUT)
        try:
            code = p.wait(timeout=part["timeout_s"])
            status, why = ("ok", "") if code == 0 else (
                ("unchecked", f"exit {code}") if code == EXIT_UNCHECKED else ("fail", f"exit {code}"))
        except subprocess.TimeoutExpired:
            kill_tree(p.pid)
            p.wait()
            status, why = "error", f"timeout after {part['timeout_s']:g}s"
        except Stopped:
            kill_tree(p.pid)
            p.wait()
            raise
    fails, last = fail_lines(log)
    if status == "unchecked":
        return status, why, [last or f"{why}; no output"], time.monotonic() - t0, log
    if status != "ok" and not fails:
        fails = [f"{why}; last output: {last}" if last else f"{why}; no output"]
    return status, why, fails, time.monotonic() - t0, log


# ---- the run ---------------------------------------------------------------

def entry(name, status, role, fails=(), reason=None, skipped=False, base=None):
    failures = [dict(zip(("file", "line"), location(base, m) if base else (None, None)), message=m, role=role)
                for m in fails]
    e = {"name": name, "status": status, "counts": {"failed": len(failures)}, "failures": failures}
    if skipped:
        e["counts"]["skipped"] = 1
    if reason:
        e["reason"] = reason
    return e


def summary(n, m, failed):
    line = f"check: {n} of {m} passed"
    return line + ("; failed: " + "; ".join(failed) if failed else "")


def shorten(names, limit=10):
    return ", ".join(names[:limit]) + (f", ... ({len(names) - limit} more)" if len(names) > limit else "")


def refused(args, faults, as_json):
    if as_json:
        print(json.dumps({"ok": False, "checks": [entry("parts-file", "error", "tests",
                                                         [f"refused: {f}" for f in faults])]}))
    else:
        for f in faults:
            print(f"parts: refused: {f}", file=sys.stderr)
    return 2


RUN_USAGE = "run <parts.json> [--resume | --only <name>...] [--json]"


def options(rest):
    """(flags, only names, fault or None) from the words after the parts file."""
    flags, only, i = set(), [], 0
    while i < len(rest):
        a = rest[i]
        if a in ("--resume", "--json"):
            flags.add(a)
        elif a == "--only":
            if i + 1 >= len(rest) or rest[i + 1].startswith("--"):
                return flags, only, f"--only needs a part name ({RUN_USAGE})"
            only.append(rest[i + 1])
            i += 1
        else:
            return flags, only, f"unknown option {a} ({RUN_USAGE})"
        i += 1
    if only and "--resume" in flags:
        return flags, only, "--only and --resume together: --only runs exactly the parts it names"
    return flags, only, None


def cmd_run(args):
    path = args[0]
    flags, only, fault = options(args[1:])
    as_json = "--json" in args[1:]
    if fault:
        return refused(args, [fault], as_json)
    try:
        base, parts = load(path)
    except Refused as e:
        return refused(args, list(e.args), as_json)
    unknown = [n for n in only if n not in {p["name"] for p in parts}]
    if unknown:
        return refused(args, [f"--only {n}: no part of that name in {path}" for n in unknown], as_json)
    pf = progress(base, path)
    say = (lambda *_: None) if as_json else (lambda line: print(line, flush=True))

    hashes = {p["name"]: part_hash(base, p, pf.ignored) for p in parts}
    every, skip, when, kept = parts, set(), None, []
    if only:
        parts = [p for p in every if p["name"] in only]
        old, why, when = pf.old(os.getpid())
        kept = [p["name"] for p in every if p["name"] not in only and not p["always"]
                and old is not None and old.get(p["name"]) == hashes[p["name"]]]
        say(f"only: running {len(parts)} of {len(every)}: {shorten([p['name'] for p in parts])}; "
            + (f"keeping {len(kept)} other part(s) that passed at {when}, their files unchanged" if old is not None
               else f"keeping none ({why})"))
    if "--resume" in flags:
        old, why, when = pf.old(os.getpid())
        if old is None:
            say(f"full run: {why}")
        else:
            skip = {p["name"] for p in parts if not p["always"] and old.get(p["name"]) == hashes[p["name"]]}
            if not skip:
                say("full run: every part's files changed since it passed, or it did not pass")
            else:
                rerun = [p["name"] for p in parts if p["name"] not in skip]
                say(f"resume: re-running {len(rerun)} of {len(parts)}: {shorten(rerun) or 'none'}"
                    f" (their files changed, they did not pass, or they are `always`);"
                    f" skipping {len(skip)} that passed at {when}, their files unchanged")

    def on_signal(signum, _frame):
        raise Stopped(signum)

    old_handlers = {s: signal.signal(s, on_signal) for s in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)}
    pf.start(len(parts))
    if skip:
        pf.state["resumed_from"] = f"{len(skip)}/{len(parts)} at {when}"
    if only:
        pf.state.update(only=[p["name"] for p in parts], kept=kept, passed_steps=list(kept),
                        hashes={n: hashes[n] for n in kept})
        pf.write()
    checks, failed, unchecked, n, stopped, current = [], [], [], 0, None, None

    def restore():
        for s, hd in old_handlers.items():
            signal.signal(s, hd)

    try:
        for part in parts:
            current = part
            name, h = part["name"], hashes[part["name"]]
            if name in skip:
                pf.step(name, True, h)
                n += 1
                checks.append(entry(name, "ok", part["role"], skipped=True))
                say(f"skip  {name} (passed at {when}, its files unchanged)")
                continue
            pf.label(name)
            status, why, fails, secs, log = run_part(base, part, pf.log_dir())
            ok = status == "ok"
            rel = os.path.relpath(log, base) if log.startswith(base + os.sep) else log
            if status == "unchecked":
                pf.step(name, None, h)
                checks.append(entry(name, status, part["role"], reason=f"{why}: {fails[0]}"))
                unchecked.append(f"{name} ({fails[0]})")
                say(f"UNCHK {name} ({why}, {secs:.0f}s; log: {rel})")
                say(f"  {fails[0]}")
                continue
            pf.step(name, ok, h)
            checks.append(entry(name, status, part["role"], [] if ok else fails, base=base))
            if ok:
                n += 1
                say(f"ok    {name} ({secs:.0f}s)")
            else:
                failed.append(f"{name} ({fails[0]})" if fails[0].startswith(why) else f"{name} ({why}: {fails[0]})")
                say(f"{'FAIL ' if status == 'fail' else 'ERROR'} {name} ({why}, {secs:.0f}s; log: {rel})")
                for line in fails:
                    say(f"  {line}")
    except Stopped as e:
        restore()
        stopped = e.args[0]
        if current is not None and current["name"] not in {c["name"] for c in checks}:
            name = current["name"]
            checks.append(entry(name, "error", current["role"], [f"stopped by signal {stopped} while it ran"]))
            failed.append(f"{name} (stopped by signal {stopped})")
            pf.step(name, False, hashes[name])
            say(f"ERROR {name} (stopped by signal {stopped})")
        done = {c["name"] for c in checks}
        for part in parts:
            if part["name"] not in done:
                checks.append(entry(part["name"], "unchecked", part["role"],
                                    reason=f"not run: the runner was stopped by signal {stopped}"))
    finally:
        restore()
    all_ok = n + len(unchecked) == len(parts)        # a part that said unchecked is neither a pass nor a fail
    pf.end("passed" if all_ok else "failed")
    if only:
        result = {c["name"]: c for c in checks}
        checks = [result.get(p["name"]) or entry(p["name"], "unchecked", p["role"],
                                                 reason="not run: --only named other parts") for p in every]
    if as_json:
        print(json.dumps({"ok": all_ok, "checks": checks}))
    else:
        not_run = sum(c["status"] == "unchecked" for c in checks) - len(unchecked)
        said = "; not run: " + (f"{not_run} (--only)" if only and not stopped else str(not_run))
        said_unchecked = "; unchecked: " + "; ".join(unchecked) if unchecked else ""
        say(summary(n, len(parts), failed) + said_unchecked + (said if not_run else ""))
    if stopped is not None:
        return 128 + stopped
    return 0 if all_ok else 1


def cmd_validate(args):
    try:
        _, parts = load(args[0])
    except Refused as e:
        for f in e.args:
            print(f)
        return 1
    print(f"ok: {len(parts)} parts")
    return 0


def cmd_files(args):
    try:
        base, parts = load(args[0])
    except Refused as e:
        for f in e.args:
            print(f"parts: refused: {f}", file=sys.stderr)
        return 2
    part = next((p for p in parts if p["name"] == args[1]), None)
    if part is None:
        print(f"parts: no part named {args[1]}", file=sys.stderr)
        return 2
    pf = progress(base, args[0])
    for kind, path, _ in walk(base, part, pf.ignored):
        print(f"{kind} {path}")
    if part["always"]:
        print(f"always: {part['always']}")
    return 0


USAGE = __doc__.split("\n\n", 3)[3].split("\n\nTHE PARTS FILE", 1)[0]
COMMANDS = {"run": (cmd_run, 1, 64), "validate": (cmd_validate, 1, 1), "files": (cmd_files, 2, 2),
            "version": (lambda a: print(VERSION) or 0, 0, 0)}


def main(argv):
    if not argv or argv[0] not in COMMANDS:
        print(USAGE, file=sys.stderr)
        return 2
    fn, lo, hi = COMMANDS[argv[0]]
    if not lo <= len(argv) - 1 <= hi:
        print(USAGE, file=sys.stderr)
        return 2
    return fn(argv[1:])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
