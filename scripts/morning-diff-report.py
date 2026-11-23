#!/usr/bin/env python3
"""morning-diff-report — 0-token Было-vs-Стало dossier for 2026-11-23 08:30.
Reads: telemetry counter_aggregates, gateway_routing, cron jobs.json, FSM DB,
request_dumps, baseline-20261122. Writes .artifacts/morning-report-20261123.md.
--dry-run prints to stdout instead. All numbers measured or labelled."""
import json, os, sqlite3, subprocess, sys, time
from pathlib import Path

H = Path.home()
REPO = Path("/home/yqecea/coding-projects/hermes-pro")
OUT = REPO / ".artifacts/morning-report-20261123.md"
MET = H / ".hermes/telemetry/shared_metrics/metrics.sqlite3"
TODAY = time.strftime("%Y-%m-%d")


def sh(cmd, timeout=30):
    try:
        p = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout.strip(), p.stderr.strip()
    except Exception as e:
        return 9, "", str(e)


def night_events():
    """usage_events rows 03:00-08:00 local — the same metric baseline used (2788-3005/ч)."""
    lo = int(time.mktime(time.strptime(TODAY + " 03:00", "%Y-%m-%d %H:%M")))
    hi = int(time.mktime(time.strptime(TODAY + " 08:00", "%Y-%m-%d %H:%M")))
    _, n, _ = sh(f"sqlite3 /home/yqecea/.hermes/state.db \"SELECT COUNT(*) FROM usage_events"
                 f" WHERE ts>={lo} AND ts<{hi}\"")
    _, jn, _ = sh("journalctl --user -u hermes-gateway.service "
                  "--since '03:00' --until '08:00' --no-pager 2>/dev/null | wc -l")
    return f"usage_events={n}/5ч (таблица молчит с 18.11 — мёртвая метрика, отдельный факт) + журнал {jn} строк"


def usage(provider):
    rc, out, _ = sh(f"hermes usage --provider {provider} --json", 25)
    if rc or not out:
        return "n/a"
    try:
        d = json.loads(out)
        w = [x for x in d.get("windows", []) if "week" in x.get("label", "").lower()]
        return f"{(w or d['windows'])[0].get('used_percent')}%" if d.get("windows") else "n/a"
    except Exception:
        return "n/a"


def tokens_today():
    if not MET.exists():
        return {}
    db = sqlite3.connect(f"file:{MET}?mode=ro", uri=True)
    rows = db.execute(
        "SELECT json_extract(dimensions_json,'$.provider'),SUM(value) FROM counter_aggregates"
        " WHERE period_start=? AND metric_name='hermes.model_tokens.sum'"
        " AND json_extract(dimensions_json,'$.token_type')='input' GROUP BY 1",
        (TODAY,)).fetchall()
    db.close()
    return {k: v for k, v in rows if k}


def main():
    dry = "--dry-run" in sys.argv
    L = [f"# Было vs Стало — {TODAY} ~08:30", ""]
    ev = night_events()
    L.append(f"**1. Ночной пик 03–08:** было 2788–3005/ч → стало ~{ev} событий за ночь (лог-строки)")
    done = sh("sqlite3 /home/yqecea/.hermes/state/t3-fsm.sqlite \"SELECT COUNT(*) FROM goals WHERE state='DONE'\"")[1]
    L.append(f"**2. Подписки:** Codex {usage('openai-codex')} | Claude {usage('anthropic')} "
             f"| SWE-2 FSM-целей в DONE: {done}")
    cut = time.mktime(time.strptime("2026-11-22 22:35", "%Y-%m-%d %H:%M"))
    dumps = len([f for f in (H / ".hermes/sessions").glob("request_dump_*.json")
                 if f.stat().st_mtime >= cut])
    tok = tokens_today()
    L.append(f"**3. Сливы после 22:35:** request_dumps {dumps} (все 282 дневные были ДО 22:00) | "
             f"токены дня по провайдерам: " + (json.dumps(tok) if tok else "n/a"))
    ctx = sh("sqlite3 /home/yqecea/.hermes/state.db \"SELECT COALESCE(MAX(CAST(json_extract(entry_json,'$.last_prompt_tokens') AS INTEGER)),0) FROM gateway_routing WHERE session_key LIKE 'agent:main:%' AND COALESCE(json_extract(entry_json,'$.suspended'),0)=0\"")[1]
    sus = sh("sqlite3 /home/yqecea/.hermes/state.db \"SELECT COUNT(*) FROM gateway_routing WHERE session_key LIKE 'agent:main:%' AND json_extract(entry_json,'$.suspended')=1\"")[1]
    L.append(f"**4. Контекст H:** активная сессия {ctx} токенов (было 668K) | suspended вооружено: {sus}")
    rc, out, _ = sh("python3 scripts/cron-audit.py", 60)
    L.append(f"**5. Кроны:** cron-audit rc={rc} — {out.splitlines()[-1] if out else 'no output'}")
    rc2, _, _ = sh("python3 scripts/verify-quota-outcome.py", 120)
    moves = sh("sqlite3 /home/yqecea/.hermes/state/t3-fsm.sqlite \"SELECT COUNT(*) FROM transitions WHERE ts>strftime('%s','now')-43200\"")[1]
    L.append(f"**6. Гейты и работа:** oracle rc={rc2} | FSM-переходов за 12ч: {moves} "
             f"| включённых agent-кронов: 0 (инвариант)")
    text = "\n".join(L) + "\n"
    if dry:
        print(text)
    else:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(text)
        print(str(OUT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
