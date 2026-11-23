#!/usr/bin/env python3
"""Measure token usage across the five T3 harnesses and T3 itself.

Reads only numeric usage fields: never message bodies, .env, auth or
session text. Prints a human summary and can dump the normalized rows.

Sources:
  claude       ~/.claude/projects/**/*.jsonl      message.usage
  codex        ~/.codex/sessions/**/rollout-*.jsonl  token_usage_record.usage
  opencode     ~/.local/share/opencode/opencode.db    session totals, message.data.tokens
  devin        ~/.local/share/devin/cli/sessions.db   message_nodes.metadata.metrics
  antigravity  ~/.gemini/antigravity*/             usage not stored -> unmeasurable
  t3           ~/.t3/userdata/state*.sqlite        no usage tables -> unmeasurable

Usage: harness-usage.py [--days N] [--per-call-sql] [--json PATH]
"""
import argparse
import glob
import json
import os
import re
import sqlite3
import sys
import tempfile
from collections import Counter, defaultdict
from datetime import datetime, timezone

HOME = os.path.expanduser("~")
DAY = 86400


def pct(vals, q):
    if not vals:
        return 0
    s = sorted(vals)
    return s[min(len(s) - 1, int(len(s) * q))]


def iso_to_epoch(ts):
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


def blank():
    return dict(calls=0, input=0, cache_read=0, cache_write=0, output=0,
                reasoning=0, cost=0.0, ctx=[], days=Counter(), models=Counter(),
                projects=Counter(), sessions=defaultdict(lambda: [0, 0, 0]),
                points=[], rate_limits=None, rl_ts=0.0, limit_hits=[])


# The owner's cost weight: input x1, cache_write x1.25, cache_read x0.1,
# output x5 (AGENTS.md W_SQL). Limit windows measure weighted tokens.
W_COST = {"input": 1.0, "cache_write": 1.25, "cache_read": 0.1,
          "output": 5.0}


def weighted(rec):
    return sum((rec.get(k) or 0) * w for k, w in W_COST.items())


def add(rec, st):
    st["calls"] += 1
    st["points"].append((rec.get("ts") or 0.0, weighted(rec)))
    for k in ("input", "cache_read", "cache_write", "output", "reasoning"):
        st[k] += rec.get(k) or 0
    st["cost"] += rec.get("cost") or 0.0
    ctx = (rec.get("input") or 0) + (rec.get("cache_read") or 0) + (rec.get("cache_write") or 0)
    if ctx:
        st["ctx"].append(ctx)
    st["days"][rec["day"]] += (rec.get("output") or 0) + ctx
    st["models"][rec.get("model") or "?"] += (rec.get("output") or 0) + ctx
    st["projects"][rec.get("project") or "?"] += (rec.get("output") or 0) + ctx
    s = st["sessions"][rec.get("session") or "?"]
    s[0] += (rec.get("output") or 0) + ctx
    s[1] += rec.get("cache_read") or 0
    s[2] += 1


def scan_claude(cutoff, st):
    for f in glob.iglob(os.path.join(HOME, ".claude/projects/*/*.jsonl")):
        if os.path.getmtime(f) < cutoff - DAY:
            continue
        seen = set()
        try:
            fh = open(f, errors="replace")
        except OSError:
            continue
        for line in fh:
            if '"usage"' not in line:
                continue
            try:
                d = json.loads(line)
            except ValueError:
                continue
            msg = d.get("message") or {}
            key = (msg.get("id"), d.get("requestId"))
            if key == (None, None):
                key = (d.get("uuid"), None)
            if key in seen:
                continue
            u = msg.get("usage") or d.get("usage")
            if not u or not isinstance(u, dict):
                continue
            ts = iso_to_epoch(d.get("timestamp", ""))
            if ts < cutoff:
                continue
            seen.add(key)
            add(dict(
                day=datetime.fromtimestamp(ts, timezone.utc).date().isoformat(),
                ts=ts,
                session=d.get("sessionId") or os.path.basename(f)[:36],
                project=d.get("cwd") or "?",
                model=msg.get("model") or "?",
                input=u.get("input_tokens"),
                cache_read=u.get("cache_read_input_tokens"),
                cache_write=u.get("cache_creation_input_tokens"),
                output=u.get("output_tokens"),
                reasoning=None), st)


