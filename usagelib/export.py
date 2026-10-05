"""The registrations, and `usage registrations --export [FILE]`
(PLAN-usage-reporting.md §9, approved by Jacob 2026-10-05).

registrations() is the one source of the status line and the three hook
entries. export(FILE) reads a settings file (default: the user-level one),
adds them, and writes the result as a static copy beside it, FILE + ".proposed".
It never writes FILE: the only path opened for writing is a temp file in the
same folder, renamed onto the proposal (tests/test_export.py,
tests/test_settings.py). Jacob reviews the proposal, copies it over FILE, then
runs `usage init`.

Gates before anything is written (any failure: an error, no proposal):
  - FILE parses as one JSON object with no key given twice;
  - the shapes it extends are the documented ones (`statusLine` an object,
    `hooks` an object of lists of groups with a `hooks` list);
  - the copy re-parses, keeps every key of FILE with its value (lists only
    grow at the end; the one rewrite allowed is a foreign status line chained
    behind ours, checked exactly), and carries each registration once.
An identical existing proposal is left alone (not rewritten); a different one
is replaced. Output names keys, never values.
"""
import json
import os
import shlex
import tempfile
from pathlib import Path

from . import config

SUFFIX = ".proposed"
HOOK_EVENTS = ("Stop", "SubagentStop", "StopFailure")
SCRIPTS = {"statusLine": "usage-statusline.sh", "Stop": "usage-stop.sh", "SubagentStop": "usage-stop.sh",
           "StopFailure": "usage-failure.sh"}


class ExportError(Exception):
    pass


def default_file():
    return Path.home() / ".claude" / "settings.json"


def _cmd(name):
    return shlex.quote(str(config.TOOL / name))


def registrations():
    return {
        "statusLine": {"type": "command", "command": _cmd(SCRIPTS["statusLine"])},
        "hooks": {
            "Stop": [{"hooks": [{"type": "command", "command": _cmd(SCRIPTS["Stop"]), "timeout": 10}]}],
            "SubagentStop": [{"hooks": [{"type": "command", "command": _cmd(SCRIPTS["SubagentStop"]),
                                         "timeout": 10}]}],
            "StopFailure": [{"matcher": "rate_limit",
                             "hooks": [{"type": "command", "command": _cmd(SCRIPTS["StopFailure"]), "timeout": 10}]}],
        },
    }


def _no_dupes(pairs):
    seen = {}
    for k, v in pairs:
        if k in seen:
            raise ExportError(f"key `{k}` is given twice in one object (a parser keeps only one; fix the file)")
        seen[k] = v
    return seen


def _load(path):
    try:
        text = path.read_text()
    except FileNotFoundError:
        raise ExportError("does not exist")
    except (OSError, UnicodeDecodeError) as e:
        raise ExportError(f"cannot be read ({type(e).__name__})")
    try:
        doc = json.loads(text, object_pairs_hook=_no_dupes)
    except json.JSONDecodeError as e:
        raise ExportError(f"does not parse as JSON (line {e.lineno}, column {e.colno}: {e.msg})")
    if not isinstance(doc, dict):
        raise ExportError("is not a JSON object")
    return doc


def _first_token(cmd):
    try:
        parts = shlex.split(cmd)
    except ValueError:
        return None
    return parts[0] if parts else None


def _which(cmd, script):
    """'ours' when cmd runs this folder's script, 'elsewhere' when it runs a
    script of that name from another place, None when it is not ours at all."""
    tok = _first_token(cmd) if isinstance(cmd, str) else None
    if tok is None or Path(tok).name != script:
        return None
    return "ours" if tok == str(config.TOOL / script) else "elsewhere"


def _status_line(doc, report):
    ours = registrations()["statusLine"]
    sl = doc.get("statusLine")
    if sl is None:
        doc["statusLine"] = ours
        report.append("statusLine: added")
        return None
    if not isinstance(sl, dict):
        raise ExportError("`statusLine` is not an object")
    cmd = sl.get("command")
    which = _which(cmd, SCRIPTS["statusLine"])
    if which == "ours":
        report.append("statusLine: already present")
        return None
    if which == "elsewhere":
        raise ExportError("`statusLine` runs usage-statusline.sh from another location; remove or fix it first")
    if not isinstance(cmd, str) or not cmd.strip():
        raise ExportError("`statusLine` has no command to chain")
    sl["command"] = f"{ours['command']} {shlex.quote(cmd)}"
    report.append("statusLine: chained (the existing command runs first, its output kept)")
    return cmd


