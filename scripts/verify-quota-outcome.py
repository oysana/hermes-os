#!/usr/bin/env python3
"""Deterministic verifier for the perpetual quota outcome.
Exit 0 = PASS (checks 1-5 pass; check 6 may report 'collecting').
Exit 1 = FAIL. Emits one JSON object on stdout."""
import json, os, re, sqlite3, subprocess, sys, time
from pathlib import Path

HOME = Path.home()
HERMES = HOME / ".hermes"
REPO = Path("/home/yqecea/coding-projects/hermes-pro")
BASELINE = REPO / ".artifacts/baseline-20261122/summary.json"
OUT = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "checks": {}, "verdict": None}


def sh(cmd, timeout=15):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)


def rec(name, ok, detail, blocking=True):
    OUT["checks"][name] = {"pass": ok, "detail": detail, "blocking": blocking}
    return ok


def c1_integrity():
    """Gateway healthy, version >= 0.23.3, cache_ttl auto x3, 0 codex/deepseek/ocg crons."""
    d = []
    pid = sh("systemctl --user show -p MainPID --value hermes-gateway.service").stdout.strip()
    active = sh("systemctl --user is-active hermes-gateway.service").stdout.strip()
    d.append(f"pid={pid} active={active}")
    ok_pid = active == "active" and pid not in ("0", "")
    ver = sh(os.path.expanduser("~/.local/bin/hermes --version")).stdout
    m = re.search(r"v(\d+\.\d+\.\d+)", ver)
    ok_ver = m and tuple(int(x) for x in m.group(1).split(".")) >= (0, 23, 3)
    d.append(f"version={m.group(1) if m else ver.strip()[:30]}")
    bad_ttl, codex_crons = [], []
    for cfg in [HERMES / "config.yaml",
                HERMES / "profiles/sqecea/config.yaml",
                HERMES / "profiles/yqecea/config.yaml"]:
        import yaml
        c = yaml.safe_load(cfg.read_text()) or {}
        if (c.get("prompt_caching") or {}).get("cache_ttl"):
            bad_ttl.append(cfg.name)
        if cfg.name == "config.yaml" and "model_thresholds" not in str(c.get("lcm") or {}):
            d.append("no model_thresholds")
    if bad_ttl:
        d.append("cache_ttl pin:" + ",".join(bad_ttl))
    for prof in ["", "/profiles/jqecea", "/profiles/sqecea", "/profiles/yqecea"]:
        jf = HERMES / (prof.strip("/") if prof else "") / "cron/jobs.json"
        if not jf.exists():
            jf = HERMES / "cron/jobs.json" if not prof else jf
        if not jf.exists():
            continue
        for j in json.loads(jf.read_text()).get("jobs", []):
            if not j.get("enabled", True):
                continue
            model = str(j.get("model") or "")
            prov = str(j.get("provider") or "")
            if any(t in model + prov for t in ("gpt-6", "codex", "deepseek", "muse", "sol", "luna", "terra", "ocg")):
                codex_crons.append(j["id"])
            elif not model and not j.get("kind") and not j.get("no_agent") and not j.get("script"):
                codex_crons.append(j["id"] + "?(default)")
    d.append(f"codex_crons={len(codex_crons)}")
    return rec("1_integrity", ok_pid and ok_ver and not bad_ttl and not codex_crons,
               "; ".join(d) + (" bad:" + ",".join(codex_crons[:8]) if codex_crons else ""))


def c2_quota_breaker():
    db = HERMES / "state/quota-breaker.sqlite"
    if not db.exists():
        return rec("2_quota_breaker", False, "no db")
    con = sqlite3.connect(str(db))
    rows = con.execute("SELECT provider, resets_at FROM breakers").fetchall()
    con.close()
    d = "breakers=" + str(rows)
    t = sh("python3 ~/.agents/bin/quota_breaker.py --selftest", timeout=30)
    ok = t.returncode == 0 and "SELFTEST-OK" in t.stdout
    return rec("2_quota_breaker", ok, d + " | selftest " + ("ok" if ok else t.stdout[-120:] + t.stderr[-120:]))