def scan_codex(cutoff, st):
    pat = os.path.join(HOME, ".codex/sessions/*/*/*/rollout-*.jsonl")
    for f in sorted(glob.iglob(pat)):
        if os.path.getmtime(f) < cutoff - DAY:
            continue
        cwd, model, sid = "?", "?", os.path.basename(f)[8:44]
        seen = set()
        for line in open(f, errors="replace"):
            if "session_meta" in line:
                try:
                    p = json.loads(line).get("payload", {})
                    cwd = p.get("cwd") or cwd
                    sid = p.get("session_id") or sid
                except ValueError:
                    pass
            if model == "?" and '"model":"' in line:
                i = line.find('"model":"') + 9
                model = line[i:line.find('"', i)] or "?"
            if "token_usage_record" not in line and not (
                    "event_msg" in line and "token_count" in line
                    and "rate_limits" in line):
                continue
            try:
                d = json.loads(line)
            except ValueError:
                continue
            p = d.get("payload", {})
            ts0 = iso_to_epoch(d.get("timestamp", "") or p.get("timestamp", ""))
            rl = p.get("rate_limits")
            if isinstance(rl, dict) and rl.get("limit_id") == "codex":
                if ts0 >= st.get("rl_ts", 0.0):
                    st["rate_limits"], st["rl_ts"] = rl, ts0
                if rl.get("rate_limit_reached_type") or \
                        rl.get("spend_control_reached"):
                    st["limit_hits"].append(ts0)
            u = p.get("usage") or {}
            rid = p.get("response_id") or d.get("id")
            if rid in seen or not u.get("total_tokens"):
                continue
            ts = iso_to_epoch(d.get("timestamp", "") or p.get("timestamp", ""))
            if ts < cutoff:
                continue
            seen.add(rid)
            add(dict(
                day=datetime.fromtimestamp(ts, timezone.utc).date().isoformat(),
                ts=ts,
                session=sid, project=cwd, model=model,
                input=(u.get("input_tokens") or 0) - (u.get("cached_input_tokens") or 0)
                      - (u.get("cache_write_input_tokens") or 0),
                cache_read=u.get("cached_input_tokens"),
                cache_write=u.get("cache_write_input_tokens"),
                output=u.get("output_tokens"),
                reasoning=u.get("reasoning_output_tokens")), st)


def db_ro(path):
    return sqlite3.connect("file:%s?mode=ro" % path, uri=True)


def scan_opencode(cutoff, st, per_call):
    db = os.path.join(HOME, ".local/share/opencode/opencode.db")
    if not os.path.exists(db):
        return "no opencode.db"
    try:
        con = db_ro(db)
        cut_ms = int(cutoff * 1000)
        rows = con.execute(
            "SELECT id, directory, model, agent, cost, tokens_input, tokens_output,"
            " tokens_reasoning, tokens_cache_read, tokens_cache_write, time_updated"
            " FROM session WHERE time_updated >= ?", (cut_ms,)).fetchall()
        for sid, d, model, agent, cost, ti, to, tr, tcr, tcw, tupd in rows:
            add(dict(day=datetime.fromtimestamp(tupd / 1000, timezone.utc).date().isoformat(),
                     ts=tupd / 1000,
                     session=sid, project=d or "?", model=model or agent or "?",
                     input=ti, cache_read=tcr, cache_write=tcw, output=to,
                     reasoning=tr, cost=cost, session_level=True), st)
        if per_call:
            st["ctx"] = []
            q = ("SELECT json_extract(data,'$.tokens.input'),"
                 " json_extract(data,'$.tokens.cache.read'),"
                 " json_extract(data,'$.tokens.cache.write'),"
                 " json_extract(data,'$.tokens.output')"
                 " FROM message WHERE time_created >= ?"
                 " AND data LIKE '%\"tokens\":%'" )
            n = 0
            for ti, tcr, tcw, to in con.execute(q, (cut_ms,)):
                if ti is None:
                    continue
                st["ctx"].append((ti or 0) + (tcr or 0) + (tcw or 0))
                st["calls"] += 1
                n += 1
            st["calls"] = n or st["calls"]
        con.close()
    except sqlite3.Error as e:
        return "sqlite error: %s" % e
    return None


