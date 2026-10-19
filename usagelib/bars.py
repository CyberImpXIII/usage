"""The usage bars (PLAN-routing-tree.md §27): bars.json is the registry, one row
per bar, and `usage bars [--json]` evaluates every row into
{name, label, pct, window, asof, age_s, source, state, reason}.

state is `ok`; `stale` (the value is older than the row's `refresh`: pct is
kept and age_s says how old); or `unknown` (pct null, reason says why). A
source that did not answer is never 0 and never its last value unmarked.

A row is {name, label, window, refresh, source}; its source is one of:

  {"window": "<key>"}   one of this tool's own windows (a value of
                        calc.WINDOW_KEYS), from the latest status-line sample,
                        read through calc.latest() exactly as `usage status`
                        reads it.
  {"service": "<tool>", "verb": "<verb>", "key": "<k>", "timeout_s": n,
   "args": [...], "asof_key": "<k>"}  (args, asof_key optional)
                        the CLI that <tools>/<tool>/cli.json declares, run
                        with a verb that cli.json lists; number <k> of the JSON
                        object it prints is the pct (0..100), and asof_key, if
                        given, its time (epoch or ISO UTC); else the time of
                        the call. Bounded by timeout_s.
  {"pending": "<why>"}  declared, with no source yet: always unknown, saying why.

problems() is the one validator: `usage bars` marks an invalid row unknown
with its problem, and devtools/check.py's `bars` gate fails on any. No model
anywhere in the path.
"""
import json
import math
import os
import re
import subprocess
from pathlib import Path

from . import calc, config, store

REGISTRY_ENV = "USAGE_BARS"        # another registry (the tests' fixtures)
TOOLS_ENV = "USAGE_TOOLS_DIR"      # where services live; default: this repo's parent
WINDOWS = ("5h", "7d", "session", "day")
ROW_KEYS = ("name", "label", "window", "refresh", "source")
SERVICE_KEYS = {"service", "verb", "key", "timeout_s"}
SERVICE_OPTIONAL = {"args", "asof_key"}
MAX_TIMEOUT_S = 120
NAME = re.compile(r"^[a-z][a-z0-9-]{0,39}$")
FAIL, UNCHECKED = "fail", "unchecked"
OUTPUT = ("name", "label", "pct", "window", "asof", "age_s", "source", "state", "reason")


def registry():
    v = os.environ.get(REGISTRY_ENV)
    return Path(v) if v else config.TOOL / "bars.json"


def tools_dir():
    v = os.environ.get(TOOLS_ENV)
    return Path(v) if v else config.TOOL.parent


def load(path=None):
    """(doc, None), or (None, why) when the registry cannot be read as JSON."""
    path = path or registry()
    try:
        return json.loads(path.read_text()), None
    except OSError as e:
        return None, f"{path}: cannot read ({type(e).__name__})"
    except ValueError as e:
        return None, f"{path}: not JSON ({e})"


# ------------------------------------------------------------- validation --

def problems(doc, tools=None):
    """[(where, message, kind)], kind FAIL or UNCHECKED (a named service whose
    folder is absent: a lone clone cannot judge it). [] for a good registry."""
    if not isinstance(doc, dict) or not isinstance(doc.get("bars"), list):
        return [("bars.json", 'not an object with a "bars" list', FAIL)]
    out = [("bars.json", f"unknown top-level key {k!r}", FAIL) for k in sorted(set(doc) - {"_comment", "bars"})]
    if not doc["bars"]:
        out.append(("bars.json", "no rows", FAIL))
    seen = set()
    for i, row in enumerate(doc["bars"]):
        name = row.get("name") if isinstance(row, dict) else None
        where = name if isinstance(name, str) and name else f"row {i + 1}"
        if isinstance(name, str) and name in seen:
            out.append((where, "name given twice", FAIL))
        seen.add(name)
        out += [(where, m, k) for m, k in row_problems(row, tools)]
    return out


