# Происхождение аудита research/x-posts-20261112

Импортировано 15.11.2026 из worktree-аудита 12.11.2026:

- источник: `/home/yqecea/.t3/worktrees/hermes-pro/fix-lam-verified-contin-dac084d809774f4d/research/x-posts-20261112/` (6 файлов, `cp`, источник сохранён);
- проверка при мерже: `review.jsonl` — 60 записей, 60 уникальных id, все `original_read: true`; статусы: внедрено 3, отложено 47, отклонено 10.

## Ограничение ссылок

`REPORT.md` содержит относительные ссылки `../../...` на скрипты, тесты и
verification-артефакты, которые были созданы в той worktree-ветке и **не**
сливались (чужой in-flight scope по MERGE-PLAN в
`.artifacts/h-superagent-overnight-20261114/lane-corpus/COVERAGE.md`):

- `scripts/capture-x-posts.py`, `scripts/check-post-audit.py`,
  `scripts/check-pr-head-lifecycle.mjs`, `scripts/eval-source-grounding.py`,
  `scripts/kanban-cost-audit.py`, `scripts/measure-kanban-worker.py`,
  `scripts/measure-tool-catalog.py`
- `tests/decision-routing/pilot-20261111.json`,
  `tests/kanban-cost/fixtures/paired-measurement.json`,
  `tests/orchestration-models/test_pr_done.py`,
  `tests/technique-audit/measurements.json`
- `verification/kanban-cost/laws/PROOF.bend`,
  `verification/orchestration/README.md`,
  `verification/source-grounding/laws/PROOF.bend`

Эти ссылки резолвятся только в исходном worktree (путь выше), пока он жив, —
либо после отдельного осознанного мержа того scope. Здесь перенесены только
вердикты аудита (60 постов), не чужая несданная работа.

## Корпус и ledger-флаги

Сам корпус постов живёт в `.artifacts/research/x-posts-20261112/` (gitignored:
`read/`, `primary/`, `capture.json`, `reading-ledger.json`, корпусные скрипты).
Флаги «разобран/не разобран» в `capture.json` — снимок на момент захвата
(01:32 12.11); аудит дописывался позже, поэтому флаги там устарели ровно
так, как описано в lane-corpus/COVERAGE.md. Флаги не правились вручную.
Полноформатные per-post заметки (37 постов) лежат рядом с корпусом:
`.artifacts/research/x-posts-20261112/lane-notes/{NOTES,INDEX,COVERAGE}`.

Корпусные скрипты (`finish-notes.py`, `notes-first.py`, `sources-ledger.py`)
читают `research/x-posts-20261112/review.jsonl` — после этого мержа они
работают на каноническом чекауте.
