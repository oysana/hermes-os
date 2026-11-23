# Blueprint из постов владельца — 23.11.2026

Целевая схема воркфлоу H/S/Y + T3, построенная из присланных постов, а не из догадок.
Слова владельца (23.11.2026): «поэтому я кидал посты чтобы вы не смотрели на мои слова
и сами не думали с нуля как улучшать. важность постов должны были понять».

## Корпус

Источники: `research/post-receipts.jsonl` (133 записи), `~/.hermes/shared/library/`
(127 полных текстов), `~/.hermes/shared/x-saves/`, `~/.hermes/shared/bookmarks.jsonl` (79,
в основном дизайн-референсы — вне скоупа этой схемы), Telegram-история `state.db`
(только URL, роль user), `research/x-posts-20261112/sources.json` (ранее снятые
первоисточники с sha256).

Итог после дедупликации: **194 позиции** — 130 X-постов, 39 GitHub-репо, 24 статьи по URL,
1 X-article. Полный реестр: `research/posts-catalog-20261123.json`.

- Прочитано полным текстом: **170** (127 из library + 43 добрано через api.fxtwitter.com,
  включая 24 пустых capture и 11 login-wall файлов из library).
- Частично/по аннотации: ~15 (репо по URL без текста — узнаваемые проекты).
- Недоступно/не по теме: YouTube-видео (401 без транскрипта), ~6 постов-шуток/gif/однострочных
  ссылок, `xarticle:2122248894012661760` (дубликат — это статья внутри поста pontusab
  2122257368213819702).

## Принципы, повторяющиеся у независимых авторов

Важность = число независимых авторов и подтверждённость на практике (prod-цифры, не только идея).

### P1. Верификация — ограничивающий шаг; «готово» — факт среды, не отчёт агента (10+ авторов)

- poteto, «Loops You Can Trust» (x:2069824386283319343) и pstack guide ч.1
  (x:2094457600259842065): «Verification is all you need… if an agent can't verify its
  own work, nothing else matters. You remain the bottleneck». Метод: менеджерский рычаг —
  раз научить агента проверять, дальше масштабируется.
- Anthropic, «How the Claude Code team thinks about verification» (url, 17.11):
  «Without them, you can really only run one or two Claudes… With them, you can run
  dozens or hundreds in parallel». Отдельный грейдер по рубрике: +10 п. к успеху.
- Anthropic, code review (url, 16.11): чтение перенесли на машины — merges ×2, reverts −40%.
- 0xTrackmind (x:2124105677748527453): «It did not "decide" to skip the tests. Nothing in
  its world made the tests mandatory… A prompt is a request. A harness is a fact.» Пример:
  агент отрапортовал «40 эндпоинтов мигрировано», реально — 11.
- 0xwhrrari (x:2118327478250840140): «Your agent passed every test and still didn't fix
  the issue» — правило «done» должно жить в окружении.
- BHolmesDev (x:2118411592627052937): три рычага — adversarial review-агент, e2e-верификация
  флоу, merge-risk скоринг на каждый PR.
- sermakarevich (x:2106453816757354947): eval = повторяемый тест с числом и ошибкой,
  грейдер — часть системы и тоже тестируется.
- annabellschfr (x:2117956378065088750): regulated-команды строят evals до продакшена —
  trace→dataset→experiment→evaluate→потом мониторинг.
- GitHubNext PDD kit (x:2112936525457740139): «How do you guarantee code is correct?
  You prove it.»

### P2. Оркестратор остаётся у человека, воркеры уходят в фон; внимание владельца — узкое место (8+)

- sydneyrunkle/LangChain «Agents Are Going Async» (x:2118434475973726697,
  url:langchain-com-blog): «A good chief of staff keeps projects moving in the background
  while staying reachable». Паттерн: orchestrator с пользователем + async subagents,
  результат приходит по завершении. Конвергенция: Claude Code subagents, OpenAI dots,
  DeepSeek Harness router+workers.
- DeepSeek Harness super-agent (x:2116857257245987236): single-session design упирается
  в «a person can usually follow only 3 to 5 active sessions» — «multi-tasking is limited
  by human attention». Цель: «much value and little human attention».