def row_problems(row, tools=None):
    """[(message, kind)] for one row."""
    if not isinstance(row, dict):
        return [("not an object", FAIL)]
    out = [(f"unknown key {k!r}", FAIL) for k in sorted(set(row) - set(ROW_KEYS))]
    out += [(f"missing key {k!r}", FAIL) for k in ROW_KEYS if k not in row]
    if "name" in row and not (isinstance(row["name"], str) and NAME.match(row["name"])):
        out.append(("name is not lower-case letters, digits and -", FAIL))
    if "label" in row and not (isinstance(row["label"], str) and row["label"].strip()):
        out.append(("label is empty", FAIL))
    if "window" in row and row["window"] not in WINDOWS:
        out.append((f"window is not one of {', '.join(WINDOWS)}", FAIL))
    if "refresh" in row and not (_int(row["refresh"]) and row["refresh"] > 0):
        out.append(("refresh is not a whole number of seconds above 0", FAIL))
    if "source" in row:
        out += source_problems(row["source"], tools)
    return out


def source_problems(src, tools=None):
    if not isinstance(src, dict):
        return [("source is not an object", FAIL)]
    keys = set(src)
    if keys == {"window"}:
        if src["window"] not in calc.WINDOW_KEYS.values():
            return [(f"source window {src['window']!r} is not one of this tool's windows "
                     f"({', '.join(calc.WINDOW_KEYS.values())})", FAIL)]
        return []
    if keys == {"pending"}:
        return [] if isinstance(src["pending"], str) and src["pending"].strip() else [("pending gives no reason", FAIL)]
    if "service" in keys:
        out = [(f"service source: unknown key {k!r}", FAIL) for k in sorted(keys - SERVICE_KEYS - SERVICE_OPTIONAL)]
        out += [(f"service source: missing key {k!r}", FAIL) for k in sorted(SERVICE_KEYS - keys)]
        if out:
            return out
        if not (isinstance(src["service"], str) and NAME.match(src["service"])):
            out.append(("service is not a tool folder name", FAIL))
        for k in ("verb", "key"):
            if not (isinstance(src[k], str) and src[k] and not src[k].startswith("-")):
                out.append((f"{k} is empty or a flag", FAIL))
        if "asof_key" in src and not (isinstance(src["asof_key"], str) and src["asof_key"]):
            out.append(("asof_key is empty", FAIL))
        if "args" in src and not (isinstance(src["args"], list) and all(isinstance(a, str) for a in src["args"])):
            out.append(("args is not a list of strings", FAIL))
        if not (_int(src["timeout_s"]) and 0 < src["timeout_s"] <= MAX_TIMEOUT_S):
            out.append((f"timeout_s is not a whole number in 1..{MAX_TIMEOUT_S}", FAIL))
        return out or _verb_exists(src, tools)
    return [('source is none of {"window"}, {"service", "verb", ...} or {"pending"}', FAIL)]


def _verb_exists(src, tools=None):
    """The seam: the service declares the verb in its cli.json and its CLI is there."""
    d = (tools or tools_dir()) / src["service"]
    if not d.is_dir():
        return [(f"service {src['service']!r} not found at {d}", UNCHECKED)]
    try:
        spec = json.loads((d / "cli.json").read_text())
    except (OSError, ValueError) as e:
        return [(f"service {src['service']!r} has no readable cli.json ({type(e).__name__}): "
                 "its verbs cannot be shown to exist", FAIL)]
    verbs = spec.get("verbs") if isinstance(spec, dict) else None
    cli = spec.get("cli") if isinstance(spec, dict) else None
    out = []
    if not isinstance(verbs, list) or src["verb"] not in verbs:
        out.append((f"verb {src['verb']!r} is not one of {src['service']}'s verbs (its cli.json)", FAIL))
    if not (isinstance(cli, str) and cli and "/" not in cli and os.access(d / cli, os.X_OK)):
        out.append((f"{src['service']}'s cli.json names no executable CLI in its folder", FAIL))
    return out


def _int(v):
    return isinstance(v, int) and not isinstance(v, bool)


# ------------------------------------------------------------- evaluation --

def describe(src):
    """The `source` field of the output: where the number comes from."""
    if not isinstance(src, dict):
        return None
    if "window" in src:
        return f"rate_limits.{src['window']}"
    if "service" in src:
        return " ".join(str(x) for x in [src.get("service"), src.get("verb"), *(src.get("args") or [])]) \
            + f" :{src.get('key')}"
    if "pending" in src:
        return "pending"
    return None


