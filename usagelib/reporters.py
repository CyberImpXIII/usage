"""The three reporters, one function per event. Each takes the parsed hook or
status-line input and either writes its rows or raises; hook.py turns every
raise into "exit 0, nothing on stderr" (fail open) and records it with
store.record_failure(), so a lost row leaves a trace (`usage failures`). The
status line, which prints whatever happens to the store, also says so on the
line itself (NOT_RECORDED).

  statusline(data) -> the one-line status (str, may be empty)
  stop(data)       -> Stop / SubagentStop: tokens.tsv rows for the turn's delta
  failure(data)    -> StopFailure: one hits.tsv row, only for error == rate_limit
"""
import hashlib
import json
import time
from pathlib import Path

from . import config, store

WINDOWS = (("5h", "five_hour"), ("7d", "seven_day"))
RATE_LIMIT_ERROR = "rate_limit"
NOT_RECORDED = " | usage: not recorded"


# ------------------------------------------------------------- status line --

def _pct(win):
    if not isinstance(win, dict):
        return None
    v = win.get("used_percentage")
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v)


def _resets(win):
    if not isinstance(win, dict):
        return None
    v = win.get("resets_at")
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return int(v)


def fmt_pct(p):
    return f"{p:.0f}%" if p is not None else "--"


def fmt_local(epoch_s, fmt="%H:%M"):
    return time.strftime(fmt, time.localtime(epoch_s)) if epoch_s is not None else "?"


def marker(p):
    if p is None:
        return ""
    if p >= config.setting("hold"):
        return " HOLD"
    if p >= config.setting("warn"):
        return " warn"
    return ""


def status_text(sample):
    """One line from a sample {five_hour_pct, seven_day_pct, *_resets_at}."""
    parts = []
    for label, key in WINDOWS:
        p = sample.get(f"{key}_pct")
        if p is None:
            continue
        r = sample.get(f"{key}_resets_at")
        when = fmt_local(r, "%H:%M" if label == "5h" else "%a %H:%M") if r else "?"
        parts.append(f"{label} {fmt_pct(p)}{marker(p)} (resets {when})")
    return " | ".join(parts)


def parse_limits(data):
    """{five_hour_pct, seven_day_pct, five_hour_resets_at, seven_day_resets_at} or
    None when the input carries no window with a used_percentage."""
    rl = data.get("rate_limits") if isinstance(data, dict) else None
    if not isinstance(rl, dict):
        return None
    out = {}
    for _, key in WINDOWS:
        out[f"{key}_pct"] = _pct(rl.get(key))
        out[f"{key}_resets_at"] = _resets(rl.get(key))
    if out["five_hour_pct"] is None and out["seven_day_pct"] is None:
        return None
    return out


def statusline(data):
    sample = parse_limits(data)
    if sample is None:
        return ""
    line = status_text(sample)
    try:
        record_sample(data["rate_limits"], sample)
    except store.NoStore:  # not set up: nothing to record, nothing lost
        pass
    except Exception as e:  # noqa: BLE001 -- the line is still printed; the store is what failed
        store.record_failure("statusline", "", e)
        line += NOT_RECORDED
    return line


def record_sample(raw, sample):
    t = config.now()
    with store.locked() as d:
        prev = store.read_json(store.LIMITS) or {}
        values = [sample[f"{k}_{f}"] for _, k in WINDOWS for f in ("pct", "resets_at")]
        last_row_at = prev.get("row_written_at")
        heartbeat = config.setting("sample_heartbeat_minutes") * 60
        due = (prev.get("row_values") != values or not isinstance(last_row_at, (int, float))
               or t - last_row_at >= heartbeat)
        if due:
            store.append(d, "samples.tsv", [dict(sample, time=store.iso(t))])
            last_row_at = t
        # The raw object is kept whole: the first one answers which windows the
        # plan reports (PLAN-usage-reporting.md §1, unknowns).
        store.write_json(d, store.LIMITS, {
            "sampled_at": t, "sample": sample, "rate_limits": raw,
            "row_written_at": last_row_at, "row_values": values})


# ---------------------------------------------------------------- tokens --

def _state_file(d, transcript):
    h = hashlib.sha1(str(transcript).encode()).hexdigest()[:20]
    return d / "state" / f"{h}.json"


