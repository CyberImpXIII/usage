"""`usage can-run <model>`: may this account run <model> now? The condition
`fable` of PLAN-routing-tree.md §16, in one place.

THE RECORDS. ~/.claude/usage/models.json maps each model to at most one record
per source, {model: {source: record}}; a record is {model, checked_at (UTC
ISO), result, evidence, source} and, for a hold, `until` (UTC ISO, or null when
the failure named no reset time). A writer replaces only its own source's
record, so a hit landing while a probe runs is never lost.

  set    `usage can-run --set <model> ok|no "<reason>"`: Jacob's own answer,
         dated now. ok | refused.
  probe  one `claude -p --model <model> --max-turns 1` call on a one-word
         prompt, read as stream-json. reply | refused | credits | hold.
  hit    the StopFailure reporter (reporters.failure) on `error: rate_limit`,
         for the model of the session's last assistant message. hold.

WHY A REPLY IS NOT `ok` (research rs-6, docs/RESEARCH-fable.md in
tools/research, from the docs, 2026-10-19): under `claude -p` a plan that bills
Fable to usage credits bills it without asking and gives an ordinary reply, so
a reply means ok OR credits, and every probe there spends credits. So `ok`
needs both a fresh `--set <model> ok` (the plan includes the model) and a fresh
probe reply (it is available now), and the probe runs only when a fresh
`--set ok` exists: without one there is no answer a probe could turn into `ok`.
A probe reads, from its output: a reply from <model> itself (a fallback model
answering is no reply) -> reply; "spend limit" (only usage credits pay for the
request) -> credits; "issue with the selected model" (no access, or no such
model) -> refused; a reset time (parse_reset) -> hold; anything else ->
refused, marked unverified (rs-6: the exit codes and stream are undocumented).
A probe that could not run (no CLI, timeout) records nothing.

THE TTL. A record is used only while younger than usage.json
probes.model_ttl_hours (24), whoever wrote it (a hand edit to an `ok` dated
yesterday is not trusted). A hold is used until its `until` (at most
HOLD_MAX_S after it was written, else it is a misread), or, with no `until`,
for the TTL. A record dated in the future, or not shaped as above, is unused.

THE ANSWER, the most cautious fresh record winning: hold <until> (any source),
else refused (a `set no`, or the probe's), else credits, else ok (a `set ok`
and a reply), else unknown. Exit: ok 0; hold, credits, refused 1; unknown 3.
Every answer but `ok` resolves to the fallback (§16 fail-safe).
"""
import json
import os
import re
import subprocess
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import config, store

RESULTS = {"set": ("ok", "refused"), "probe": ("reply", "refused", "credits", "hold"), "hit": ("hold",)}
EXIT = {"ok": 0, "hold": 1, "credits": 1, "refused": 1, "unknown": 3}
HOLD_MAX_S = 8 * 86400  # the longest window is weekly: a reset further off is a misread, not a hold
FUTURE_SLACK_S = 60  # a record dated later than this past now was not written by this clock
EVIDENCE_MAX = 300
TAIL_BYTES = 1 << 20  # how much of a transcript's end is read for the session's model
PROMPT = "ok"
# A full id, never an alias: the probe's reply is matched by prefix (a real
# probe of claude-haiku-4-5 answered as claude-haiku-4-5-20251001), and `fable`
# is no prefix of claude-fable-5-1, so an alias would read every reply as refused.
MODEL_ID = re.compile(r"^claude-[a-z0-9][a-z0-9.-]{0,120}$")
# Probe output -> result, first match wins (rs-6's documented texts, then a reset time).
SPEND_LIMIT = re.compile(r"spend limit", re.I)
NO_ACCESS = re.compile(r"issue with the selected model", re.I)


class Unreadable(Exception):
    """models.json exists but is not a JSON object."""


def ttl_s():
    return config.setting("probes")["model_ttl_hours"] * 3600


def _ttl_text():
    return f"probes.model_ttl_hours {config.setting('probes')['model_ttl_hours']}"


def _evidence(text):
    return store._cell(text)[:EVIDENCE_MAX]


# ------------------------------------------------------------ the records --

def read_records(d):
    """The whole file, {model: {source: record}}; {} when absent; Unreadable
    when it is not a JSON object."""
    try:
        data = json.loads((d / store.MODELS).read_text())
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as e:
        raise Unreadable(store.describe(e)) from None
    if not isinstance(data, dict):
        raise Unreadable(f"a JSON {type(data).__name__}, not an object")
    return data


