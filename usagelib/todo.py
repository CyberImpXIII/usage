"""A todo item's size as a token estimate, for `usage gate --todo ID`
(PLAN-architecture-review.md item 7).

The item is read through tools/todo's own CLI, `todo get ID` (one record as
JSON: {id, closed, tags, record}), never its store files. The CLI is
$USAGE_TODO_CLI, else the sibling ../todo/todo. It runs in the workspace root
(WORKSPACE, two folders above this tool), where todo needs no store of its own
and scans from that folder (tools/todo 4aee964); $USAGE_TODO_DIR overrides it,
for the tests, and $TODO_ROOT passes through untouched.

The estimate per size is data: usage.json `size_tokens`, read through
config.setting(). A size with no entry there (L, which todo says to split) and
an unsized item give no number: estimate() returns (None, why), and the CLI
exits 3, unchecked. A guessed estimate would be a wrong answer.
"""
import json
import os
import subprocess
from pathlib import Path

from . import config

CLI_ENV, DIR_ENV = "USAGE_TODO_CLI", "USAGE_TODO_DIR"
WORKSPACE = config.TOOL.parent.parent  # tests/test_gate_todo.py holds it to todo's scan-root rule
TIMEOUT = 30


def cli_path():
    return Path(os.environ[CLI_ENV]) if os.environ.get(CLI_ENV) else config.TOOL.parent / "todo" / "todo"


def get(item_id):
    """(record dict, None) or (None, why). Never raises."""
    cli = cli_path()
    if not (cli.is_file() and os.access(cli, os.X_OK)):
        return None, f"no todo CLI at {cli} (${CLI_ENV} names another)"
    where = os.environ.get(DIR_ENV) or str(WORKSPACE)
    try:
        r = subprocess.run([str(cli), "get", item_id], cwd=where, capture_output=True, text=True, timeout=TIMEOUT)
    except (OSError, subprocess.SubprocessError) as e:
        return None, f"todo get {item_id} did not run: {type(e).__name__}: {e}"
    if r.returncode != 0:
        msg = (r.stderr.strip() or r.stdout.strip()).splitlines()
        return None, f"todo get {item_id} exit {r.returncode}: {msg[-1] if msg else 'no message'}"
    try:
        doc = json.loads(r.stdout)
        rec = doc["record"]
        if not isinstance(rec, dict):
            raise TypeError("record is not an object")
    except (ValueError, KeyError, TypeError) as e:
        return None, f"todo get {item_id} printed no {{record}} object: {type(e).__name__}: {e}"
    if doc.get("closed"):
        return None, f"todo:{rec.get('id', item_id)} is closed: nothing left to estimate"
    return rec, None


def estimate(rec):
    """(tokens, size, None) or (None, size, why)."""
    size = rec.get("size")  # absent (an item older than the field) is unsized too
    rid = rec.get("id", "?")
    if size is None:
        return None, None, f"todo:{rid} is unsized (todo edit {rid} --size S|M): no estimate"
    table = config.setting("size_tokens")
    n = table.get(size) if isinstance(size, str) else None
    if not isinstance(n, int) or isinstance(n, bool) or n <= 0:
        if size == "L":
            return None, size, f"todo:{rid} is L: more than one checkpoint, split it (todo split {rid}): no estimate"
        return None, size, f"todo:{rid} has size {size!r}, which usage.json size_tokens has no estimate for"
    return n, size, None