def evaluate(doc, tools=None):
    """One output dict per row of doc (a dict with a "bars" list), in order."""
    now = config.now()
    out = []
    for row in doc["bars"]:
        r = row if isinstance(row, dict) else {}
        bar = {"name": r.get("name"), "label": r.get("label"), "pct": None, "window": r.get("window"),
               "asof": None, "age_s": None, "source": describe(r.get("source")), "state": "unknown",
               "reason": None}
        bad = row_problems(row, tools)
        if bad:
            bar["reason"] = "bars.json: " + "; ".join(m for m, _ in bad)
            out.append(bar)
            continue
        pct, at, why = read(r["source"], now, tools)
        if why is None and at > now + 60:
            why = f"its time {store.iso(at)} is in the future"
        if why is not None:
            bar["reason"] = why
            out.append(bar)
            continue
        age = max(0, int(now - at))
        bar.update(pct=pct, asof=store.iso(at), age_s=age, state="ok")
        if age > r["refresh"]:
            bar.update(state="stale", reason=f"{_age(age)} old (refresh {r['refresh']} s)")
        out.append(bar)
    return out


def read(src, now, tools=None):
    """(pct, epoch, None) or (None, None, why)."""
    if "window" in src:
        return _window(src["window"])
    if "pending" in src:
        return None, None, f"no source yet: {src['pending']}"
    return _service(src, now, tools)


def _window(key):
    d = config.store_dir()
    if not d.is_dir():
        return None, None, f"no store at {d} (usage init)"
    sample, at = calc.latest()
    if sample is None:
        return None, None, "no status-line sample yet"
    if at is None:
        return None, None, "the latest sample has no time"
    p = calc._f(sample.get(f"{key}_pct"))
    if p is None:
        return None, None, f"rate_limits.{key} is not in the sample of {store.iso(at)}"
    return p, at, None


def _service(src, now, tools=None):
    d = (tools or tools_dir()) / src["service"]
    name = f"{src['service']} {src['verb']}"
    try:
        cli = d / json.loads((d / "cli.json").read_text())["cli"]
        r = subprocess.run([str(cli), src["verb"], *src.get("args", [])], cwd=d, capture_output=True, text=True,
                           timeout=src["timeout_s"], stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return None, None, f"{name} gave no answer within {src['timeout_s']} s"
    except (OSError, ValueError, KeyError, TypeError) as e:
        return None, None, f"{name} did not run ({type(e).__name__})"
    if r.returncode != 0:
        return None, None, f"{name} exit {r.returncode}"
    try:
        doc = json.loads(r.stdout)
    except ValueError:
        return None, None, f"{name} printed no JSON"
    if not isinstance(doc, dict):
        return None, None, f"{name} printed JSON that is not an object"
    key = src["key"]
    if key not in doc:
        return None, None, f"{name}: key {key!r} is absent from its output"
    v = doc[key]
    if v is None:
        return None, None, f"{name}: {key} is null (the source does not know)"
    p = None if isinstance(v, bool) or not isinstance(v, (int, float)) else float(v)
    if p is None or not math.isfinite(p):
        return None, None, f"{name}: {key} is not a number"
    if not 0 <= p <= 100:
        return None, None, f"{name}: {key} is outside 0..100"
    at = now
    if "asof_key" in src:
        a = doc.get(src["asof_key"])
        at = store.epoch(a) if isinstance(a, str) else (
            float(a) if isinstance(a, (int, float)) and not isinstance(a, bool) and math.isfinite(a) else None)
        if at is None:
            return None, None, f"{name}: {src['asof_key']} is absent or not a time"
    return p, at, None


def rate_limit_keys():
    """(sorted keys of the latest raw rate_limits object, its time), or (None, None).
    Names only, never values: the live half of the per-model window probe."""
    j = store.read_json(store.LIMITS)
    if not isinstance(j, dict) or not isinstance(j.get("rate_limits"), dict):
        return None, None
    at = j.get("sampled_at")
    return sorted(j["rate_limits"]), (at if isinstance(at, (int, float)) else None)


def _age(s):
    return f"{s // 3600} h {s % 3600 // 60} min" if s >= 3600 else f"{s // 60} min" if s >= 60 else f"{s} s"