- Anthropic «Building always-on agents» (url, 16.11): always-on = цикл sleep/run; агент
  спит большую часть времени и просыпается по событию, состояние переносится между runs.
- ai_database (x:2123005797332386071): у Anthropic ~80% времени сессии Claude — ожидание
  ввода человека.
- addyosmani (x:2119332146485891501): «Your coding agents made you a manager» —
  навыки управления агентами = навыки управления командой.
- MahyadGhassemi (x:2120022348925640771): «stop being a meat proxy… I turned everything
  that doesn't need my judgement into skills… I doubled my throughput».
- 0xCodez/Paradigm (x:2124260948256907704): «stop pinging agents and let them ping you».

### P3. Цели формулируются как постоянные исходы с метрикой и стоп-правилом (7+)

- hwchase17 + Actively (x:2121635683999883577): «design the scope of an agent such that
  it makes it as easy as possible to give feedback»; метрики, под которые агент оптимизирует.
- dabit3 (x:2124294531214385183): «goal → outcome» — 15 примеров («Fix this crash» →
  «keep crash-free sessions above 99.9%»; «Update the docs» → «docs accurate to main»).
- maestrooth (x:2123855918626451593): «give every task a finish line and a stop rule.
  Fails twice? Stop and ask. Retry loops are the worst leak».
- LoopX (gh, 16.11): control plane «turn long-horizon goals into verifiable results»,
  состояние/решения/результаты переживают сессии.
- SureForge (gh): research→ask→plan→build→verify→independent review, иначе «done»
  невалидно.
- Arindam (x:2122631856885846094): proactive-агент — «never page twice for the same
  problem, ask a human before doing anything risky».

### P4. Модель — меньшая часть; чинится харнесс и слои, не промпт (6+)

- distortgeekin (x:2123031990622437426): Anthropic — «80% of an agent's results come down
  to how it spends tokens, not the model»; LangChain — та же модель, другой сетап:
  52.8%→66.5%. Карта из 9 слоёв: prompt/context/skills → verification/loop/memory →
  harness/graph/tokens.
- beamnxw (x:2119806541821776215): четыре слоя — prompt, context, loop, harness; «a team
  that rewrites prompts when the harness has no verification step will never fix silent
  failure loops».
- Mr_Buzzoni (x:2119730968437432423): «agent = model + harness. The model collects the
  applause; the harness decides whether the agent actually works».
- heydengai (x:2119950162772582491): эксперимент AWS — «task description + code skeleton»
  даёт 29%→74% по тестам; артефакт следующего SDD — не Markdown-спека.
- Ronny_MiniMax (x:2119429272599474240): экономия пришла от меньшего числа model requests,
  а не от «умного» промпта.

### P5. Стоимость = пробуждения × контекст × шаги × фан-аут; триггеры вместо расписаний (8+)

- maestrooth (x:2123855918626451593): «usage is one equation: wake-ups × context × steps
  × fan-out»; «swap schedules for triggers» — 672 запуска/нед → 15; итог 2000→400 единиц.
- 0xwhrrari про Anthropic MCP (x:2124138527189331995): tool-context 150k→2k токенов;
  инструменты как файлы, фильтрация в коде до модели, циклы без round-trip, скиллы из
  рабочего кода.
- CrazyShyyt/HarnessMM (x:2119829427010044261): reuse решений через decision-graph —
  −87.93% токенов, успех вырос 71.43%→85.71%.
- nasuvit_z (x:2124441515196129660, первоисточник Anthropic contextual retrieval):
  KB ≤200k токенов — полный контекст + prompt caching бьёт RAG: latency ×2, cost −90%.
- tvytlx (x:2115253465693737317): markdown-папка из 11k файлов против SQLite — ×40 токенов,
  вопросы с глобальной статистикой без индекса просто падают (15M токенов и провал).
- Mr_Buzzoni (x:2104783226833186920) и кластер «Jev Engineering» (x:2119971224113803483,
  x:2120219078778609721): мелкие решения (which tool, did it pass, is it done) — дешёвой
  типизированной моделью ~$0.042/M; код пишется на 4–10% токенов, остальное — чтение,
  поиск, вывод команд.