def write_record(d, rec, replace_unreadable):
    """Replace one source's record for one model; the caller holds the store
    lock. An unreadable file is replaced whole only when the caller says so
    (the CLI, which says so on its output); a hook raises Unreadable instead,
    which hook.py records. Returns True when an unreadable file was replaced."""
    try:
        recs, replaced = read_records(d), False
    except Unreadable:
        if not replace_unreadable:
            raise
        recs, replaced = {}, True
    entry = recs.get(rec["model"])
    entry = dict(entry) if isinstance(entry, dict) else {}
    entry[rec["source"]] = rec
    recs[rec["model"]] = entry
    store.write_json(d, store.MODELS, recs)
    return replaced


def usable(rec, model, source, now):
    """(result, until) for a record that may be used at `now`, else
    (None, why). `until` is a hold's end (epoch) or None."""
    if rec is None:
        return None, "none recorded"
    at = store.epoch(rec.get("checked_at")) if isinstance(rec, dict) else None
    if (at is None or rec.get("model") != model or rec.get("source") != source
            or rec.get("result") not in RESULTS[source]):
        return None, "not shaped as a models.json record"
    if at > now + FUTURE_SLACK_S:
        return None, f"dated {rec['checked_at']}, in the future"
    if rec["result"] == "hold" and rec.get("until") is not None:
        u = store.epoch(rec.get("until"))
        if u is None or not at < u <= at + HOLD_MAX_S:
            return None, f"a hold until {rec.get('until')!r}, not within {HOLD_MAX_S // 86400} days of {rec['checked_at']}"
        return ("hold", u) if now < u else (None, f"the hold ended at {rec['until']}")
    if now - at < ttl_s():
        return rec["result"], None
    return None, f"checked {rec['checked_at']}, past the TTL ({_ttl_text()})"


def resolve(fresh):
    """The answer from the usable records {source: (result, until)}: (answer,
    until, why), or (None, None, why) when a probe could settle it."""
    holds = [(s, u) for s, (r, u) in fresh.items() if r == "hold"]
    if holds:
        known = [u for _, u in holds if u is not None]
        return "hold", (max(known) if known else None), "held by " + ", ".join(s for s, _ in holds)
    s, p = (fresh.get(k, (None, None))[0] for k in ("set", "probe"))
    if s == "refused":
        return "refused", None, "set no"
    if p in ("refused", "credits"):
        return p, None, f"the probe: {p}"
    if s != "ok":
        why = "a probe reply means ok or usage credits (rs-6)" if p == "reply" else "no answer from Jacob"
        return "unknown", None, f"{why}: ok needs `usage can-run --set <model> ok \"<reason>\"`, no probe run"
    if p == "reply":
        return "ok", None, "set ok, and the probe got a reply"
    return None, None, "set ok, and no fresh probe"


def describe(rec, result, until):
    if result == "hold":
        good = f"until {store.iso(until)}" if until else \
            f"no reset named, until {store.iso(store.epoch(rec['checked_at']) + ttl_s())}"
    else:
        good = f"good until {store.iso(store.epoch(rec['checked_at']) + ttl_s())}"
    return f"{rec['source']}: {result} at {rec['checked_at']}, {good}: {rec.get('evidence') or '(no evidence)'}"


def headline(answer, until=None):
    if answer == "hold":
        return f"hold {store.iso(until) if until else 'unknown'}"
    return answer


# ------------------------------------------------------------ can-run --

def can_run(model, allow_probe=True):
    """(answer, until, detail lines)."""
    try:
        d = store.root()
    except store.NoStore:
        return "unknown", None, [f"no store at {config.store_dir()} (usage init): nothing recorded, no probe run"]
    notes = []
    for attempt in (0, 1):
        try:
            entry = read_records(d).get(model)
        except Unreadable as e:
            entry = None
            notes.append(f"{store.MODELS} is unreadable ({e}): no record used")
        entry = entry if isinstance(entry, dict) else {}
        now, fresh, lines = config.now(), {}, []
        for source in RESULTS:
            result, until = usable(entry.get(source), model, source, now)
            if result is not None:
                fresh[source] = (result, until)
                lines.append(describe(entry[source], result, until))
            elif source in entry:
                lines.append(f"{source}: not used: {until}")
        answer, until, why = resolve(fresh)
        if answer is not None:
            return answer, until, notes + [why] + lines
        if attempt or not allow_probe:
            return "unknown", None, notes + [why + (": --no-probe" if not allow_probe else "")] + lines
        rec, failed = probe(model, d)
        if rec is None:
            return "unknown", None, notes + [why + f": {failed}, nothing recorded"] + lines
        with store.locked() as d:
            if write_record(d, rec, replace_unreadable=True):
                notes.append(f"{store.MODELS} was unreadable: replaced")
    raise AssertionError("unreachable")