def scan_devin(cutoff, st):
    db = os.path.join(HOME, ".local/share/devin/cli/sessions.db")
    if not os.path.exists(db):
        return "no sessions.db"
    try:
        con = db_ro(db)
        sess = {r[0]: (r[1], r[2]) for r in con.execute(
            "SELECT id, working_directory, model FROM sessions")}
        q = ("SELECT session_id, created_at,"
             " json_extract(chat_message,'$.metadata.metrics.input_tokens'),"
             " json_extract(chat_message,'$.metadata.metrics.output_tokens'),"
             " json_extract(chat_message,'$.metadata.metrics.cache_read_tokens'),"
             " json_extract(chat_message,'$.metadata.metrics.cache_creation_tokens'),"
             " json_extract(chat_message,'$.metadata.generation_model'),"
             " json_extract(chat_message,'$.metadata.metrics.total_time_ms'),"
             " json_extract(chat_message,'$.metadata.metrics.ttft_ms'),"
             " json_extract(chat_message,'$.metadata.request_id')"
             " FROM message_nodes"
             " WHERE chat_message LIKE '%\"input_tokens\"%'")
        lat, ttfts = [], []
        seen = set()
        for sid, ts, ti, to, tcr, tcw, gm, tms, ttft, rid in con.execute(q):
            if ts and ts > 1e14:
                ts = ts / 1e9
            elif ts and ts > 1e11:
                ts = ts / 1000.0
            if not ts or ts < cutoff:
                continue
            if rid and rid in seen:
                continue
            if rid:
                seen.add(rid)
            if tms:
                lat.append(tms)
            if ttft:
                ttfts.append(ttft)
            wd, smodel = sess.get(sid, ("?", "?"))
            add(dict(day=datetime.fromtimestamp(ts, timezone.utc).date().isoformat(),
                     ts=ts,
                     session=sid, project=wd, model=gm or smodel,
                     input=ti, cache_read=tcr, cache_write=tcw, output=to,
                     reasoning=None), st)
        st["latency_ms_median"] = pct(lat, 0.5)
        st["latency_ms_p90"] = pct(lat, 0.9)
        st["ttft_ms_median"] = pct(ttfts, 0.5)
        con.close()
    except sqlite3.Error as e:
        return "sqlite error: %s" % e
    return None


# --- subscription limit meter (limits.json) ------------------------------
#
# Per provider and window: {used, capacity, share, resets_at, source}.
# Codex reports its windows itself (rate_limits in token_count rollout
# events, verified 2026-11-17: primary/secondary carry used_percent,
# window_minutes and resets_at; only the weekly window is emitted on the
# current plan). The other metered providers report nothing: their
# capacity is calibrated from our own records — the metered use in the
# trailing window at the last observed usage-limit failure (the same
# T3 failure record keeper.py's quota wait reads). A calibration is a
# lower bound and is marked so; missing data is "unknown", never 0.

LIMITS_VERSION = 1
WINDOWS = (("5h", 5 * 3600), ("daily", 24 * 3600), ("weekly", 7 * DAY))
# The windows a provider actually has (G4): Devin's quota is daily +
# weekly — the 5h window was a guess that priced it ~10x too small.
PROVIDER_WINDOWS = {"devin": ("daily", "weekly")}
DEFAULT_WINDOWS = ("5h", "weekly")
# codex window_minutes -> our window name; anything else stays unmapped
CODEX_MINUTES = {300: "5h", 10080: "weekly"}
METERED = ("codex", "devin", "claude")
DEFAULT_LIMITS = os.path.join(HOME, ".hermes/h2/limits.json")
DEFAULT_T3 = os.path.join(HOME, ".t3/userdata/statev2.sqlite")

QUOTA_CODES = {"-32011", "usageLimitExceeded", "provider.chatgpt-limit",
               "provider.free-limit", "api_error_429"}
QUOTA_RE = re.compile(r"usage.?limit|quota|rate.?limit|too many requests|"
                      r"\b429\b", re.I)


