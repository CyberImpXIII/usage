"""The store under ~/.claude/usage/: three append-only TSV ledgers, limits.json
(the latest status-line sample), state/ (one offset file per transcript),
failures.tsv (the reporters' own failed writes) and cli.json (the store's gate,
written by `usage init`).

Nothing here creates the store folder: a missing folder means the tool is not
set up (`usage init`), and the reporters then write nothing (NoStore). Every
ledger write takes the store's lock, so two sessions appending at once
interleave whole rows.

A FAILED WRITE LEAVES A TRACE. The reporters fail open (exit 0, nothing on
stderr), so a row they could not write would otherwise be lost silently.
record_failure() appends one line to failures.tsv in the store or, when the
store itself refuses the write (read-only, full), to FALLBACK beside it
(`<store>-failures.tsv`). `usage failures` reads both and `usage status` counts
them. Only "no store folder" is not a failure: the tool is not set up.
"""
import calendar
import fcntl
import json
import os
import time
from contextlib import contextmanager

from . import config

# The ledgers' columns: written and read only through these names.
COLUMNS = {
    "samples.tsv": ["time", "five_hour_pct", "seven_day_pct", "five_hour_resets_at", "seven_day_resets_at"],
    "tokens.tsv": ["time", "session", "agent_type", "model", "input", "output",
                   "cache_read", "cache_write", "basis"],
    "hits.tsv": ["time", "session", "agent_type", "five_hour_pct", "seven_day_pct", "sample_time",
                 "five_hour_resets_at", "seven_day_resets_at"],
}
LIMITS = "limits.json"
FAILURES = "failures.tsv"
FAILURE_COLUMNS = ["time", "reporter", "event", "error"]
FAILURES_MAX_BYTES = 256 * 1024  # past this, a failure is not recorded (the file already says it happens)
CONTRACT = "cli.json"
# Every file the store's cli.json names: the store-guard hook refuses a hand
# write to any of them. The repo's cli.json names the same list under
# ~/.claude/usage/ (tests/test_gated.py holds the two equal).
STORE_FILES = [*COLUMNS, LIMITS, FAILURES, "state/*"]


class NoStore(Exception):
    pass


def iso(epoch):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


def epoch(iso_text):
    """None for anything that is not our ISO form (prefer null to a guess)."""
    try:
        return float(calendar.timegm(time.strptime(iso_text, "%Y-%m-%dT%H:%M:%SZ")))
    except (TypeError, ValueError):
        return None


def root():
    d = config.store_dir()
    if not d.is_dir():
        raise NoStore(str(d))
    return d


@contextmanager
def locked():
    d = root()
    with open(d / ".lock", "a") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield d
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def _cell(v):
    if v is None:
        return ""
    return str(v).replace("\t", " ").replace("\n", " ").replace("\r", " ")


class HeaderMismatch(Exception):
    pass


def append(d, name, rows):
    """Append rows (dicts keyed by COLUMNS[name]) under a held lock; header on
    create. A file whose header is not COLUMNS[name] (an older layout) is never
    appended to: rows under the wrong header would be read as other columns, so
    HeaderMismatch is raised and the reporter records it as a failure."""
    cols = COLUMNS[name]
    path = d / name
    lines = []
    if not path.exists():
        lines.append("\t".join(cols))
    else:
        with open(path) as fh:
            head = fh.readline().rstrip("\n")
        if head and head.split("\t") != cols:
            raise HeaderMismatch(f"{name}: header is not {len(cols)} columns {cols[0]}..{cols[-1]}; "
                                 "not appended (move the file aside, or migrate it)")
    for r in rows:
        lines.append("\t".join(_cell(r.get(c)) for c in cols))
    with open(path, "a") as fh:
        fh.write("\n".join(lines) + "\n")


def read(name):
    """Rows as dicts; [] when the store or the file is absent. A row whose cell
    count is off is skipped, never padded."""
    try:
        return scan(root(), name)[0]
    except NoStore:
        return []


def scan(d, name):
    """(rows, line number of each row, skipped line numbers) of one ledger in
    store folder d: the one reader, under read() and the data-repo export
    (usagelib/datarepo.py). An absent file is ([], [], [])."""
    return _rows(d / name, COLUMNS[name])