def set_answer(model, yes, reason):
    """Jacob's answer. (record, note or None); NoStore without a store."""
    rec = {"model": model, "checked_at": store.iso(config.now()), "result": "ok" if yes else "refused",
           "evidence": _evidence(f"set: {reason}"), "source": "set"}
    with store.locked() as d:
        replaced = write_record(d, rec, replace_unreadable=True)
    return rec, (f"{store.MODELS} was unreadable: replaced" if replaced else None)


# ------------------------------------------------------------ the probe --

def probe(model, cwd):
    """One `claude -p` call. (record, None), or (None, why it could not run)."""
    cli = os.environ.get("USAGE_CLAUDE_CLI") or "claude"
    timeout = config.setting("probes")["probe_timeout_s"]
    args = [cli, "-p", "--model", model, "--max-turns", "1", "--output-format", "stream-json", "--verbose", PROMPT]
    try:
        r = subprocess.run(args, cwd=str(cwd), capture_output=True, text=True, timeout=timeout,
                           stdin=subprocess.DEVNULL)
    except FileNotFoundError:
        return None, f"no claude CLI to probe with ({cli})"
    except subprocess.TimeoutExpired:
        return None, f"the probe gave no answer within probes.probe_timeout_s {timeout}"
    except OSError as e:
        return None, f"the probe could not start: {store.describe(e)}"
    t = config.now()
    result, until, ev = classify(model, r.returncode, r.stdout, r.stderr, t)
    rec = {"model": model, "checked_at": store.iso(t), "result": result, "evidence": _evidence(ev),
           "source": "probe"}
    if result == "hold":
        rec["until"] = store.iso(until)
    return rec, None


def classify(model, code, out, err, t):
    """(result, until, evidence) of one probe's exit code and output."""
    models, res = read_stream(out)
    text = " ".join(x for x in (res.get("result") if isinstance(res, dict) and isinstance(res.get("result"), str)
                                else "", err.strip()) if x)
    if code == 0 and isinstance(res, dict) and res.get("is_error") is False and models \
            and all(m.startswith(model) for m in models):
        return "reply", None, f"replied as {', '.join(sorted(models))}: ok or usage credits (rs-6)"
    facts = [f"exit {code}", f"answered by {', '.join(sorted(models))}" if models else "no assistant message"]
    said = f"; said: {text}" if text else ""
    if SPEND_LIMIT.search(text):
        return "credits", None, "a spend limit: only usage credits pay for it (rs-6); " + "; ".join(facts) + said
    if NO_ACCESS.search(text):
        return "refused", None, "no access, or no such model (rs-6); " + "; ".join(facts) + said
    until = parse_reset(text, t)
    if until is not None:
        return "hold", until, "a limit with a reset; " + "; ".join(facts) + said
    return "refused", None, "unverified (rs-6: undocumented): no reply read as refused; " + "; ".join(facts) + said


def read_stream(text):
    """(set of claude models of the assistant messages, the result object or
    None) from `claude -p --output-format stream-json --verbose` output."""
    models, result = set(), None
    for line in text.splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if not isinstance(e, dict):
            continue
        if e.get("type") == "assistant" and isinstance(e.get("message"), dict):
            m = e["message"].get("model")
            if isinstance(m, str) and m.startswith("claude"):
                models.add(m)
        elif e.get("type") == "result":
            result = e
    return models, result


# ------------------------------------------------------------ the hold --

