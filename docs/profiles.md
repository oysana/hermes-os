# Профили Hermes

Четыре профиля за одним шлюзом `hermes-gateway`. У каждого свой `config.yaml`,
плагины, навыки, cron и память. Профили разные намеренно — это роли,
а не рассинхрон (Йусуф, 05.11.2026). Актуальные списки — в `SETUP-MAP.md`.

| Профиль | Каталог | Роль | Модель (из config.yaml) |
|---|---|---|---|
| H | `~/.hermes` (default, алиас hqecea) | Работа и T3: единственный собеседник в Telegram, координирует делегирование и приёмку | gpt-6.1-terra (openai-codex), фолбэк gpt-6-luna |
| Y | `~/.hermes/profiles/yqecea` | Личное: люди, деньги, напоминания, быт | gpt-6.1-terra (openai-codex), фолбэк gpt-6-luna |
| S | `~/.hermes/profiles/sqecea` | Учёба: задания, разбор материала | gpt-6.1-terra (openai-codex), фолбэк gpt-6-luna |
| J | `~/.hermes/profiles/jqecea` | Поиск работы | claude-haiku-5-5 (claude-subscription-directsdk-experimental) |

Расхождение с прозой: в AGENTS.md записано «S и Y используют только OpenCode
Go», но живой `config.yaml` у обоих показывает `openai-codex` + terra/luna.
Источник истины — конфиг; строка в AGENTS.md устарела (не проверено, когда
модели переключили обратно).

## Что у профиля своё

- `config.yaml` — модель, фолбэки, `plugins.enabled`, автозагрузка навыков.
- `plugins/` — каталог установленных плагинов профиля.
- `skills/` — навыки профиля (у H общие в `~/.hermes/skills/`).
- `cron/jobs.json` — расписание профиля.
- `memories/MEMORY.md` — память профиля (только append).
- `SOUL` / характер — собирается из `~/.hermes/shared/CHARACTER.md`
  скриптом `~/.hermes/shared/build-soul.sh` (кап 20 000 символов).

## Посторонние каталоги в profiles/

`setup-map.py` помечает каталоги без живого `config.yaml`. На 23.11.2026 это
`sqceea` (опечатка от sqecea, конфиг пуст), `omh-tui` и `.deleted`. Не
использовать как профили.

## H особенный

- Единственный говорит с владельцем в Telegram; Y/S/J работают в фоне и
  через свои топики/уведомления.
- Плагины только у H: `hermes-lcm-x` (lossless context engine),
  `t3-route`, `h-router-budget`, `ariadne`, `tokenjuice`, `inbox`,
  `claude-subscription-directsdk-experimental` (см. SETUP-MAP).
- Плагин только у Y: `personal-auto-reply` (политика личных ответов).
- Плагин только у J: `internship-scout`.
- Удалённые слои не возвращать: диспетчер H2, плагин `hermes-pro`,
  turn hook и task ledger удалены 18.11.2026; архивы —
  `.artifacts/debloat-hp-h2-20261118/`.