def quota_error(failure) -> bool:
    """keeper.py's predicate, kept identical: the same T3 failure record
    the quota wait already honours."""
    return (failure.get("class") == "usage_limit"
            or str(failure.get("code")) in QUOTA_CODES
            or bool(QUOTA_RE.search(str(failure.get("message") or ""))))


def failure_provider(inst) -> str:
    p = str(inst or "").lower()
    if "codex" in p:
        return "codex"
    if "devin" in p:
        return "devin"
    if "claude" in p or "anthropic" in p:
        return "claude"
    return ""


def iso_cut(ts) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat() \
        .replace("+00:00", "Z")


def quota_failures(t3_db, since) -> list:
    """[(ts, provider)] of usage-limit failures T3 recorded, bounded to
    `since`. An unreadable projection is no signal, never zero."""
    out = []
    try:
        con = db_ro(t3_db)
    except sqlite3.Error:
        return out
    try:
        q = ("select ti.updated_at, r.provider_instance_id,"
             " json_extract(ti.payload_json,'$.failure')"
             " from orchestration_v2_projection_turn_items ti"
             " join orchestration_v2_projection_runs r"
             "   on r.run_id = ti.run_id"
             " where ti.type='error' and ti.updated_at >= ?")
        for upd, inst, raw in con.execute(q, (iso_cut(since),)):
            try:
                f = json.loads(raw or "{}") or {}
            except ValueError:
                continue
            ts = iso_to_epoch(upd)
            p = failure_provider(inst)
            if p and ts and quota_error(f):
                out.append((ts, p))
    except sqlite3.Error:
        return []
    finally:
        con.close()
    return out


def window_in(points, end, span) -> float:
    """Weighted tokens of the metered calls inside (end-span, end]."""
    return sum(w for t, w in points if end - span < t <= end)


def calibrate(points, failures, prev_window, span, meter_start):
    """(token capacity, calibrated_at) for one window. A later observed
    failure recalibrates: capacity = metered use in the trailing window
    ending at it — but only when the meter's horizon reaches that far
    back; a partially covered window would understate capacity, so the
    persisted value wins. An unobservable failure or one with nothing
    metered keeps the persisted value; none at all is (None, None)."""
    cal_at = (prev_window or {}).get("calibrated_at") or 0
    cap = (prev_window or {}).get("token_capacity")
    if cap is None and (prev_window or {}).get("unit") == "weighted_tokens":
        cap = prev_window.get("capacity")
    last = max(failures or [0])
    if last > cal_at and meter_start is not None \
            and meter_start <= last - span:
        use = window_in(points, last, span)
        if use > 0:
            return round(use, 1), last
    return cap, (cal_at or None)


def _window(used, capacity, resets_at, source, unit, **extra):
    share = used / capacity \
        if isinstance(used, (int, float)) and capacity else None
    out = {"used": used, "capacity": capacity, "share": share,
           "resets_at": resets_at, "source": source, "unit": unit}
    out.update(extra)
    return out


def codex_window(rl, wname, now):
    """The provider's rate_limits entry for our window, matched by its
    reported window_minutes (never by field name). An expired report —
    resets_at already past — is stale, not data."""
    for key in ("primary", "secondary"):
        w = rl.get(key)
        if not isinstance(w, dict):
            continue
        if CODEX_MINUTES.get(w.get("window_minutes")) != wname:
            continue
        res = w.get("resets_at")
        if isinstance(res, (int, float)) and res < now:
            continue
        return w
    return None


