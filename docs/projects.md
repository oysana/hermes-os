# Проекты

Все проекты живут в `~/coding-projects/`. Карта проектов —
`~/coding-projects/PROJECTS.md`, единственный курируемый индекс:
у каждого проекта поле `Open:` — каноническая точка входа.

## Как устроено

- Многие проекты лежат на HDD-зеркале `/mnt/HDD/yqecea/coding-projects/`;
  одноимённые симлинки в `~/coding-projects/` резолвятся туда. Если
  `/mnt/HDD` недоступен — сказать об этом, а не объявлять проект потерянным.
- Похожие каталоги — не отдельные проекты: симлинки, linked worktrees и
  копии-варианты принадлежат записи в карте. Никогда не выбирать проект
  по похожести имён.
- Летучее состояние (SHA, ветки, привязки) — в registry
  `~/coding-projects/.maintenance/filesystem-organization-20260905/registry/`,
  не в PROJECTS.md.
- Что на каком харнессе установлено и как обновляется —
  `~/coding-projects/SETUP.md`.
- Параллельная работа агентов — в worktrees `~/.t3/worktrees/<repo>/*`,
  чтобы не сталкиваться в одном чекауте.

## CI у проектов

У части репо поднят self-hosted runner как systemd unit
(`actions-runner-<repo>.service`, рабочий каталог `~/actions-runner/<repo>`).
У этого репо — `actions-runner-hermes-pro.service` + workflow
`.github/workflows/check.yml` (`canonical-check` → `scripts/check.sh`).

## Добавить проект

### Greenfield (новый)

1. Создать каталог в `~/coding-projects/<name>`, `git init`, базовые
   файлы (README, AGENTS.md).
2. Добавить запись в `PROJECTS.md` с полем `Open:`.
3. Прогнать проверку индекса:
   `node ~/coding-projects/.maintenance/filesystem-organization-20260905/scripts/check-projects-index.mjs`.
4. Работу отдавать T3-работнику в его worktree; приёмка — как обычно
   (реальный путь, доказательство).

### Brownfield (существующий код)

1. Найти или добавить запись в `PROJECTS.md` (не по похожести имени
   каталога — по карте).
2. Проверить, что `Open:`-путь существует; если нет — посмотреть
   registry/roots.jsonl и STORAGE-MAP.md и сообщить о расхождении,
   а не угадывать путь.
3. Если нужен CI — добавить runner по образцу `actions-runner-*.service`.
4. Новому агенту: читать `AGENTS.md` проекта, потом `CURRENT_WORK.md`/`PLAN.md`.

## Этот репо в карте

`hermes-pro` — репозиторий-карта и проверки самого сетапа. Изменения в нём
не «устанавливаются»: живое поведение меняется только через файлы в
`~/.hermes`, `~/.agents`, `~/.config` — с бэкапом и проверкой.