- mdp_sec (x:2115614748742218045): метрика выбора — «cost per solved task» ($0.24 vs $7.72),
  не топ-строка бенчмарка.

### P6. Память — курируемое состояние; правила и инструкции гниют и должны зарабатывать место (7+)

- Stale-instructions paper (url arxiv 2610.30571) + LimestoneHQ (x:2119808229236699466):
  в 90% случаев агент следует устаревшему CLAUDE.md/AGENTS.md против кода; одна строка
  «при расхождении прав код» чинит большинство.
- davekiss (x:2119851595836092686): «rules have to keep earning their place against a
  number» — правила без подтверждения внешней метрикой авто-выбывают; число должно
  приходить из мира, не от AI-судьи.
- 0xMfox (x:2122741561595162780): Anthropic «dreaming» — ночью дешёвый агент перечитывает
  сессии и исправляет заметки; иначе «a note that was wrong on day one still loads on
  day ninety».
- dair_ai/SIM paper (x:2124510548070895895): harness-оптимизатор хранит rationale +
  predicted vs observed outcome каждой правки — иначе при смене модели evolved-харнесс
  хуже seed (68.41 vs 73.89).
- ai_database (x:2120077716460572735): правила инструкционного файла сортируются на
  «оставить / закрепить механизмом / удалить» — лишнее ухудшает результат.
- ai_database (x:2122581247553843259): без списка существующих возможностей агент
  переписывает готовое — тесты проходят, дубль невиден.
- HermesAgentTips (x:2118678768730403042): нативная память Hermes — два маленьких
  файла (~1300 токенов), frozen snapshot на старте сессии ради prompt-cache.

### P7. Фабрика на поддерживаемых примитивах бьёт самописную оркестрацию (7+)

- JoshARosen (x:2112902388302467493): build / buy / fork / delegate — всё чаще выигрывает
  «give the job to an always-on agent» вместо своей инфраструктуры.
- Warp «software factory as code» (url, 16.11): «All the agents at our company run out
  this one configuration file» — оркестрация, модели, автомации, скоринг, бенчмарки,
  self-improvement — как код под версионным контролем.
- Suraj Sharma (x:2122374135913628065): Anthropic Applied AI — «a single agent loop with
  skills and tools often beats complex multi-agent setups on quality, cost and speed»;
  один заказчик срезал runtime вдвое, свернув multi-agent в один.
- Paradigm playbook (x:2124260948256907704), шаги 5–6: «frontier model + short prompt +
  well-set-up codebase, then step away. skip custom plans, memory, compaction and skills»;
  «remove what distracts the swarm».
- poteto «Loops You Can Trust»: automations + навыки верификации = «loops that run
  themselves»; 2000 PR/мес через pstack (zenn-разбор 2500 PR/мес — url 16.11).
- cu30rry_ (x:2117938155567325676): один AGENTS.md + передача контекста через коммиты —
  «для агента код эффективнее естественного языка».

## Что уже совпадает у нас

- H = оркестратор у владельца, T3 = фоновые воркеры через официальный MCP, ожидание
  фоновым helper'ом — прямая реализация P2 (async subagents).
- OMH + hermes-lcm-x как память/контекст, единый клиент decisions.py на gemini-decision-1 —
  аналог Jev-модели из P5 («код решает, дешёвая типизированная модель для мелких решений»).
- 32/39 кронов переведены на no_agent script jobs (22.11) — прямая реализация P5.
- Один канонический AGENTS.md + симлинки (P7); rule→enforcer таблица (P1); REQUESTS.md
  отмечается только по PASS с доказательством (P3 частично).
- check.sh lanes + oracles (P1); SETUP-MAP.md генерируется из живых конфигов (зачаток P7
  «фабрика как код»).

## Расхождения

1. **Посты обрабатывались как квитанции, не как спека.** 133 записи, до этого разбора —
  поштучные состояния без единой картины; изменений по последней волне — 0. Прямая
  первопричина жалобы «воркфлоу не правильный».
