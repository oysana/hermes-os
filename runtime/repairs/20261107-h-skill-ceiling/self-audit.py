#!/usr/bin/env python3
"""Weekly self-audit: where the token budget leaks and what to cut.

Modelled on three review bots (dr eggbot, trimmy, efficiency optimizer): read
our own memory, skills, cron and ledger, then produce a cut list with
before/after sizes and an estimated saving instead of a general complaint.

Nothing is deleted here. It writes a report; cutting is a separate, reviewed step.

Usage:
  self-audit.py            # print report
  self-audit.py --write    # also save to ~/.hermes/work/self-audit/<date>.md
"""
import argparse
import datetime
import json
import re
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

HOME = Path.home() / ".hermes"
ZONE = ZoneInfo("Asia/Almaty")
PROFILES = [("H", HOME), ("Y", HOME / "profiles" / "yqecea"), ("S", HOME / "profiles" / "sqecea")]
CHARS_PER_TOKEN = 4  # rough, Russian text; every number below is an estimate


def tokens(chars: int) -> int:
    return round(chars / CHARS_PER_TOKEN)


def memory_audit(home: Path):
    path = home / "memories" / "MEMORY.md"
    if not path.is_file():
        return None
    text = path.read_text(errors="replace")
    entries = [e.strip() for e in text.split("§") if e.strip()]
    seen, duplicates = {}, []
    for entry in entries:
        key = re.sub(r"\W+", "", entry.lower())[:80]
        if key in seen:
            duplicates.append((seen[key][:70], entry[:70]))
        else:
            seen[key] = entry
    oversized = [e for e in entries if len(e) > 500]
    return {
        "chars": len(text),
        "tokens": tokens(len(text)),
        "entries": len(entries),
        "duplicates": duplicates,
        "oversized": oversized,
        "duplicate_chars": sum(len(d[1]) for d in duplicates),
        "oversized_chars": sum(len(e) for e in oversized),
    }


def skills_audit(home: Path):
    dirs = [HOME / "skills", HOME / "plugins"]
    if home != HOME:
        dirs.append(home / "skills")
    found = {}
    for base in dirs:
        if not base.is_dir():
            continue
        for skill in base.rglob("SKILL.md"):
            try:
                body = skill.read_text(errors="replace")
            except OSError:
                continue
            found[str(skill)] = len(body)
    big = sorted(found.items(), key=lambda kv: kv[1], reverse=True)[:5]
    return {"count": len(found), "chars": sum(found.values()), "largest": big}