def limits_doc(stats, failures, now, prev=None, unreadable=(),
               meter_start=None) -> dict:
    """The limits.json document. `used`/`capacity` are in `unit`
    ("percent" on a provider-reported window, "weighted_tokens" on a
    metered one); token_used/token_capacity always carry the metered
    numbers so a goal's share of a window can be priced in tokens.
    `unreadable` names providers whose scan failed — their use is
    unknown, not zero. `meter_start` bounds how far back the meter
    reaches; a failure whose trailing window starts earlier cannot
    calibrate."""
    prev = prev or {}
    by_fail = {}
    for ts, p in failures:
        by_fail.setdefault(p, []).append(ts)
    providers = {}
    for name in METERED:
        st = stats.get(name) or {}
        pts = st.get("points") or []
        rl = st.get("rate_limits") or {}
        scanned = name in stats and name not in unreadable
        prev_w = ((prev.get("providers") or {}).get(name)
                  or {}).get("windows") or {}
        wins = {}
        for wname, span in WINDOWS:
            if wname not in PROVIDER_WINDOWS.get(name, DEFAULT_WINDOWS):
                continue
            pw = prev_w.get(wname) or {}
            cap, cal = calibrate(pts, by_fail.get(name), pw, span,
                                 meter_start)
            prov = codex_window(rl, wname, now) if name == "codex" else None
            if isinstance(prov, dict) and \
                    isinstance(prov.get("used_percent"), (int, float)):
                entry = _window(used=round(float(prov["used_percent"]), 2),
                                capacity=100.0,
                                resets_at=prov.get("resets_at"),
                                source="provider", unit="percent")
            else:
                # a metered window (or a codex window with no live
                # provider report): measured use only when the provider
                # was actually read; the share is known once a failure
                # has calibrated the capacity
                used = round(window_in(pts, now, span), 1) if scanned \
                    else None
                entry = _window(used=used, capacity=cap, resets_at=None,
                                source="calibrated" if cap else "unknown",
                                unit="weighted_tokens")
            entry["token_used"] = round(window_in(pts, now, span), 1) \
                if scanned else None
            if entry["unit"] == "percent" and entry["share"] \
                    and entry["token_used"]:
                # G6: the provider's own % is ground truth — capacity is
                # token_used/share. The calibrated bound came from goal
                # pricing and understated Codex ~10x (181.7M vs ~1.89B).
                entry["token_capacity"] = round(
                    entry["token_used"] / entry["share"], 1)
            else:
                entry["token_capacity"] = cap
            if isinstance(entry.get("share"), (int, float)) \
                    and entry["share"] > 1:
                # measured use already passed the bound: the calibration
                # is disproven — flag it for audit, never silently trust
                entry["stale"] = True
            if cal:
                entry["calibrated_at"] = cal
            wins[wname] = entry
        providers[name] = {"windows": wins}
    return {"version": LIMITS_VERSION, "computed_at": int(now),
            "providers": providers}


def write_limits(path, doc) -> None:
    d = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(dir=d or ".", suffix=".tmp")
    with os.fdopen(fd, "w") as fh:
        json.dump(doc, fh, indent=1, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, path)


