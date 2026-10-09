"""The store's serialised form (PLAN-repo-setup.md §7.11): what `usage export`
writes into $DATA_REPO/usage/, what `usage import` recreates the ledgers from,
and what `usage verify` compares the store against.

THE CONTRACT. The store under ~/.claude/usage/ stays the one source; the data
repo holds its history, the part nothing can regenerate. What is exported is an
allowlist (CLASSES below): every file the store holds is classified, and
tests/test_datarepo.py holds CLASSES equal to what store.py names and to what
the reporters actually write, both ways, so nothing reaches the data repo by
default.

  exported   samples.tsv  the rate-limit window history (what %/token and
                          `burn` are measured from; never recoverable later)
             tokens.tsv   the per-agent token ledger (transcripts are pruned,
                          so this is the only lasting record)
             hits.tsv     the rate-limit hits, the evidence `calibrate` reads
  excluded   limits.json  the latest sample, rewritten on every status line;
                          stale after sample_stale_minutes, so a restored copy
                          would be a wrong answer (the gate holds on it)
             state/       this machine's transcript offsets, keyed by a hash of
                          a local path; meaningless on another machine
             .lock        the write lock
             failures.tsv this machine's failed reporter writes (a local fault)
             cli.json     the store's gate, holding this machine's tool path

Each exported ledger is written exactly as the tool reads it: header, then
every row store.scan() returns, rendered by store.render(). A row the reader
skips (cell count off) is not exported, and export names its line. So verify
is a comparison of exact text, and re-exporting an unchanged store writes
nothing.

CREDENTIALS. Every cell must fit its column's shape (SHAPES); the free-text
columns (session, agent_type, model) must also hold no run of 20 or more
letters and digits, the shape of a key or token. One misfit stops the export
(nothing is written) and the import (the store is not touched), and the
finding names the file, line and column, never the value.

ITEM STATES, as tools/checks' stores-exported reads them
(schema/verify.schema.json):
  same         the export file equals the store's rendering
  differs      both hold it and they differ (`detail`: how many rows one side
               lacks, or the first differing row; never a value); or the
               export file is not as `usage export` writes it
  missing      one side lacks it (`detail` says which, and what to run)
  unavailable  never: everything compared here is local

Layout, under $DATA_REPO/usage/: manifest.json (tool, format, this contract as
data), samples.tsv, tokens.tsv, hits.tsv.
"""
import json
import os
import re
from pathlib import Path

from . import config, store

TOOL = "usage"
FORMAT = 2  # 2: hits.tsv carries both windows' resets_at (2026-10-09)
MANIFEST = "manifest.json"

EXPORT = "export"
# Every file or folder the store holds, classified: EXPORT, or why not.
CLASSES = {
    "samples.tsv": EXPORT,
    "tokens.tsv": EXPORT,
    "hits.tsv": EXPORT,
    store.LIMITS: "the latest sample, rewritten on every status line and stale after "
                  "sample_stale_minutes: a restored copy would be a wrong answer",
    "state/": "this machine's transcript offsets, keyed by a hash of a local path",
    ".lock": "the write lock",
    store.FAILURES: "this machine's failed reporter writes: a local fault to fix here, not history",
    store.CONTRACT: "the store's gate, written by `usage init` with this machine's tool path",
}
LEDGERS = [n for n, c in CLASSES.items() if c == EXPORT]

_ISO = r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ"
_NUM = r"-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?"
_IDENT = r"[A-Za-z0-9][A-Za-z0-9_.:@/\[\]-]{0,127}"
# column -> (pattern, may be empty, free text: also checked for a credential run)
_COL = {
    "time": (_ISO, False, False), "sample_time": (_ISO, True, False),
    "five_hour_pct": (_NUM, True, False), "seven_day_pct": (_NUM, True, False),
    "five_hour_resets_at": (_NUM, True, False), "seven_day_resets_at": (_NUM, True, False),
    "session": (_IDENT, True, True), "agent_type": (_IDENT, True, True), "model": (_IDENT, True, True),
    "input": (r"\d+", False, False), "output": (r"\d+", False, False),
    "cache_read": (r"\d+", False, False), "cache_write": (r"\d+", False, False),
    "basis": (r"turn|backlog", False, False),
}
SHAPES = {name: {c: _COL[c] for c in store.COLUMNS[name]} for name in LEDGERS}
CREDENTIAL_RUN = re.compile(r"[A-Za-z0-9]{20,}")