def scan(transcript, state, now_s):
    """Read the transcript from state's offset; count each API message once, at
    the max of each usage field across its streamed lines (the same rule as the
    workspace's transcript reader), as a delta against what earlier runs counted.
    Returns (new_state, {(model, basis): [in, out, cache_read, cache_write]}).
    A message whose timestamp is older than sample_stale_minutes is `backlog`
    (a transcript first seen mid-session), never `turn`."""
    path = Path(transcript)
    size = path.stat().st_size
    offset = state.get("offset", 0)
    seen = dict(state.get("seen", {}))
    if size < offset:  # the file was rewritten: re-read it; seen keeps the counts
        offset = 0
    with open(path, "rb") as fh:
        fh.seek(offset)
        data = fh.read()
    end = data.rfind(b"\n")
    if end < 0:
        return state, {}
    new_offset = offset + end + 1
    stale = config.setting("sample_stale_minutes") * 60
    sums = {}
    for raw in data[:end + 1].splitlines():
        try:
            e = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(e, dict) or e.get("type") != "assistant":
            continue
        msg = e.get("message") if isinstance(e.get("message"), dict) else {}
        usage, model = msg.get("usage"), msg.get("model") or ""
        if not isinstance(usage, dict) or not str(model).startswith("claude"):
            continue
        mid = msg.get("id") or e.get("uuid") or ""
        vals = [int(usage.get(k) or 0) for k in
                ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")]
        prev = seen.get(mid, [0, 0, 0, 0])
        new = [max(a, b) for a, b in zip(prev, vals)]
        delta = [n - p for n, p in zip(new, prev)]
        seen[mid] = new
        if not any(delta):
            continue
        ts = store.epoch(str(e.get("timestamp", ""))[:19] + "Z") if e.get("timestamp") else None
        basis = "backlog" if ts is not None and now_s - ts > stale else "turn"
        acc = sums.setdefault((model, basis), [0, 0, 0, 0])
        for i, v in enumerate(delta):
            acc[i] += v
    return {"path": str(path), "offset": new_offset, "seen": seen}, sums


def stop(data):
    if not isinstance(data, dict):
        return
    if data.get("hook_event_name") == "SubagentStop":
        transcript = data.get("agent_transcript_path")
    else:
        transcript = data.get("transcript_path")
    if not isinstance(transcript, str) or not transcript:
        return
    agent = data.get("agent_type") if isinstance(data.get("agent_type"), str) else ""
    session = data.get("session_id") if isinstance(data.get("session_id"), str) else ""
    t = config.now()
    with store.locked() as d:
        sf = _state_file(d, transcript)
        try:
            state = json.loads(sf.read_text())
        except (OSError, ValueError):
            state = {}
        new_state, sums = scan(transcript, state, t)
        if new_state.get("offset") == state.get("offset") and not sums:
            return
        rows = [{"time": store.iso(t), "session": session, "agent_type": agent, "model": model,
                 "input": v[0], "output": v[1], "cache_read": v[2], "cache_write": v[3], "basis": basis}
                for (model, basis), v in sorted(sums.items())]
        if rows:
            store.append(d, "tokens.tsv", rows)
        sf.parent.mkdir(exist_ok=True)
        store.write_json(sf.parent, sf.name, new_state)


# ---------------------------------------------------------------- hits --

def failure(data):
    if not isinstance(data, dict) or data.get("error") != RATE_LIMIT_ERROR:
        return
    t = config.now()
    with store.locked() as d:
        last = store.read_json(store.LIMITS) or {}
        sample = last.get("sample") or {}
        at = last.get("sampled_at")
        store.append(d, "hits.tsv", [{
            "time": store.iso(t),
            "session": data.get("session_id") if isinstance(data.get("session_id"), str) else "",
            "agent_type": data.get("agent_type") if isinstance(data.get("agent_type"), str) else "",
            "five_hour_pct": sample.get("five_hour_pct"),
            "seven_day_pct": sample.get("seven_day_pct"),
            "sample_time": store.iso(at) if isinstance(at, (int, float)) else None,
            "five_hour_resets_at": sample.get("five_hour_resets_at"),
            "seven_day_resets_at": sample.get("seven_day_resets_at"),
        }])