2. **Остаток мёртвых и шумных джобов** (P5): paused-записи (Reading Nation one-shot
  истёк 19.11; h-rotate-watch; t3-completion-watch); `Провайдеры: отказ` каждые 10 мин —
  144 прогона/сутки при том, что алерт подождёт 30; `OyVoice: автономный бэклог` в
  agent-режиме ежечасно падает на 429 (retry loop без стоп-правила — прямая цитата
  maestrooth «the worst leak»); systemd-таймер pencil-claude-auth-guard ~каждые 5 мин.
3. **Нет обязательного outcome-контракта на входе** (P3): REQUESTS.md — свободный текст;
  критерий и стоп-правило появляются, если агент догадался их выписать. У T3-воркеров
  контракт уже есть (t3-work-bridge), на входе от владельца — нет.
4. **Контекст на ход не измерен как бюджет** (P5/P6): memory_char_limit 48000 +
  user 30000 против нативных 2200/1375; MEMORY.md уже 68 КБ — в лимит не влезает,
  хвост (новейшие записи) рискует обрезаться. Резать вслепую нельзя — сначала замер
  фактического payload на ход (см. P6, tvytlx).
5. **Лишнее против P7**: ~16 enabled плагинов (ariadne, seedguard, h-router-budget,
  bitwarden-session-bridge, telegram-*-reactions, claude-subscription-directsdk-experimental…)
  и ~10 watcher-скриптов без измеренной пользы — кандидаты «keep / enforce / delete»
  из ai_database. Не трогать вслепую: каждый отключается только после замера или
  подтверждения бесполезности.
6. **Нет метрики автономности** (P1/P3): посты предлагают «полезной работы на единицу
  внимания владельца». У нас нет счётчика «вмешательств владельца на доставленный
  результат» — поэтому «большой расход токенов без результата» не виден числом.

## Целевая схема (по постам)

```
Владелец ──исход (outcome: инвариант + метрика + стоп-правило + как давать feedback)──► REQUESTS.md
H (plain Hermes) ─ оркестратор, sleep/run-цикл:
  wake = событие/триггер; расписание только там, где событий нет
  ↳ разбор входа → outcome-контракт → очередь
  ↳ T3 workers (async): бриф = исход + верификация + стоп-правило + бюджет
  ↳ результаты возвращаются с доказательством; H не ждёт в ходе
Гейт: check lanes + oracle; merge только green; PROOF машине, эмпирика владельцу
Память: OMH curated + ночной пересмотр (dreaming); правила зарабатывают место
  (enforcer в RULES.md или retire); знания из постов оседают как навык/правило, не как квитанция
Стоимость: бюджет wake-ups×context×steps×fan-out; скрипт вместо агентского тика;
  decision-модель для мелких решений; low-usage режим при пороге пула
```

## Изменения по приоритету (сначала удаления и упрощения)

| # | Изменение | Источники | Критерий успеха (наблюдаемый) |
|---|---|---|---|
| 1 | Реестр постов → терминальные состояния, связанные с этим документом | задача владельца; HermesAgentTips x-bookmarks (digest→task) | все позиции каталога имеют состояние + ссылку на раздел blueprint |
| 2 | Cron-гигиена: удалить мёртвые paused-записи (Reading Nation one-shot, истёк 19.11); billing */10→*/30 | maestrooth, Arindam | `hermes cron list`: меньше записей; billing-скрипт вручную — ok; алерты не потеряны |
| 3 | Outcome-контракт в шапке REQUESTS.md | hwchase17, dabit3, maestrooth, SureForge | файл содержит контракт; новые записи следуют ему |
| 4 | ~~Пауза agent-mode джобы, падающей в retry-loop~~ — **сделано 23.11** (обе OyVoice на pause до сброса квоты) | maestrooth «fails twice → stop and ask»; omh fanout `limit_shaped`→cooldown | `hermes cron list`: обе paused; 0 failed runs; владельцу сказано |
| 5 | Замер per-turn контекста (memory+SOUL+skills payload) → потом решение по лимитам | tvytlx, HermesAgentTips, P5 | цифра токенов/ход до и после; решение по измерению, не вслепую |
| 6 | Инвентаризация plugins/watchers → keep/enforce/delete с владельцем | ai_database, Paradigm step 6 | таблица с вердиктом по каждому; отключённые — после бэкапа |
| 7 | Ночной пересмотр памяти (dreaming) — если OMH поддерживает активный режим | 0xMfox, Anthropic, davekiss | ночной прогон правит заметки, supersession-записи видны |
| 8 | Метрика «результатов на вмешательство владельца», еженедельно | DeepSeek «little human attention», P3 | число в отчёте; тренд вниз по вмешательствам |
| 9 | factory.yaml-описание сетапа поверх SETUP-MAP (декларативно, не новый код) | Warp factory-as-code | один файл описывает джобы/связи; расхождение с живым = lint |