class ExportError(Exception):
    def __init__(self, msg, findings=()):
        super().__init__(msg)
        self.findings = list(findings)


def manifest():
    return {
        "tool": TOOL,
        "format": FORMAT,
        "store": "~/.claude/usage/",
        "exported": {n: store.COLUMNS[n] for n in LEDGERS},
        "excluded": {n: c for n, c in CLASSES.items() if c != EXPORT},
    }


def render_manifest():
    return json.dumps(manifest(), indent=2) + "\n"


def export_dir(data_repo):
    return Path(data_repo) / TOOL


def problems(name, rows, at):
    """Where a row of ledger `name` does not fit SHAPES: 'file line N, column: why
    (value not shown)'. Never quotes a value."""
    out = []
    for row, line in zip(rows, at):
        for col, (pat, empty, free) in SHAPES[name].items():
            v = row.get(col, "")
            where = f"{name} line {line}, {col}"
            if v == "":
                if not empty:
                    out.append(f"{where}: empty")
                continue
            if free and CREDENTIAL_RUN.search(v):
                out.append(f"{where}: credential-shaped (a run of 20+ letters and digits; value not shown)")
            elif not re.fullmatch(pat, v):
                out.append(f"{where}: not the column's shape (value not shown)")
    return out


def _files(d):
    """Every regular file under d, relative, sorted; dotfiles skipped."""
    if not d.is_dir():
        return []
    out = []
    for root, dirs, files in os.walk(d):
        dirs[:] = sorted(x for x in dirs if not x.startswith("."))
        out += [os.path.relpath(os.path.join(root, f), d) for f in files if not f.startswith(".")]
    return sorted(out)


def _read(p):
    try:
        return p.read_text(encoding="utf-8")
    except (FileNotFoundError, IsADirectoryError):
        return None


def _store_ledgers(d):
    """{name: (rows, at, skipped)} of the store folder d (None: no store)."""
    return {n: store.scan(d, n) if d is not None else ([], [], []) for n in LEDGERS}


def write_export(out):
    """Writes the store's ledgers into `out` (changed files rewritten, unchanged
    left alone, files this export does not write removed). Refuses: no store, a
    credential-shaped or misshapen cell, a non-empty folder that is not this
    tool's export. Returns a summary."""
    try:
        with store.locked() as d:
            got = _store_ledgers(d)
    except store.NoStore as e:
        raise ExportError(f"no store at {e}: nothing to export (usage init)") from None
    bad = [p for n, (rows, at, _) in got.items() for p in problems(n, rows, at)]
    if bad:
        raise ExportError(f"refusing to export: {len(bad)} cell(s) do not fit their column (values not shown)", bad)
    files = {MANIFEST: render_manifest(), **{n: store.render(n, got[n][0]) for n in LEDGERS}}
    present = _files(out)
    if present:
        try:
            m = json.loads(_read(out / MANIFEST) or "")
        except ValueError:
            m = None
        if not (isinstance(m, dict) and m.get("tool") == TOOL):
            raise ExportError(f"refusing to write into {out}: it holds files and no {MANIFEST} naming {TOOL}")
    written = unchanged = removed = 0
    out.mkdir(parents=True, exist_ok=True)
    for rel, text in files.items():
        if _read(out / rel) == text:
            unchanged += 1
            continue
        tmp = out / f".{rel}.tmp-{os.getpid()}"
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, out / rel)
        written += 1
    for rel in present:
        if rel not in files:
            os.remove(out / rel)
            removed += 1
    return {"dir": str(out), "rows": {n: len(got[n][0]) for n in LEDGERS}, "written": written,
            "unchanged": unchanged, "removed": removed,
            "skipped": {n: got[n][2] for n in LEDGERS if got[n][2]}}


