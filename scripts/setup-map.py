#!/usr/bin/env python3
"""Build SETUP-MAP.md from the live Hermes/T3 configuration.

Reads ~/.hermes profiles and ~/.config/t3-pstack/models.md and writes one
current map of the setup: profile roles, models, enabled cron jobs, plugins
and Telegram topics. Contains no secrets: chat and user ids are masked and
no credential files are opened.

Usage:
  scripts/setup-map.py            # print the map
  scripts/setup-map.py --write    # refresh SETUP-MAP.md at the repo root
"""
import argparse
import datetime
import json
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

ROOT = Path.home() / ".hermes"
ZONE = ZoneInfo("Asia/Almaty")
REPO = Path(__file__).resolve().parent.parent
PROFILES = [
    ("H", "default (hqecea)", ROOT, "работа и T3: координирует делегирование, проекты, приёмку"),
    ("Y", "yqecea", ROOT / "profiles" / "yqecea", "личное: люди, деньги, напоминания, быт"),
    ("S", "sqecea", ROOT / "profiles" / "sqecea", "учёба: задания, разбор материала"),
    ("J", "jqecea", ROOT / "profiles" / "jqecea", "поиск работы"),
]
T3_MODELS = Path.home() / ".config" / "t3-pstack" / "models.md"


def load_config(home: Path) -> dict:
    config = home / "config.yaml"
    if not config.is_file():
        return {}
    try:
        return yaml.safe_load(config.read_text(errors="replace")) or {}
    except yaml.YAMLError:
        return {}


def cron_jobs(home: Path):
    jobs_file = home / "cron" / "jobs.json"
    try:
        data = json.loads(jobs_file.read_text())
    except (json.JSONDecodeError, OSError):
        return []
    if isinstance(data, dict):
        data = data.get("jobs", data)
    jobs = data.values() if isinstance(data, dict) else data
    return [j for j in jobs if isinstance(j, dict)]


def schedule(job: dict) -> str:
    sched = job.get("schedule") or {}
    if isinstance(sched, dict):
        return sched.get("display") or sched.get("expr") or str(sched.get("kind", "?"))
    return str(sched)


def model_line(config: dict) -> str:
    model = config.get("model") or {}
    if not isinstance(model, dict):
        return "?"
    return f"{model.get('default', '?')} ({model.get('provider', '?')})"


def fallbacks(config: dict) -> str:
    entries = config.get("fallback_providers") or []
    names = [f"{e.get('model', '?')} ({e.get('provider', '?')})" for e in entries if isinstance(e, dict)]
    return ", ".join(names) or "нет"


def enabled_plugins(config: dict) -> list:
    plugins = config.get("plugins") or {}
    enabled = plugins.get("enabled") or []
    return [str(p) for p in enabled]


def autoload_skills(config: dict) -> list:
    skills = config.get("auto_load") or []
    return [str(s) for s in skills]


def memory_size(home: Path) -> float:
    memory = home / "memories" / "MEMORY.md"
    return memory.stat().st_size / 1024 if memory.is_file() else 0.0


def telegram_topics(config: dict):
    """Topic names and thread ids from platforms.telegram.extra.dm_topics. Chat/user ids masked."""
    extra = (((config.get("platforms") or {}).get("telegram") or {}).get("extra") or {})
    out = []
    for dm in extra.get("dm_topics") or []:
        for t in dm.get("topics") or []:
            out.append(f"{t.get('name', '?')} — топик {t.get('thread_id', '?')}")
    return out


def moa_models(config: dict) -> str:
    moa = config.get("moa") or {}
    preset = (moa.get("presets") or {}).get("default") or {}
    refs = [f"{m.get('model')} ({m.get('provider')})" for m in preset.get("reference_models") or []]
    agg = preset.get("aggregator") or {}
    if refs:
        return ", ".join(refs) + f"; агрегатор {agg.get('model', '?')} ({agg.get('provider', '?')})"
    return "нет"


def t3_roles() -> list:
    """Role lines from the pstack model sheet, comments stripped."""
    if not T3_MODELS.is_file():
        return []
    return [
        line.strip()
        for line in T3_MODELS.read_text(errors="replace").splitlines()
        if line.strip() and not line.startswith("#")
    ]


def stray_profile_dirs() -> list:
    known = {ROOT} | {home for _, _, home, _ in PROFILES}
    stray = []
    profiles_dir = ROOT / "profiles"
    if profiles_dir.is_dir():
        for d in profiles_dir.iterdir():
            if d.is_dir() and d not in known:
                stray.append(f"{d.name} (config.yaml {'пуст' if not (d / 'config.yaml').is_file() else 'есть'})")
    return stray


def build(public: bool = False) -> str:
    lines = [
        "# SETUP-MAP — что реально настроено",
        "",
        f"сгенерировано {datetime.datetime.now(ZONE).strftime('%d.%m.%Y %H:%M')} скриптом `scripts/setup-map.py`",
        "из живых конфигов `~/.hermes` и `~/.config/t3-pstack/models.md`. Не редактировать вручную.",
        "",
        "## Профили",
    ]
    configs = {alias: load_config(home) for alias, _, home, _ in PROFILES}
    for alias, name, home, role in PROFILES:
        config = configs[alias]
        jobs = cron_jobs(home)
        enabled = [j for j in jobs if j.get("enabled") is not False]
        lines.extend([
            f"### {alias} — {name}",
            f"- роль: {role}",
            f"- модель: {model_line(config)}; фолбэки: {fallbacks(config)}",
            f"- плагины ({len(enabled_plugins(config))}): {', '.join(enabled_plugins(config)) or 'нет'}",
            f"- cron: {len(enabled)} включено из {len(jobs)}",
            f"- память MEMORY.md: {memory_size(home):.1f} КБ",
            f"- автозагрузка навыков: {', '.join(autoload_skills(config)) or 'нет'}",
            "",
        ])
    h = configs["H"]
    lines.extend([
        "## Маршруты H",
        f"- moa default: {moa_models(h)}",
        "- Telegram-топики (chat_id владельца замаскирован):",
        *[f"  - {t}" for t in telegram_topics(h)],
        "",
        "## Роли T3 (pstack)",
        *[f"- {r}" for r in t3_roles()],
        "",
        "## Включённые cron-задачи",
    ])
    for alias, _, home, _ in PROFILES:
        enabled = [j for j in cron_jobs(home) if j.get("enabled") is not False]
        if public and alias != "H":
            lines.append(f"- [{alias}] {len(enabled)} включённых личных задачи (имена скрыты в публичной версии)")
            continue
        for j in enabled:
            lines.append(f"- [{alias}] {j.get('name') or j.get('id', '?')} — {schedule(j)}")
    stray = stray_profile_dirs()
    if stray:
        lines.extend(["", "## Посторонние каталоги профилей", *[f"- {s}" for s in stray]])
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--public", action="store_true",
                        help="hide personal cron job names of non-H profiles")
    args = parser.parse_args()
    text = build(public=args.public)
    if args.write:
        target = REPO / "SETUP-MAP.md"
        target.write_text(text)
        print(target)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