## Что меняет для параллельных тредов

- **«Корень неавтономности»** (конвейер задач): синтез подтверждает направление —
  узкое место не конвейер, а отсутствие outcome-контракта и метрики внимания (P3, P2).
  Рекомендация: мерить не только эффективность конвейера, но и «вмешательств владельца
  на доставленный результат».
- **«Открытый OS сетапа»**: blueprint — исходный материал для docs/OS описания целевого
  воркфлоу; п.9 (factory.yaml-поверх-SETUP-MAP) стыкуется с их `scripts/setup-map.py`.
- **«@mention ботов»**: независимо; их oracle-подход (RED→GREEN) прямо соответствует P1.

## Внедрено в этом заходе

1. Этот документ + `research/posts-catalog-20261123.json` + терминальные состояния в
   `research/post-receipts.jsonl`.
2. Cron-гигиена (бэкап `cron.db` + `hermes cron list` в `.artifacts/blueprint-posts-20261123/backups/`).
3. Outcome-контракт в `~/.hermes/shared/REQUESTS.md` (бэкап туда же).
4. Контракт делегата + карантин + resume в `t3-work-bridge` (дополнение 23.11); обе OyVoice-джобы на pause по limit-cooldown.

Не внедрено (требует замера/решения владельца): пункты 5–9 таблицы; memory-лимиты;
отключение плагинов; dreaming-режим OMH.

## Дополнение 23.11 — oh-my-hermes fanout (PR #3122, main/Unreleased)

Источник: `~/.hermes/cache/scratch/omhsrc/` — `docs/FANOUT.md` (1799 строк),
`FANOUT-EXECUTOR-EVIDENCE.md`, `executor_presets.py` (пресеты `validated:false`),
CHANGELOG 4.0.8…main. Пост владельца 23.11 08:43; прочитан по первоисточнику.
Принцип внедрения: берём контракт, **не ставим** `omh coding run` вторым
оркестратором — оркестратором делегатов остаётся T3.

Четыре правила, перенесённые в `~/.hermes/skills/t3-work-bridge/SKILL.md`
(секция «Delegate acceptance is git, not words»):

1. **Приёмка по git, не по словам.** Верbatim из FANOUT: «the unit is complete
   only when the CLI exits 0 and left a clean commit descending from the base;
   prose on stdout, a claimed commit or exit 0 with no commit does not count».
   У нас: новый коммит от базы + `merge-base --is-ancestor` + чистый
   `git status` + зелёные проверки проекта. Read-only делегаты помечаются
   в брифе, гейт к ним не применяется.
2. **Классификация отказа + карантин.** `failure_kind`: `limit`, `auth`
   (классифицируется до limit — ожидание не чинит креды), `timeout`, `crash`.
   Наблюдаемый limit/auth = cooldown сиденья провайдера: новой работе туда —
   отказ до окна, отказ владельцу озвучивается (вместо тихой смерти помощников
   на исчерпанном Codex). Без тихого ретаргета: «no coding owner is ever
   switched without an explicit choice» = наше правило no-silent-fallback;
   запасной исполнитель — только по `~/.config/t3-pstack/models.md`.
3. **Resume той же сессии при повторе** (`session.resume` в fanout;
   у нас — `t3_thread_send` в тот же тред, Devin `-r <id>`), а не новый
   диспатч с нулевого контекста.