def _rows(path, cols):
    if not path.exists():
        return [], [], []
    out, at, skipped = [], [], []
    for i, line in enumerate(path.read_text().splitlines()):
        cells = line.split("\t")
        if i == 0 and cells == cols:
            continue
        if len(cells) != len(cols):
            skipped.append(i + 1)
            continue
        out.append(dict(zip(cols, cells)))
        at.append(i + 1)
    return out, at, skipped


def render(name, rows):
    """A ledger's text as append() writes it: header, then one line per row."""
    cols = COLUMNS[name]
    return "".join("\t".join(r) + "\n" for r in
                   [cols] + [[_cell(row.get(c)) for c in cols] for row in rows])


def write_text(d, name, text):
    """Replace one store file whole (tmp + rename), under a held lock."""
    path = d / name
    tmp = d / f".{name}.tmp-{os.getpid()}"
    tmp.write_text(text)
    os.replace(tmp, path)


def write_json(d, name, obj):
    write_text(d, name, json.dumps(obj, sort_keys=True) + "\n")


def read_json(name):
    try:
        return json.loads((root() / name).read_text())
    except (NoStore, OSError, ValueError):
        return None


# ------------------------------------------------------------ failures --

def fallback_path(d):
    """Where a failure goes when the store itself refuses the write."""
    return d.parent / f"{d.name}-failures.tsv"


def describe(exc):
    """`Type: message`, one line, at most 300 characters (never the input)."""
    text = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
    return _cell(text)[:300]


def _append_line(path, line):
    """One O_APPEND write of header (on an empty file) + line; never through a
    link. Returns False when the file is at FAILURES_MAX_BYTES."""
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o644)
    try:
        size = os.fstat(fd).st_size
        if size >= FAILURES_MAX_BYTES:
            return False
        head = "\t".join(FAILURE_COLUMNS) + "\n" if size == 0 else ""
        os.write(fd, (head + line + "\n").encode())
        return True
    finally:
        os.close(fd)


def record_failure(reporter, event, exc):
    """Leave a trace of a reporter's failed write: failures.tsv in the store,
    else FALLBACK beside it. Returns the path written, or None (no store folder:
    not set up, so nothing was lost; or both places refused). Never raises:
    it runs inside the reporters' fail-open path."""
    try:
        d = config.store_dir()
        if not d.is_dir():
            return None
        try:
            t = iso(config.now())
        except Exception:  # noqa: BLE001 -- a bad clock must not cost the trace
            t = iso(time.time())
        line = "\t".join(_cell(x) for x in (t, reporter, event, describe(exc)))
        for path in (d / FAILURES, fallback_path(d)):
            try:
                if _append_line(path, line):
                    return path
                return None
            except OSError:
                continue
    except Exception:  # noqa: BLE001 -- the recorder fails open too
        pass
    return None


def read_failures():
    """[{time, reporter, event, error, where}] from both places, oldest first;
    `where` is the file. [] when neither exists."""
    d = config.store_dir()
    out = []
    for path in (d / FAILURES, fallback_path(d)):
        try:
            rows = _rows(path, FAILURE_COLUMNS)[0]
        except OSError:
            continue
        out += [dict(r, where=str(path)) for r in rows]
    return sorted(out, key=lambda r: r["time"])


# ------------------------------------------------------------ the gate --

def contract(cli_path, verbs):
    """The store's cli.json: the files a hand write must not touch, the one CLI
    (absolute: the store is outside every repo) and its verbs. Read by the
    store-guard hook, which looks for a cli.json in each folder above a write
    target and resolves its store paths against that folder."""
    return {"store": list(STORE_FILES), "cli": str(cli_path), "verbs": list(verbs)}


def render_contract(cli_path, verbs):
    return json.dumps(contract(cli_path, verbs), indent=2) + "\n"


def contract_state(cli_path, verbs):
    """'current', 'missing', 'differs' or None (no store)."""
    d = config.store_dir()
    if not d.is_dir():
        return None
    try:
        have = (d / CONTRACT).read_text()
    except FileNotFoundError:
        return "missing"
    except OSError:
        return "differs"
    return "current" if have == render_contract(cli_path, verbs) else "differs"


def write_contract(cli_path, verbs):
    """Write the store's cli.json when absent or different: 'written' or 'unchanged'."""
    d = root()
    want = render_contract(cli_path, verbs)
    try:
        if (d / CONTRACT).read_text() == want:
            return "unchanged"
    except OSError:
        pass
    write_text(d, CONTRACT, want)
    return "written"
