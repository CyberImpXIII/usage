"""The store under ~/.claude/usage/: three append-only TSV ledgers, limits.json
(the latest status-line sample) and state/ (one offset file per transcript).

Nothing here creates the store folder: a missing folder means the tool is not
set up (`usage init`), and the reporters then write nothing (NoStore). Every
write takes the store's lock, so two sessions appending at once interleave
whole rows.
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
    "hits.tsv": ["time", "session", "agent_type", "five_hour_pct", "seven_day_pct", "sample_time"],
}
LIMITS = "limits.json"


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


def append(d, name, rows):
    """Append rows (dicts keyed by COLUMNS[name]) under a held lock; header on create."""
    cols = COLUMNS[name]
    path = d / name
    lines = []
    if not path.exists():
        lines.append("\t".join(cols))
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
    path = d / name
    if not path.exists():
        return [], [], []
    cols = COLUMNS[name]
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