def c3_ladder():
    sk = (HERMES / "skills/t3-work-bridge/SKILL.md").read_text()
    fb_row = re.search(r"exhausted[^\n]*claudeAgent|unavailable[^\n]*claudeAgent", sk) or \
        "take the next row that fits" in sk
    t = sh("python3 ~/.agents/bin/t3-route --selftest", timeout=30)
    ok = t.returncode == 0 and "SELFTEST-OK" in t.stdout
    probes = [
        ({"tool_name": "delegate_task", "args": {"model": "swe-2-max",
          "task": "JUDGE раунд: оценить дизайн"}}, "block"),
        ({"tool_name": "delegate_task", "args": {"model": "swe-2-max",
          "task": "impl", "gate_fails": 0}}, "block"),
        ({"tool_name": "delegate_task", "args": {"model": "swe-2-max",
          "task": "impl", "gate_fails": 2}}, "allow"),
    ]
    max_g = []
    for payload, want in probes:
        p = subprocess.run(["python3", os.path.expanduser("~/.agents/bin/t3-route")],
                           input=json.dumps(payload), capture_output=True, text=True, timeout=10)
        got = json.loads(p.stdout).get("action") if p.stdout.strip() else "err"
        max_g.append(got == want)
    return rec("3_ladder", ok and not fb_row and all(max_g),
               f"silent_fallback_present={fb_row} route_selftest={'ok' if ok else (t.stdout+t.stderr)[-160:]} max_gate={max_g}")


def c4_zero_kanban():
    t = sh("python3 ~/.agents/bin/t3_fsm.py --selftest", timeout=30)
    ok = t.returncode == 0 and "SELFTEST-OK" in t.stdout
    it = sh("python3 ~/.agents/bin/t3-tick --itest", timeout=30)
    it_ok = it.returncode == 0 and "ITEST-OK" in it.stdout
    cfg = (HERMES / "config.yaml").read_text()
    gw_off = "dispatch_in_gateway: false" in cfg or "dispatch_in_gateway: false" in cfg.replace(" ", "")
    return rec("4_zero_kanban", ok and it_ok,
               f"fsm_selftest={'ok' if ok else (t.stdout+t.stderr)[-160:]} tick_itest={'ok' if it_ok else (it.stdout+it.stderr)[-160:]} gateway_dispatch_off={gw_off}")


def c5_gemini_contracts():
    t = sh("python3 ~/.agents/bin/gemini_contracts.py --selftest", timeout=30)
    ok = t.returncode == 0 and "SELFTEST-OK" in t.stdout
    return rec("5_gemini_contracts", ok, "selftest " + ("ok" if ok else (t.stdout + t.stderr)[-160:]))


def c6_ratchet():
    if not BASELINE.exists():
        return rec("6_ratchet", True, "no baseline file", blocking=False)
    canary = REPO / ".artifacts/baseline-20261122/canary-24h.json"
    if not canary.exists():
        return rec("6_ratchet", True, "collecting: no 24h canary yet", blocking=False)
    try:
        b = json.loads(BASELINE.read_text())
        c = json.loads(canary.read_text())
        ratio = c["verified_done"] / max(c["quota_pct"] * c["median_min"], 1e-9)
        base = b.get("baseline_ratio", 0)
        return rec("6_ratchet", ratio > base and c.get("429_storms", 0) == 0,
                   f"ratio={ratio:.3f} vs baseline={base}", blocking=False)
    except Exception as e:
        return rec("6_ratchet", False, f"ratchet read fail: {e}", blocking=False)


def main():
    c1_integrity(); c2_quota_breaker(); c3_ladder()
    c4_zero_kanban(); c5_gemini_contracts(); c6_ratchet()
    blocking_fail = [k for k, v in OUT["checks"].items() if v["blocking"] and not v["pass"]]
    OUT["verdict"] = "PASS" if not blocking_fail else "FAIL"
    OUT["failed"] = blocking_fail
    print(json.dumps(OUT, ensure_ascii=False, indent=2))
    sys.exit(0 if not blocking_fail else 1)


if __name__ == "__main__":
    main()
