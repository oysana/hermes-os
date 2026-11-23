# SETUP-MAP — что реально настроено

сгенерировано 23.11.2026 09:01 скриптом `scripts/setup-map.py`
из живых конфигов `~/.hermes` и `~/.config/t3-pstack/models.md`. Не редактировать вручную.

## Профили
### H — default (hqecea)
- роль: работа и T3: координирует делегирование, проекты, приёмку
- модель: gpt-6.1-terra (openai-codex); фолбэки: gpt-6.1-terra (openai-codex), gpt-6-luna (openai-codex)
- плагины (16): advanced-delegation, ariadne, bitwarden-session-bridge, claude-subscription-directsdk-experimental, gemini-skill-picker, h-router-budget, hermes-lcm-x, hermes-lcm-x-local-embeddings, inbox, omh, omh-gemini-decisions, seedguard, t3-route, telegram-owner-reactions, telegram-task-reactions, tokenjuice
- cron: 8 включено из 38
- память MEMORY.md: 67.3 КБ
- автозагрузка навыков: нет

### Y — yqecea
- роль: личное: люди, деньги, напоминания, быт
- модель: gpt-6.1-terra (openai-codex); фолбэки: gpt-6-luna (openai-codex)
- плагины (10): advanced-delegation, bitwarden-session-bridge, memory-rewind, omh, gemini-skill-picker, omh-gemini-decisions, personal-auto-reply, seedguard, telegram-owner-reactions, telegram-task-reactions
- cron: 22 включено из 35
- память MEMORY.md: 19.5 КБ
- автозагрузка навыков: нет

### S — sqecea
- роль: учёба: задания, разбор материала
- модель: gpt-6.1-terra (openai-codex); фолбэки: gpt-6-luna (openai-codex)
- плагины (9): advanced-delegation, bitwarden-session-bridge, memory-rewind, omh, gemini-skill-picker, omh-gemini-decisions, seedguard, telegram-owner-reactions, telegram-task-reactions
- cron: 8 включено из 18
- память MEMORY.md: 13.0 КБ
- автозагрузка навыков: нет

### J — jqecea
- роль: поиск работы
- модель: claude-haiku-5-5 (claude-subscription-directsdk-experimental); фолбэки: нет
- плагины (6): gemini-skill-picker, advanced-delegation, internship-scout, seedguard, telegram-task-reactions, claude-subscription-directsdk-experimental
- cron: 2 включено из 4
- память MEMORY.md: 0.0 КБ
- автозагрузка навыков: нет

## Маршруты H
- moa default: gpt-6.1-terra (openai-codex), gpt-6-luna (openai-codex); агрегатор gpt-6.1-terra (openai-codex)
- Telegram-топики (chat_id владельца замаскирован):
  - Главное — топик 9265
  - Уведомления — топик 9267
  - OyVoice — топик 9272
  - OyMessage — топик 9274
  - AiPay — топик 9276
  - OyKaspi — топик 16840

## Роли T3 (pstack)
- feature, refactoring: acpRegistry_devin/swe-2-high thought_level=medium speed=standard
- bug-fix: acpRegistry_devin/swe-2-high thought_level=high speed=standard
- perf-issue: acpRegistry_devin/swe-2-high thought_level=high speed=standard
- hillclimb: acpRegistry_devin/swe-2-high thought_level=medium speed=standard
- judgment and prose: codex/gpt-6.2-sol reasoningEffort=high
- hardest tasks: codex/gpt-6.2-sol reasoningEffort=high
- how explorer: antigravity/gemini-3.8-flash thinking_level=medium
- how explainer: codex/gpt-6.2-sol reasoningEffort=medium
- why investigators: codex/gpt-6.2-sol reasoningEffort=high
- why synthesizer: codex/gpt-6.2-sol reasoningEffort=medium
- reflect tooling: codex/gpt-6.1-terra reasoningEffort=medium
- reflect judgment, divergent, synthesizer: codex/gpt-6.2-sol reasoningEffort=high
- arena runners: acpRegistry_devin/swe-2-high thought_level=high speed=standard, codex/gpt-6.1-terra reasoningEffort=high
- arena cross-judge pool: acpRegistry_devin/swe-2-high thought_level=high speed=standard, codex/gpt-6.2-sol reasoningEffort=high
- swarm workers: acpRegistry_devin/swe-2-high thought_level=high speed=standard
- architect runners: acpRegistry_devin/swe-2-high thought_level=high speed=standard, codex/gpt-6.2-sol reasoningEffort=high
- interrogate reviewers: codex/gpt-6.2-sol reasoningEffort=high, acpRegistry_devin/swe-2-high thought_level=high speed=standard
- session hook: on

## Включённые cron-задачи
- [H] Диск: сторож + самолечение — 0 9,15,21 * * *
- [H] Провайдеры: отказ по оплате/лимиту — */30 * * * *
- [H] Codex: остаток лимита — every 60m
- [H] Лимиты подписок: сторож темпа — every 60m
- [H] t3-stuck-watch — */30 * * * *
- [H] t3-tick FSM poll — every 5 min
- [H] daily quota-outcome audit — every day 07:00
- [H] kanban-watch — 0 */4 * * *
- [Y] 22 включённых личных задачи (имена скрыты в публичной версии)
- [S] 8 включённых личных задачи (имена скрыты в публичной версии)
- [J] 2 включённых личных задачи (имена скрыты в публичной версии)

## Посторонние каталоги профилей
- sqceea (config.yaml пуст)
- omh-tui (config.yaml пуст)
- .deleted (config.yaml пуст)