def read_export(out):
    """{name: text} of an export, checked in full before anything is written:
    the manifest names this tool and format, each ledger is exactly as
    `usage export` writes it, and every cell fits its column."""
    raw = _read(out / MANIFEST)
    if raw is None:
        raise ExportError(f"{out}: no {MANIFEST}: not an export of {TOOL} (run `usage export` where the store is)")
    try:
        m = json.loads(raw)
    except ValueError as e:
        raise ExportError(f"{out}/{MANIFEST}: does not parse ({e})") from None
    if not isinstance(m, dict) or m.get("tool") != TOOL:
        raise ExportError(f"{out}/{MANIFEST}: does not name {TOOL}: not this tool's export")
    if m.get("format") != FORMAT:
        raise ExportError(f"{out}/{MANIFEST}: export format {m.get('format')}, this usage reads format {FORMAT}")
    texts, bad = {}, []
    for n in LEDGERS:
        text = _read(out / n)
        if text is None:
            raise ExportError(f"{out}: no {n}")
        rows, at, skipped = store.scan(out, n)
        if skipped or text != store.render(n, rows):
            raise ExportError(f"{out}/{n}: not as `usage export` writes it"
                              + (f" (line {skipped[0]})" if skipped else ""))
        bad += problems(n, rows, at)
        texts[n] = text
    if bad:
        raise ExportError(f"refusing to import: {len(bad)} cell(s) do not fit their column (values not shown)", bad)
    return texts


def import_into(out):
    """Recreates the ledgers from `out`. Creates the store folder if absent; never
    writes into a store whose ledgers hold a row (an existing store is never
    dropped); limits.json and state/ are left to the reporters."""
    texts = read_export(out)
    config.store_dir().mkdir(parents=True, exist_ok=True)
    with store.locked() as d:
        held = {n: len(rows) for n, (rows, _, _) in _store_ledgers(d).items() if rows}
        if held:
            raise ExportError(f"the store at {d} already holds rows ("
                              + ", ".join(f"{n} {k}" for n, k in held.items())
                              + "): import never writes into an existing store")
        for n, text in texts.items():
            store.write_text(d, n, text)
    return {"dir": str(out), "store": str(d), "rows": {n: t.count("\n") - 1 for n, t in texts.items()}}


def _rows_detail(mine, theirs):
    """How two renderings of one ledger differ, by row count and position only."""
    a, b = mine.splitlines()[1:], theirs.splitlines()[1:]
    if len(a) > len(b) and a[:len(b)] == b:
        return f"the store has {len(a) - len(b)} row(s) the export lacks (run `usage export`)"
    if len(b) > len(a) and b[:len(a)] == a:
        return f"the export has {len(b) - len(a)} row(s) the store lacks"
    first = next((i for i, (x, y) in enumerate(zip(a, b)) if x != y), min(len(a), len(b)))
    return f"first difference at row {first + 1} (store {len(a)} rows, export {len(b)})"


def verify(out):
    """{"items": [...]}: the shape tools/checks' stores-exported reads. Never empty."""
    items = []

    def add(item, status, detail=None):
        items.append({"item": item, "status": status, **({"detail": detail} if detail else {})})

    d = config.store_dir()
    d = d if d.is_dir() else None
    have = _read(out / MANIFEST)
    if have is None:
        add("manifest", "missing", f"the export has no {MANIFEST} (run `usage export`)")
    elif have == render_manifest():
        add("manifest", "same")
    else:
        add("manifest", "differs", f"{MANIFEST} is not what this usage writes (format or contract changed; "
                                   "run `usage export`)")
    for n, (rows, _, _) in _store_ledgers(d).items():
        item = f"ledger:{n}"
        theirs = _read(out / n)
        if theirs is None:
            add(item, "missing", "the export lacks it (run `usage export`" + (")" if d else " after `usage init`)"))
            continue
        if d is None:
            add(item, "missing", f"the export has it, there is no store at {config.store_dir()} (run `usage import`)")
            continue
        mine = store.render(n, rows)
        if theirs == mine:
            add(item, "same")
            continue
        t_rows, _, skipped = store.scan(out, n)
        if skipped or theirs != store.render(n, t_rows):
            add(item, "differs", "the export file is not as `usage export` writes it"
                                 + (f" (line {skipped[0]})" if skipped else ""))
        else:
            add(item, "differs", _rows_detail(mine, theirs))
    for rel in _files(out):
        if rel not in (MANIFEST, *LEDGERS):
            add(f"file:{rel}", "missing", "the export has it, usage does not write it")
    return {"items": items}


def summary(items):
    return {s: sum(1 for i in items if i["status"] == s) for s in ("same", "differs", "missing", "unavailable")}