def load_limits(path) -> dict:
    try:
        with open(path) as fh:
            d = json.load(fh)
            return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def selftest():
    """Prove usage rows dedup to API calls, not log lines.

    Regression: claude jsonl writes one line per content block of the same
    response, devin writes ~2.2 message_nodes per request_id. Counting lines
    overcounted both ~2.2x. Guard: synthetic duplicated rows must collapse.
    """
    import shutil
    import tempfile
    tmp = tempfile.mkdtemp()
    try:
        cdir = os.path.join(tmp, ".claude/projects/p")
        os.makedirs(cdir)
        line = lambda u, r, m, t: json.dumps(dict(
            uuid=u, type="assistant", requestId=r, timestamp=t,
            message=dict(id=m, model="x", usage=dict(
                input_tokens=1, cache_read_input_tokens=10, output_tokens=2))))
        with open(os.path.join(cdir, "s.jsonl"), "w") as fh:
            fh.write(line("u1", "r1", "m1", "2026-11-07T00:00:01Z") + "\n")
            fh.write(line("u2", "r1", "m1", "2026-11-07T00:00:01Z") + "\n")
            fh.write(line("u3", "r2", "m2", "2026-11-07T00:00:02Z") + "\n")
        ddir = os.path.join(tmp, ".local/share/devin/cli")
        os.makedirs(ddir)
        con = sqlite3.connect(os.path.join(ddir, "sessions.db"))
        con.execute("CREATE TABLE sessions(id TEXT, working_directory TEXT,"
                    " model TEXT)")
        con.execute("CREATE TABLE message_nodes(row_id INTEGER, session_id"
                    " TEXT, node_id TEXT, parent_node_id TEXT, chat_message"
                    " TEXT, created_at REAL, metadata TEXT)")
        import time
        now = time.time()
        msg = lambda rid: json.dumps(dict(metadata=dict(
            request_id=rid, metrics=dict(input_tokens=5, output_tokens=1,
                                         cache_read_tokens=50))))
        for i, rid in enumerate(("r1", "r1", "r2"), 1):
            con.execute("INSERT INTO message_nodes VALUES(?,?,?,?,?,?,?)",
                        (i, "s", "n%d" % i, None, msg(rid), now, ""))
        con.commit()
        con.close()
        global HOME
        old_home, HOME = HOME, tmp
        st_c, st_d = blank(), blank()
        scan_claude(0, st_c)
        scan_devin(0, st_d)
        HOME = old_home
        assert st_c["calls"] == 2, "claude dedup broke: %d calls" % st_c["calls"]
        assert st_d["calls"] == 2, "devin dedup broke: %d calls" % st_d["calls"]
        # the window meter counts API calls, not stored rows: 2 points, not 3
        assert len(st_c["points"]) == 2 and len(st_d["points"]) == 2
        print("selftest ok: 3 stored rows -> 2 API calls (claude, devin)")

        # --- limits.json: parse, calibration, unknown -------------------
        import time as _time
        now = _time.time()
        # a codex rollout line carrying provider rate_limits
        rl_line = json.dumps({
            "timestamp": "2026-11-17T10:00:00.000Z", "ordinal": 9,
            "type": "event_msg",
            "payload": {"type": "token_count",
                        "info": {"total_token_usage": {"total_tokens": 100},
                                 "last_token_usage": {"total_tokens": 10}},
                        "rate_limits": {
                            "limit_id": "codex", "plan_type": "prolite",
                            "primary": {"used_percent": 42.0,
                                        "window_minutes": 10080,
                                        "resets_at": now + 1000},
                            "secondary": None}}})
        codir = os.path.join(tmp, ".codex/sessions/2026/11/17")
        os.makedirs(codir)
        with open(os.path.join(codir, "rollout-x.jsonl"), "w") as fh:
            fh.write(rl_line + "\n")
        st_x = blank()
        old_home, HOME = HOME, tmp
        scan_codex(now - 60, st_x)
        HOME = old_home
        rl = st_x.get("rate_limits") or {}
        assert (rl.get("primary") or {}).get("used_percent") == 42.0, \
            "codex rate_limits not parsed: %r" % rl
        doc = limits_doc({"codex": st_x}, [], now, {})
        w = doc["providers"]["codex"]["windows"]["weekly"]
        assert w["source"] == "provider" and w["share"] == 0.42, w
        assert w["resets_at"] == now + 1000
        assert doc["providers"]["codex"]["windows"]["5h"]["source"] == "unknown"
        # windows map by window_minutes, not by the field name
        rl2 = {"secondary": {"used_percent": 70.0,
                             "window_minutes": 10080,
                             "resets_at": now + 500},
               "primary": {"used_percent": 20.0, "window_minutes": 300,
                           "resets_at": now + 100}}
        st_y = blank(); st_y["rate_limits"] = rl2
        doc = limits_doc({"codex": st_y}, [], now, {})
        wins = doc["providers"]["codex"]["windows"]
        assert wins["weekly"]["share"] == 0.70, wins["weekly"]
        assert wins["5h"]["share"] == 0.20, wins["5h"]
        # an expired report is stale, not data
        rl3 = {"primary": {"used_percent": 9.0, "window_minutes": 10080,
                           "resets_at": now - 1}}
        st_z = blank(); st_z["rate_limits"] = rl3
        doc = limits_doc({"codex": st_z}, [], now, {})
        assert doc["providers"]["codex"]["windows"]["weekly"]["source"] \
            == "unknown"
        # calibration: a usage-limit failure at T makes the metered use in
        # the trailing window the capacity (never below the observed use)
        t_fail = now - 3600
        for ts, wtd in ((t_fail - 2 * 3600, 300.0), (t_fail - 6 * 3600, 50.0)):
            st_x["points"].append((ts, wtd))
        doc = limits_doc({"codex": st_x}, [(t_fail, "codex")], now, {},
                         meter_start=t_fail - 7 * 86400)
        w = doc["providers"]["codex"]["windows"]["5h"]
        assert w["source"] == "calibrated" and w["capacity"] == 300.0, w
        w7 = doc["providers"]["codex"]["windows"]["weekly"]
        # G6: provider-reported % is ground truth — token_capacity is
        # token_used/share (350/0.42), not the calibrated bound
        assert abs(w7["token_capacity"] - 350.0 / 0.42) < 0.1, w7
        # a failure whose trailing window starts before the meter's
        # horizon cannot calibrate: the observed use would be partial
        st_d = blank(); st_d["points"] = [(now - 60, 10.0)]
        doc = limits_doc({"devin": st_d}, [(now - 6 * 86400, "devin")],
                         now, {}, meter_start=now - 2 * 86400)
        w = doc["providers"]["devin"]["windows"]["weekly"]
        assert w["source"] == "unknown" and w["token_capacity"] is None, w
        # a persisted calibration survives a run that sees no new failure
        prev = {"providers": {"devin": {"windows": {"weekly": {
            "capacity": 900.0, "calibrated_at": now - 90000,
            "unit": "weighted_tokens"}}}}}
        st_dv = blank(); st_dv["points"] = [(now - 60, 30.0)]
        doc = limits_doc({"devin": st_dv}, [], now, prev)
        w = doc["providers"]["devin"]["windows"]["weekly"]
        assert w["source"] == "calibrated" and w["capacity"] == 900.0, w
        assert abs(w["share"] - 30.0 / 900.0) < 1e-9, w
        # a provider whose scan failed is unknown, never a calibrated 0
        doc = limits_doc({"devin": st_dv}, [], now, prev,
                         unreadable={"devin"})
        w = doc["providers"]["devin"]["windows"]["weekly"]
        assert w["used"] is None and w["share"] is None, w
        # missing data is unknown, never 0
        doc = limits_doc({}, [], now, {})
        w = doc["providers"]["devin"]["windows"]["weekly"]
        assert w["share"] is None and w["used"] is None, w
        assert w["source"] == "unknown"
        # G4: Devin's real windows are daily + weekly — no 5h window
        dw = doc["providers"]["devin"]["windows"]
        assert set(dw) == {"daily", "weekly"}, dw
        # G4: measured use past the bound flags the calibration as stale
        st_o = blank(); st_o["points"] = [(now - 60, 5000.0)]
        prev_o = {"providers": {"devin": {"windows": {"daily": {
            "capacity": 1000.0, "calibrated_at": now - 90000,
            "unit": "weighted_tokens"}}}}}
        doc = limits_doc({"devin": st_o}, [], now, prev_o)
        w = doc["providers"]["devin"]["windows"]["daily"]
        assert w["share"] == 5.0 and w["stale"] is True, w
        # ...and codex's windows still map to 5h + weekly only
        assert set(doc["providers"]["codex"]["windows"]) == \
            {"5h", "weekly"}
        print("selftest ok: limits parse + calibration + unknown")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=8)
    ap.add_argument("--per-call-sql", action="store_true",
                    help="scan opencode message blobs for per-call context stats")
    ap.add_argument("--json", dest="json_out")
    ap.add_argument("--limits", nargs="?", const=True, default=None,
                    metavar="PATH", help="also write limits.json "
                    "($H2_LIMITS or ~/.hermes/h2/limits.json when no PATH)")
    ap.add_argument("--selftest", action="store_true",
                    help="prove per-API-call dedup on synthetic rows, then exit")
    args = ap.parse_args()
    if args.selftest:
        selftest()
        return 0
    now_ts = datetime.now(timezone.utc).timestamp()
    cutoff = (now_ts - args.days * DAY)

    stats = {h: blank() for h in ("claude", "codex", "opencode", "devin")}
    errs = {}
    scan_claude(cutoff, stats["claude"])
    scan_codex(cutoff, stats["codex"])
    e = scan_opencode(cutoff, stats["opencode"], args.per_call_sql)
    if e:
        errs["opencode"] = e
    e = scan_devin(cutoff, stats["devin"])
    if e:
        errs["devin"] = e
    unmeasurable = {
        "antigravity": "usage is not written to local stores: conversations are"
                       " protobuf blobs, summaries and logs carry no token fields",
        "t3": "no usage/token/cost tables in ~/.t3/userdata/state*.sqlite;"
              " verified via .schema of both databases",
    }
    unmeasurable.update(errs)

    if args.limits:
        lim_path = args.limits if isinstance(args.limits, str) else \
            os.environ.get("H2_LIMITS") or DEFAULT_LIMITS
        t3_db = os.environ.get("H2_T3_DB") or DEFAULT_T3
        fails = quota_failures(t3_db, cutoff - 90 * DAY)
        fails += [(ts, "codex") for st in (stats.get("codex"),)
                  if st for ts in st.get("limit_hits") or []]
        lim = limits_doc(stats, fails, now_ts, load_limits(lim_path),
                         unreadable={k for k in errs if k in METERED},
                         meter_start=cutoff)
        write_limits(lim_path, lim)
        for name in METERED:
            for wname, w in lim["providers"][name]["windows"].items():
                if w["source"] == "unknown" and w["used"] is None:
                    continue
                print("== limits %s %s ==  used=%s capacity=%s share=%s "
                      "resets_at=%s (%s)" % (
                          name, wname, w["used"], w["capacity"],
                          ("%.3f" % w["share"]) if w["share"] is not None
                          else "unknown",
                          w["resets_at"] or "-", w["source"]))

    if args.json_out:
        rows = {}
        for h, st in stats.items():
            rows[h] = {k: (dict(v) if isinstance(v, (Counter, defaultdict)) else v)
                       for k, v in st.items()
                       if k not in ("ctx", "points", "rate_limits", "rl_ts",
                                    "limit_hits")}
            rows[h]["ctx_median"] = pct(st["ctx"], 0.5)
            rows[h]["ctx_p90"] = pct(st["ctx"], 0.9)
            rows[h]["ctx_max"] = max(st["ctx"], default=0)
            for k in ("latency_ms_median", "latency_ms_p90", "ttft_ms_median"):
                if st.get(k):
                    rows[h][k] = st[k]
            rows[h]["sessions_top10"] = sorted(
                ((k, *v) for k, v in st["sessions"].items()),
                key=lambda x: -x[1])[:10]
        rows["_unmeasurable"] = unmeasurable
        with open(args.json_out, "w") as fh:
            json.dump(rows, fh, indent=1, default=str)

    for h, st in stats.items():
        tot_in = st["input"] + st["cache_read"] + st["cache_write"]
        share = 100.0 * st["cache_read"] / tot_in if tot_in else 0
        print("== %s ==  calls=%d  input=%s  cache_read=%s (%.0f%%)  cache_write=%s"
              "  output=%s  reasoning=%s  cost=%s" % (
                  h, st["calls"], f"{st['input']:,}", f"{st['cache_read']:,}",
                  share, f"{st['cache_write']:,}", f"{st['output']:,}",
                  f"{st['reasoning']:,}",
                  ("$%.2f" % st["cost"]) if st["cost"] else "n/a"))
        if st.get("latency_ms_median"):
            print("  latency: median=%.0fms p90=%.0fms ttft_median=%.0fms" % (
                st["latency_ms_median"], st["latency_ms_p90"], st["ttft_ms_median"]))
        if st["ctx"]:
            print("  ctx/call: median=%s p90=%s max=%s  calls>200k=%d%%" % (
                f"{pct(st['ctx'], 0.5):,}", f"{pct(st['ctx'], 0.9):,}",
                f"{max(st['ctx']):,}",
                100 * sum(1 for c in st["ctx"] if c > 200000) // len(st["ctx"])))
        for name, key in (("day", "days"), ("model", "models"), ("project", "projects")):
            top = st[key].most_common(5)
            print("  top %s: %s" % (name, ", ".join(
                "%s=%s" % (k, f"{v:,}") for k, v in top)))
        top_sess = sorted(st["sessions"].items(), key=lambda x: -x[1][0])[:10]
        for sid, (tot, cr, n) in top_sess:
            print("    session %s  total=%s cache_read=%s calls=%d"
                  % (sid[:36], f"{tot:,}", f"{cr:,}", n))
    for h, why in unmeasurable.items():
        print("== %s ==  NOT MEASURABLE: %s" % (h, why))
    return 0


if __name__ == "__main__":
    sys.exit(main())