def hold_from_hit(d, data, t):
    """A rate-limit StopFailure: hold the session's model until the reset its
    text names. Called by reporters.failure under the store lock. Returns the
    record, or None when the session's model is not known (no transcript, or
    no assistant message from a claude model in its tail). An unreadable
    transcript (OSError) or models.json (Unreadable) raises: hook.py records
    the lost hold in failures.tsv, and the hits row is already written."""
    path = next((data[k] for k in ("agent_transcript_path", "transcript_path")
                 if isinstance(data.get(k), str) and data[k]), None)
    model = session_model(path) if path else None
    if model is None:
        return None
    texts = [data[k] for k in ("last_assistant_message", "error_details") if isinstance(data.get(k), str)]
    until = next((u for u in (parse_reset(x, t) for x in texts) if u is not None), None)
    rec = {"model": model, "checked_at": store.iso(t), "result": "hold",
           "until": store.iso(until) if until is not None else None,
           "evidence": _evidence("rate-limit hit: " + (" | ".join(texts) or "(no error text)")), "source": "hit"}
    write_record(d, rec, replace_unreadable=False)
    return rec


def session_model(path):
    """The model of the last assistant message from a claude model in the
    transcript's last TAIL_BYTES (`<synthetic>` error lines are skipped); None
    when there is none."""
    with open(path, "rb") as fh:
        fh.seek(0, os.SEEK_END)
        fh.seek(max(0, fh.tell() - TAIL_BYTES))
        lines = fh.read().splitlines()
    for raw in reversed(lines):
        try:
            e = json.loads(raw)
        except ValueError:
            continue
        msg = e.get("message") if isinstance(e, dict) and e.get("type") == "assistant" else None
        m = msg.get("model") if isinstance(msg, dict) else None
        if isinstance(m, str) and m.startswith("claude"):
            return m
    return None


_EPOCH = re.compile(r"\|\s*(\d{10})\b")
_ABS = re.compile(r"\bresets?\s+(?:at\s+)?(\d{4})-(\d\d)-(\d\d)[ T](\d{1,2}):(\d\d)(?::\d\d)?\s*(UTC|Z)?", re.I)
_CLOCK = re.compile(r"\bresets?\s+(?:at\s+)?(?:(mon|tue|wed|thu|fri|sat|sun)[a-z]*\.?\s+)?"
                    r"(\d{1,2})(?::(\d\d))?\s*(am|pm)(?:\s*\(([A-Za-z_]+(?:/[A-Za-z0-9_+-]+)*)\))?", re.I)
_DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def parse_reset(text, t):
    """The reset time (epoch) a limit message names, or None. Forms:
    `...|<epoch>`; `resets 2026-10-20 00:00 UTC`; and Claude Code's own
    `resets 3:45pm`, `resets Mon 12:00am` (errors#youve-hit-your-session-limit),
    optionally `(Area/City)`: the next such clock time after `t`, in that zone,
    else the local one (where Claude Code printed it). A zone named but not
    known is None, never read as local. Only a time after `t` and within
    HOLD_MAX_S of it is returned."""
    u = None
    if (m := _EPOCH.search(text)):
        u = float(m.group(1))
    elif (m := _ABS.search(text)):
        y, mo, dd, hh, mi = (int(x) for x in m.groups()[:5])
        try:
            u = (store.epoch(f"{y:04d}-{mo:02d}-{dd:02d}T{hh:02d}:{mi:02d}:00Z") if m.group(6)
                 else time.mktime((y, mo, dd, hh, mi, 0, 0, 0, -1)))
        except (OverflowError, ValueError):
            u = None
    elif (m := _CLOCK.search(text)):
        u = _next_clock(m, t)
    if u is None or not t < u <= t + HOLD_MAX_S:
        return None
    return u


def _next_clock(m, t):
    day, hh, mi, ampm, zone = m.groups()
    hh, mi = int(hh), int(mi or 0)
    if not (1 <= hh <= 12 and 0 <= mi <= 59):
        return None
    hh = hh % 12 + (12 if ampm.lower() == "pm" else 0)
    tz = None
    if zone:
        try:
            tz = ZoneInfo(zone)
        except (ZoneInfoNotFoundError, ValueError):
            return None
    base = datetime.fromtimestamp(t, tz) if tz else datetime.fromtimestamp(t)
    for add in range(8):
        c = (base + timedelta(days=add)).replace(hour=hh, minute=mi, second=0, microsecond=0)
        if day and _DAYS[c.weekday()] != day.lower()[:3]:
            continue
        u = c.timestamp() if tz else time.mktime(c.timetuple()[:8] + (-1,))
        if u > t:
            return u
    return None