def runs_per_day(expr: str) -> float:
    """Runs per day from a 5-field cron expression. Unknown shape counts as 1."""
    fields = (expr or "").split()
    if len(fields) != 5:
        return 1.0
    minute, hour, dom, month, dow = fields

    def count(field: str, span: int) -> int:
        if field == "*":
            return span
        if field.startswith("*/"):
            try:
                step = int(field[2:])
                return max(1, span // step)
            except ValueError:
                return 1
        return len([p for p in field.split(",") if p])

    per_day = float(count(minute, 60) * count(hour, 24))
    if dow != "*":
        per_day *= count(dow, 7) / 7
    if dom != "*":
        per_day *= count(dom, 31) / 31
    return max(per_day, 0.01)


def cron_audit(home: Path):
    jobs_file = home / "cron" / "jobs.json"
    if not jobs_file.is_file():
        return {"total": 0, "agent": 0, "script": 0, "heavy": [], "estimated_daily_agent_tokens": 0}
    try:
        data = json.loads(jobs_file.read_text())
    except (json.JSONDecodeError, OSError):
        return {"total": 0, "agent": 0, "script": 0, "heavy": [], "estimated_daily_agent_tokens": 0}
    if isinstance(data, dict):
        data = data.get("jobs", data)
    if isinstance(data, dict):
        data = list(data.values())
    enabled = [j for j in data if isinstance(j, dict) and j.get("enabled", True)]
    agent = [j for j in enabled if not j.get("no_agent")]
    script = [j for j in enabled if j.get("no_agent")]
    per_run = 180_000  # floor: an agent run re-reads its whole context
    daily = 0.0
    heavy = []
    for job in agent:
        schedule = job.get("schedule") or {}
        expr = schedule.get("expr") if isinstance(schedule, dict) else str(schedule)
        runs = runs_per_day(expr or "")
        daily += per_run * runs
        heavy.append(((job.get("name") or "")[:40], expr, round(runs, 2)))
    heavy.sort(key=lambda row: row[2], reverse=True)
    return {
        "total": len(enabled),
        "agent": len(agent),
        "script": len(script),
        "heavy": heavy[:5],
        "estimated_daily_agent_tokens": int(daily),
    }


def skill_review_audit(now=None, ledger_path=None, log_dir=None):
    """Curator-tagged skill edits in the ledger over the last 7 days vs. how many
    'Background review complete ... result=none' runs agent.log* recorded. A gap
    means the counter does not see real edits."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    if now.tzinfo is None:
        now = now.astimezone()  # naive input means local time
    since = now - datetime.timedelta(days=7)
    since_utc = since.astimezone(datetime.timezone.utc)
    since_local_naive = since.astimezone().replace(tzinfo=None)
    edits = 0
    lp = ledger_path or HOME / "skills" / ".curator_ledger.jsonl"
    try:
        for line in lp.read_text(errors="replace").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if not isinstance(row, dict) or row.get("actor") != "curator":
                continue
            try:
                ts = datetime.datetime.fromisoformat(str(row.get("ts") or ""))
            except ValueError:
                continue
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=datetime.timezone.utc)
            if ts >= since_utc:
                edits += 1
    except OSError:
        pass
    none_runs = 0
    for log in sorted((log_dir or HOME / "logs").glob("agent.log*")):
        try:
            lines = log.read_text(errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            if "result=none" not in line or "Background review" not in line:
                continue
            try:
                ts = datetime.datetime.strptime(line[:23], "%Y-%m-%d %H:%M:%S,%f")
            except ValueError:
                continue
            if ts >= since_local_naive:
                none_runs += 1
    return {"edits_7d": edits, "result_none_runs": none_runs}


def ledger_audit():
    ledger = HOME / "scripts" / "ledger"
    if not ledger.is_file():
        return {"open": 0, "total": 0}
    import subprocess
    try:
        out = subprocess.run([str(ledger), "list"], capture_output=True, text=True, timeout=60).stdout
        rows = json.loads(out)
    except Exception:
        return {"open": 0, "total": 0}
    open_rows = [r for r in rows if r.get("status") in ("open", "waiting")]
    junk = re.compile(r"/tmp/|h-bench|h-lean|coord-g6|Ответь одним словом|^\[IMPORTANT:", re.IGNORECASE)
    junk_rows = [r for r in rows if junk.search(r.get("title") or "")]
    return {"open": len(open_rows), "total": len(rows), "junk": len(junk_rows)}


def build() -> str:
    lines = [f"# Аудит расхода · {datetime.datetime.now(ZONE).strftime('%d.%m.%Y %H:%M')}", ""]
    total_saving = 0

    lines.append("## Память")
    for alias, home in PROFILES:
        row = memory_audit(home)
        if not row:
            continue
        lines.append(f"- **{alias}**: {row['entries']} записей, {row['chars']} знаков ≈ {row['tokens']} токенов")
        if row["duplicates"]:
            lines.append(f"  - дублей: {len(row['duplicates'])}, это ≈ {tokens(row['duplicate_chars'])} токенов каждый ход")
            for a, b in row["duplicates"][:2]:
                lines.append(f"    - «{a}…» ≈ «{b}…»")
        if row["oversized"]:
            lines.append(f"  - записей длиннее 500 знаков: {len(row['oversized'])}, ≈ {tokens(row['oversized_chars'])} токенов")
        total_saving += tokens(row["duplicate_chars"] + row["oversized_chars"])

    lines.append("")
    lines.append("## Навыки и плагины")
    skill_rows = [skills_audit(home) for _a, home in PROFILES]
    seen_paths, merged = set(), {"count": 0, "chars": 0, "largest": []}
    for row in skill_rows:
        for path, size in row["largest"]:
            if path not in seen_paths:
                merged["largest"].append((path, size))
    merged["largest"] = sorted(merged["largest"], key=lambda kv: kv[1], reverse=True)[:5]
    first = skill_rows[0]
    lines.append(f"- файлов навыков у H: {first['count']}, суммарно ≈ {tokens(first['chars'])} токенов если загрузить всё")
    for path, size in merged["largest"]:
        lines.append(f"  - самый тяжёлый: {Path(path).parent.name} — {size} знаков ≈ {tokens(size)} токенов")
    review = skill_review_audit()
    lines.append(f"- правок навыков фоновым ревью за 7 дней: {review['edits_7d']} (по журналу), "
                 f"запусков с result=none: {review['result_none_runs']}")

    lines.append("")
    lines.append("## Расписания")
    for alias, home in PROFILES:
        row = cron_audit(home)
        if not row["total"]:
            continue
        lines.append(f"- **{alias}**: {row['total']} задач, из них с моделью {row['agent']}, скриптовых {row['script']}")
        if row["estimated_daily_agent_tokens"]:
            lines.append(f"  - оценка только на модельные запуски: ≈ {row['estimated_daily_agent_tokens']:,} токенов в сутки".replace(",", " "))
        for name, expr, runs in row["heavy"][:4]:
            lines.append(f"    - {name} ({expr}) — {runs} раз в сутки")

    lines.append("")
    lines.append("## Очередь задач")
    led = ledger_audit()
    lines.append(f"- всего записей: {led['total']}, открытых: {led['open']}, похожих на тестовые: {led['junk']}")

    lines.append("")
    lines.append("## Что резать первым")
    cuts = []
    for alias, home in PROFILES:
        row = memory_audit(home)
        if row and row["duplicates"]:
            cuts.append(f"{alias}: слить {len(row['duplicates'])} повторяющихся записей памяти")
        if row and len(row["oversized"]) > 2:
            cuts.append(f"{alias}: разбить или сократить {len(row['oversized'])} длинных записей")
    if led["junk"]:
        cuts.append(f"снять {led['junk']} тестовых записей из очереди")
    for alias, home in PROFILES:
        row = cron_audit(home)
        if row["agent"] > 6:
            cuts.append(f"{alias}: {row['agent']} модельных расписаний — перевести часть на скрипты")
    if not cuts:
        cuts.append("явного мусора не найдено")
    for cut in cuts:
        lines.append(f"- {cut}")

    lines.append("")
    lines.append(f"**Оценка экономии в памяти:** ≈ {total_saving} токенов на каждый ход, если убрать дубли и длинные записи.")
    lines.append("Все числа — оценка по знакам, делённым на 4. Точные счётчики только у провайдера.")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    text = build()
    if args.write:
        target = HOME / "work" / "self-audit" / f"{datetime.datetime.now(ZONE).date()}.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
        print(target)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