4. **Минимум статического контекста** (`omh setup --core`: каждый навык — строка
   индекса в каждом запросе + ~8k chars на body-load до compaction). Соответствует
   P5/P6: skills читаемые по требованию вместо always-on индекса. Прямого маппинга
   на нашу установку нет (H-навыки = «обучение H», по AGENTS не трогаем) —
   зафиксировано как принцип для новых добавлений.

### Применено живьём (23.11)

- Правила 1–4 записаны в `~/.hermes/skills/t3-work-bridge/SKILL.md` (бэкап:
  `.artifacts/posts-blueprint-20261123/backups/t3-work-bridge-SKILL.md-20261123b`).
- **Карантин по limit:** `hermes cron pause 0adb2e01dd13` (OyVoice backlog engine,
  падает HTTP 429 в retry-loop) и `hermes cron pause eed9ea1c0e41` (OyVoice daily
  review) — до сброса квоты Codex; решение о возобновлении — за владельцем.
- **Живой прогон git-гейта:** ворктри blueprint → `commits_from_base=1
  ancestor=yes dirty=0` (принят); основной чекаут → `commits=0 dirty=72`
  (верно отклонён: без коммита и с чужими правками приёмки нет).

Не проверено: фактическое поведение правила на следующем реальном делегате
T3 (правило в skill-файле, применяется читающим его агентом); срок квоты Codex
и момент возобновления OyVoice-джобов.


## Статус внедрения: заморозка живых правок (координация H, 23.11)

Параллельные треды (@mention, очередь/kanban, автообновление) меняют живой
Hermes. Новых живых правок не делать до команды H; шлюз не перезапускать.

### Уже живо (сделано до заморозки, откат указан)

| Изменение | Откат |
|---|---|
| Удалён cron-джоб `ec10c4b89936` (просроченный one-shot) | создать заново по `cron-list-20261123.txt` |
| Billing-watcher `*/30` (было `*/10`) | `hermes cron edit bc8c10bcfeb7 --schedule '*/10 * * * *'` |
| OyVoice `0adb2e01dd13` + `eed9ea1c0e41` paused (limit-cooldown) | `hermes cron resume <id>` после сброса квоты |
| Outcome-контракт в шапке `~/.hermes/shared/REQUESTS.md` | восстановить из `backups/REQUESTS.md-20261123` |
| Секция delegate-acceptance в `t3-work-bridge/SKILL.md` | восстановить из `backups/t3-work-bridge-SKILL.md-20261123b` |

### Готовые правки, ожидающие команды H

1. **Resume OyVoice после сброса квоты Codex.** Правка: `hermes cron resume
   0adb2e01dd13 && hermes cron resume eed9ea1c0e41`. Проверка: `hermes cron
   runs --limit 20` — следующий run ≠ HTTP 429; `cron list` — обе `[active]`.
   Предварительно убедиться, что квота реально сброшена (один ручной run
   дешёвой джобы или проверка provider billing watcher).
2. **Замер per-turn статического контекста** (п.5 таблицы; read-only —
   можно и до снятия заморозки, файлов не меняет). Правка: посчитать
   `wc -c ~/.hermes/SOUL.profile.md`, суммарный размер skills-индекса
   (36 навыков H + 84 по профилям, по правилу omh ~index-line + ~8k
   chars/body-load на навык) и OMH-recall payload одного реального хода
   из журнала. Проверка: число токенов/ход зафиксировано в этом документе;
   решение о сокращении принимается по измеренному, не вслепую.
3. **Инвентаризация plugins/watchers → keep/enforce/delete** (п.6).
   Правка: таблица `path | что делает | источник-опора | вердикт` для
   `~/.hermes/plugins`, `~/.hermes/scripts`, cron-джобов; отключение —
   только после бэкапа и с владельцем. Проверка: каждая строка имеет
   вердикт и ссылку на источник; удалённое покрыто бэкапом в `.artifacts/`.
4. **factory.yaml поверх SETUP-MAP** (п.9, декларативно). Правка: один
   файл, перечисляющий джобы/профили/связи H-S-Y-J; lint = расхождение
   с живым состоянием. Проверка: `check-select.py` подбирает лейн или
   линт встроен в `check.sh`; расхождение = красный вывод.
