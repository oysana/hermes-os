# Память

Два слоя: контекст хода и долгосрочные факты. Плюс канал между H и T3.

## Контекст хода

- У H движок контекста `hermes-lcm-x` (плагин, lossless) + локальные
  эмбеддинги (`hermes-lcm-x-local-embeddings`).
- Компрессия: 0.5 окна у H, Y, S. Пороги по моделям
  (`lcm.model_thresholds`): Terra 0.5, Sol 0.3. Ниже владельца опускать
  нельзя: Claude `autoCompactWindow` ≥ 500000, Codex
  `model_auto_compact_token_limit` ≥ 500000.

## Долгосрочные факты: OMH

- Плагин `omh` включён у H, Y, S; хранилище `~/.omh`
  (у профилей — `profiles/<p>/.omh` по настройке `omh_home`).
- Это и есть общая память H↔T3: работники вызывают
  `omh memory recall`/`omh memory capture --scope-kind user-global
  --scope-ref default`. Направлять работников в `~/.hermes/memories`
  или строить параллельный канал памяти запрещено.
- `omh-gemini-decisions` — решения через gemini-decision-1 с возможностью
  переопределения инструментом.

## Память профиля

- `~/.hermes/memories/MEMORY.md` и `profiles/*/memories/` — только
  append: новую запись добавляют, старую не переписывают и не удаляют.
- `sleep-consolidation` — навык свёртки памяти (в библиотеке H).
- `memory-rewind` — плагин отката у S и Y.

## Контрольная запись

- `~/.hermes/shared/` — оперативные записи владельца/агентов:
  `REQUESTS.md` (просьбы, PRD), `STATUS.md`, `RULES.md`
  (правило → enforcer), `CHARACTER.md` → `build-soul.sh` → SOUL.
- `decisions.tsv` в репо (gitignored) — журнал решений, append-only.
- Исследовательская запись — Hermes NotebookLM; добавление источников —
  по `docs/NOTEBOOKLM_WORKFLOW.md`.
