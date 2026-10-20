"""The questions asked of the store: burn, projection, the gate, hits and the
calibration proposal. Pure functions over rows; cli.py prints them.

A window's samples are cut into instances: a new instance starts where the
percentage drops, or where a row's time has passed the previous row's
resets_at. Burn and %-per-token are measured inside one instance only.

Tokens are input + output + cache read + cache write of `turn` rows (never
`backlog`: those were counted when a transcript was first seen, at a time
that is not when they were spent).
"""
import math

from . import config, store

WINDOW_KEYS = {"5h": "five_hour", "7d": "seven_day"}


def _f(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def samples(key):
    """[(t, pct, resets_at|None)] for one window, oldest first."""
    out = []
    for r in store.read("samples.tsv"):
        t, p = store.epoch(r["time"]), _f(r[f"{key}_pct"])
        if t is None or p is None:
            continue
        out.append((t, p, _f(r[f"{key}_resets_at"])))
    out.sort(key=lambda x: x[0])
    return out


def instances(rows):
    segs = []
    for row in rows:
        if segs:
            prev = segs[-1][-1]
            new = row[1] < prev[1] or (prev[2] is not None and row[0] >= prev[2])
            if not new:
                segs[-1].append(row)
                continue
        segs.append([row])
    return segs


def token_rows():
    out = []
    for r in store.read("tokens.tsv"):
        t = store.epoch(r["time"])
        vals = [_f(r[c]) for c in ("input", "output", "cache_read", "cache_write")]
        if t is None or any(v is None for v in vals):
            continue
        out.append({"t": t, "session": r["session"], "agent_type": r["agent_type"], "model": r["model"],
                    "basis": r["basis"], "input": vals[0], "output": vals[1],
                    "cache_read": vals[2], "cache_write": vals[3], "total": sum(vals)})
    return out


def tokens_between(t0, t1, rows=None):
    rows = token_rows() if rows is None else rows
    return sum(r["total"] for r in rows if r["basis"] == "turn" and t0 < r["t"] <= t1)


def burn(window, n=None):
    """{pct_per_hour, tokens_per_hour, n, t0, t1, last_pct, resets_at} over the last
    n samples of the current instance, or None with fewer than 2 / no time span."""
    n = n or config.setting("burn_samples")
    segs = instances(samples(WINDOW_KEYS[window]))
    if not segs:
        return None
    seg = segs[-1][-n:]
    if len(seg) < 2 or seg[-1][0] <= seg[0][0]:
        return None
    hours = (seg[-1][0] - seg[0][0]) / 3600
    tok = tokens_between(seg[0][0], seg[-1][0])
    return {"pct_per_hour": (seg[-1][1] - seg[0][1]) / hours, "tokens_per_hour": tok / hours,
            "n": len(seg), "t0": seg[0][0], "t1": seg[-1][0], "last_pct": seg[-1][1],
            "resets_at": seg[-1][2]}


def project(window, threshold):
    """(kind, epoch|None): kind is at, already, never, resets-first or unknown."""
    b = burn(window)
    if b is None:
        return "unknown", None
    if b["last_pct"] >= threshold:
        return "already", b["t1"]
    if b["pct_per_hour"] <= 0:
        return "never", None
    at = b["t1"] + (threshold - b["last_pct"]) / b["pct_per_hour"] * 3600
    if b["resets_at"] is not None and b["resets_at"] <= at:
        return "resets-first", b["resets_at"]
    return "at", at


def pct_per_token(window):
    """(ratio, basis) from the current instance; else from every instance on
    record; (None, reason) when no instance has both a rise and tokens."""
    rows = token_rows()
    segs = instances(samples(WINDOW_KEYS[window]))
    if segs:
        cur = segs[-1]
        dp = cur[-1][1] - cur[0][1]
        tok = tokens_between(cur[0][0], cur[-1][0], rows)
        if dp > 0 and tok > 0:
            return dp / tok, "this window"
    dp_sum = tok_sum = used = 0
    for seg in segs:
        dp = seg[-1][1] - seg[0][1]
        tok = tokens_between(seg[0][0], seg[-1][0], rows)
        if dp > 0 and tok > 0:
            dp_sum, tok_sum, used = dp_sum + dp, tok_sum + tok, used + 1
    if used:
        return dp_sum / tok_sum, f"history ({used} window(s))"
    return None, "no window yet shows both a rise and recorded tokens"


def latest():
    """(sample dict, sampled_at) from limits.json, or (None, None)."""
    j = store.read_json(store.LIMITS)
    if not isinstance(j, dict) or not isinstance(j.get("sample"), dict):
        return None, None
    at = j.get("sampled_at")
    return j["sample"], (at if isinstance(at, (int, float)) else None)


def windows_now(sample):
    """{label: {pct, resets_at, resets_iso}} of one sample, every window of
    WINDOW_KEYS present; a value not reported, or not a finite number, is None
    (never 0). `usage status` and the gate read a sample only through this."""
    out = {}
    for label, key in WINDOW_KEYS.items():
        r = _f(sample.get(f"{key}_resets_at"))
        r = int(r) if r is not None else None
        out[label] = {"pct": _f(sample.get(f"{key}_pct")), "resets_at": r,
                      "resets_iso": store.iso(r) if r is not None else None}
    return out


# `usage gate --json` prints these fields, every one present (null when not known).
GATE_FIELDS = ("verdict", "exit", "line", "tokens", "hold_until", "hold_until_iso", "window", "over",
               "sample_age_min", "warn", "hold", "windows")
UNCHECKED = 3  # gate --todo: no estimate, so no answer (never a number)


def gate_result(tokens, verdict, code, line):
    """The gate's answer before any window is read; gate() fills it in."""
    return {"verdict": verdict, "exit": code, "line": line, "detail": [], "tokens": tokens, "hold_until": None,
            "hold_until_iso": None, "window": None, "over": [], "sample_age_min": None,
            "warn": config.setting("warn"), "hold": config.setting("hold"), "windows": {}}


def gate(tokens):
    """The verdict as fields; cli.py prints `line` and `detail` (text) or
    GATE_FIELDS (--json). verdict: pass or warn (exit 0); hold, or unknown
    when there is no fresh sample or no measured %/token (exit 1).
    over: the windows at or past hold after the job (warn: past warn).
    hold_until (epoch) and window: the reset the hold waits for (the latest of
    the holding windows' resets) and its window; both null unless every
    holding window's reset is known (prefer null to a guess).
    windows: {label: {pct, resets_at, resets_iso, after, pct_per_mtok, basis}},
    after/pct_per_mtok/basis null for a window not reported."""
    g = gate_result(tokens, "unknown", 1, None)
    warn, hold = g["warn"], g["hold"]

    def unknown(why):
        g["line"] = f"unknown, hold: {why}"
        return g

    sample, at = latest()
    stale = config.setting("sample_stale_minutes")
    if sample is None or at is None:
        return unknown("no status-line sample yet")
    age_min = (config.now() - at) / 60
    g["sample_age_min"] = round(age_min, 1)
    if age_min > stale:
        return unknown(f"the last sample is {age_min:.0f} min old (stale after {stale})")
    holds, warns, detail, seen = [], [], g["detail"], 0
    for label, w in windows_now(sample).items():
        g["windows"][label] = w = dict(w, after=None, pct_per_mtok=None, basis=None)
        p = w["pct"]
        if p is None:
            detail.append(f"{label}: not reported")
            continue
        seen += 1
        ratio, basis = pct_per_token(label)
        w["basis"] = basis
        if ratio is None:
            return unknown(f"{label} has no measured %/token ({basis})")
        after = p + tokens * ratio
        w["after"], w["pct_per_mtok"] = after, ratio * 1e6
        detail.append(f"{label}: {p:.1f}% now, ~{after:.1f}% after {tokens:,} tokens "
                      f"({ratio * 1e6:.4f}% per 1M, {basis})")
        if p >= hold or after >= hold:
            holds.append((label, w["resets_at"]))
        elif after >= warn:
            warns.append(label)
    if not seen:
        return unknown("the sample reports no window")
    if holds:
        g["verdict"], g["over"] = "hold", [lb for lb, _ in holds]
        times = [r for _, r in holds if r is not None]
        names = ", ".join(g["over"])
        if len(times) < len(holds):
            g["line"] = f"hold ({names} would reach hold {hold}%; reset time unknown)"
            return g
        until = max(times)
        g["hold_until"], g["hold_until_iso"] = until, store.iso(until)
        g["window"] = next(lb for lb, r in holds if r == until)
        g["line"] = f"hold until {store.iso(until)} ({names} would reach hold {hold}%)"
        return g
    g["exit"] = 0
    if warns:
        g["verdict"], g["over"] = "warn", warns
        g["line"] = f"pass, warn: {', '.join(warns)} past warn {warn}% after this job"
        return g
    g["verdict"], g["line"] = "pass", "pass"
    return g


def hits(since=None):
    """[{time, t, session, agent_type, pct, five_hour_pct, seven_day_pct,
    sample_time, age_min, flag, window, resets_at}] -- flag: below-hold,
    stale-sample, no-sample or ''. pct is the larger of the two windows.

    window/resets_at: the window that was hit and when it resets, from the
    sample recorded with the hit. Given only when that sample is fresh and
    exactly one window is at or past `hold` and its reset is after the hit;
    otherwise both are None (two windows high, or neither: which one stopped
    the session is not known, and a guessed reset is a wrong answer)."""
    hold, stale = config.setting("hold"), config.setting("sample_stale_minutes")
    out = []
    for r in store.read("hits.tsv"):
        if since and r["time"][:10] < since:
            continue
        by = {w: (_f(r[f"{k}_pct"]), _f(r[f"{k}_resets_at"])) for w, k in WINDOW_KEYS.items()}
        ps = [p for p, _ in by.values() if p is not None]
        t, st = store.epoch(r["time"]), store.epoch(r["sample_time"])
        pct = max(ps) if ps else None
        age = (t - st) / 60 if t is not None and st is not None else None
        if pct is None or age is None:
            flag = "no-sample"
        elif age > stale:
            flag = "stale-sample"
        elif pct < hold:
            flag = "below-hold"
        else:
            flag = ""
        window = resets = None
        at_hold = [w for w, (p, _) in by.items() if p is not None and p >= hold]
        if flag == "" and len(at_hold) == 1:
            r_at = by[at_hold[0]][1]
            if r_at is not None and t is not None and r_at > t:
                window, resets = at_hold[0], int(r_at)
        out.append({"time": r["time"], "t": int(t) if t is not None else None, "session": r["session"],
                    "agent_type": r["agent_type"], "pct": pct, "five_hour_pct": by["5h"][0],
                    "seven_day_pct": by["7d"][0], "sample_time": r["sample_time"] or None,
                    "age_min": age, "flag": flag, "window": window, "resets_at": resets})
    return out


def calibrate():
    """(proposal dict or None, explanation lines). Never writes usage.json."""
    warn, hold, margin = config.setting("warn"), config.setting("hold"), config.setting("calibrate_margin")
    all_hits = hits()
    below = [h["pct"] for h in all_hits if h["flag"] == "below-hold"]
    if not below:
        return None, [f"no evidence to move the thresholds: {len(all_hits)} hit(s), none below hold {hold}% "
                      f"with a fresh sample; warn {warn}, hold {hold} stay"]
    new_hold = max(0, math.floor(min(below)) - margin)
    new_warn = max(0, min(warn, new_hold - (hold - warn)))
    lines = [f"{len(below)} hit(s) arrived below hold {hold}%: " + ", ".join(f"{p:.1f}%" for p in sorted(below)),
             f"propose: hold {hold} -> {new_hold}, warn {warn} -> {new_warn} "
             f"(lowest hit, floored, minus calibrate_margin {margin}; warn keeps its gap of {hold - warn})",
             "usage.json is unchanged: a change there is a commit a person reads"]
    return {"warn": new_warn, "hold": new_hold}, lines


def tokens_by(by, since=None):
    key = {"agent": "agent_type", "model": "model", "session": "session"}[by]
    acc = {}
    for r in token_rows():
        if since and store.iso(r["t"])[:10] < since:
            continue
        k = r[key]
        a = acc.setdefault(k, {"rows": 0, "input": 0, "output": 0, "cache_read": 0, "cache_write": 0, "total": 0})
        a["rows"] += 1
        for c in ("input", "output", "cache_read", "cache_write", "total"):
            a[c] += r[c]
    return acc