def _hooks(doc, report):
    ours = registrations()["hooks"]
    hooks = doc.get("hooks")
    if hooks is None:
        hooks = doc["hooks"] = {}
    if not isinstance(hooks, dict):
        raise ExportError("`hooks` is not an object")
    for event in HOOK_EVENTS:
        groups = hooks.get(event)
        if groups is None:
            groups = hooks[event] = []
        if not isinstance(groups, list):
            raise ExportError(f"`hooks.{event}` is not a list")
        found = []
        for g in groups:
            if not isinstance(g, dict) or not isinstance(g.get("hooks"), list):
                raise ExportError(f"`hooks.{event}` has a group without a `hooks` list")
            found += [_which(h.get("command") if isinstance(h, dict) else None, SCRIPTS[event]) for h in g["hooks"]]
        if "elsewhere" in found:
            raise ExportError(f"`hooks.{event}` runs {SCRIPTS[event]} from another location; remove or fix it first")
        n = found.count("ours")
        if n > 1:
            raise ExportError(f"`hooks.{event}` registers {SCRIPTS[event]} {n} times; leave one")
        if n == 1:
            report.append(f"hooks.{event}: already present")
        else:
            groups.extend(ours[event])
            report.append(f"hooks.{event}: added")


def _changed(orig, copy, path, chained):
    """The paths where copy does not keep orig (lists may only grow at the end)."""
    if path == "statusLine.command" and chained is not None:
        ok = isinstance(copy, str) and shlex.split(copy) == [str(config.TOOL / SCRIPTS["statusLine"]), chained]
        return [] if ok else [path]
    if isinstance(orig, dict):
        if not isinstance(copy, dict):
            return [path or "$"]
        out = []
        for k, v in orig.items():
            sub = f"{path}.{k}" if path else k
            out += _changed(v, copy[k], sub, chained) if k in copy else [sub]
        return out
    if isinstance(orig, list):
        if not isinstance(copy, list) or len(copy) < len(orig):
            return [path]
        return [p for i, v in enumerate(orig) for p in _changed(v, copy[i], f"{path}[{i}]", chained)]
    return [] if orig == copy and type(orig) is type(copy) else [path]


def _verify(original, text, chained):
    copy = json.loads(text, object_pairs_hook=_no_dupes)
    bad = _changed(original, copy, "", chained)
    if bad:
        raise ExportError(f"the copy would change {', '.join(bad[:5])}: refusing (a bug in usage, report it)")
    sl = copy.get("statusLine", {}).get("command")
    counts = {"statusLine": int(_which(sl, SCRIPTS["statusLine"]) == "ours")}
    for event in HOOK_EVENTS:
        counts[event] = sum(_which(h.get("command"), SCRIPTS[event]) == "ours"
                            for g in copy["hooks"][event] for h in g["hooks"])
    wrong = [k for k, n in counts.items() if n != 1]
    if wrong:
        raise ExportError(f"the copy carries {', '.join(wrong)} other than once: refusing (a bug in usage, report it)")


def render(doc):
    return json.dumps(doc, indent=2, ensure_ascii=False) + "\n"


def export(path):
    """(status, report lines, proposal path); raises ExportError, having written nothing."""
    path = Path(path).expanduser()
    target = path.with_name(path.name + SUFFIX)
    original = _load(path)
    doc = json.loads(json.dumps(original))
    report = []
    chained = _status_line(doc, report)
    _hooks(doc, report)
    text = render(doc)
    _verify(original, text, chained)
    if target.is_symlink() or (target.exists() and not target.is_file()):
        raise ExportError(f"{target.name} exists and is not a regular file; remove it first")
    if target.is_file():
        try:
            if target.read_text() == text:
                return "unchanged", report, target
        except (OSError, UnicodeDecodeError):
            pass
        status = "replaced"
    else:
        status = "wrote"
    mode = path.stat().st_mode & 0o777
    fd, tmp = tempfile.mkstemp(dir=str(target.parent), prefix=target.name + ".")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(text)
        os.chmod(tmp, mode)
        os.replace(tmp, target)
    except OSError as e:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise ExportError(f"could not write {target.name} ({type(e).__name__})")
    return status, report, target
