# Модели и выбор места

Два независимых слоя выбора модели: профили Hermes и роли T3/pstack.
Живой снимок обоих — `SETUP-MAP.md` (`scripts/setup-map.py`).

## Hermes (config.yaml)

- H, Y, S: `gpt-6.1-terra` через `openai-codex`, фолбэк `gpt-6-luna`.
- У H включён moa-пресет `default`: reference-модели terra+luna,
  агрегатор terra.
- J: `claude-haiku-5-5` через подписку Claude
  (`claude-subscription-directsdk-experimental`).
- У чата владельца стоит `channel_overrides` → `gpt-6.2-sol` (openai-codex).
- Решения вероятностей: `gemini-decision-1` (единственный клиент
  `~/.agents/lib/decisions.py`).
- Никогда не менять модель/провайдера/порядок фолбэков без слов владельца.

## T3 / pstack (`~/.config/t3-pstack/models.md`)

Лист ролей — единственный источник «кто что делает» внутри T3.
Провайдер/модель/effort и авторизованный фолбэк сохранять, молча не
подменять. Сводка на 23.11.2026:

| Роль | Место |
|---|---|
| основной работник (feature, bug-fix, perf, swarm и т.п.) | `acpRegistry_devin/swe-2-high` |
| судья и проза | `codex/gpt-6.2-sol` reasoningEffort=high |
| аккуратная ограниченная работа | `codex/gpt-6.1-terra` |
| быстрый read-only explorer | `antigravity/gemini-3.8-flash` |
| бесплатные быстрые задачи | OpenCode free models (по задаче) |

- Места Claude в T3 нет (hook блокирует): Claude запускается только как
  координаторская сессия, которую открывает сам владелец. Дизайн-работа —
  нативными Claude Code subagent с моделью sonnet внутри его сессии
  (владелец 21.11: «for design subagent I'd use sonnet, not swe»).
- `/setup-pstack` переписывает лист; каждая строка роли перекрывает
  дефолты навыков.
- Детали вызова (`delegate_task`, `modelSelection`, квота-стоп) —
  в `~/.hermes/skills/t3-work-bridge/SKILL.md`.

## Харнессы

| Харнес | Каталог | Заметки |
|---|---|---|
| Claude Code | `~/.claude` | координаторские сессии владельца; pstack-плагин |
| Codex | `~/.codex` | общий AGENTS.md = `~/.codex/AGENTS.md` |
| Devin | `~/.t3/tools/devin`, `~/.config/devin` | основной работник через T3 ACP |
| OpenCode | `~/.config/opencode` | бесплатные модели |
| Antigravity | agy | Gemini-места |

## Общие скрипты агентов

`~/.agents/bin` (роутер навыков владельца, t3-route, quota_breaker,
owner-done-check и др.), `~/.agents/lib` (`decisions.py` — единственный
клиент gemini-decision-1). Общий `~/.agents/AGENTS.md` — правила работы с
владельцем для всех харнессов.
