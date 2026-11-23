# Расписания

Две машины времени: cron внутри Hermes и systemd user timers.
Актуальный список включённых задач — секция «Включённые cron-задачи»
в `SETUP-MAP.md`.

## Hermes cron (`*/cron/jobs.json` каждого профиля)

Задача — это промпт, который поднимает ход профиля в его топике.
Виды расписаний: `cron` (выражение), `interval` (минуты), `once`.
Системные задачи H на 23.11.2026: утренний отчёт, сторож диска,
лимиты провайдеров/Codex, `t3-tick`, `t3-stuck-watch`, `kanban-watch`,
`quota-audit`, автономный бэклог OyVoice. У Y/S/J — свои бытовые,
учебные и job-search задачи.

Скрипты, которые вызывают задачи/вотчеры, лежат в `~/.hermes/scripts/`;
два из них вызывают код этого репо:
`morning-report.sh` → `scripts/morning-diff-report.py`,
`quota-audit.sh` → `scripts/verify-quota-outcome.py`.

## systemd user units (`~/.config/systemd/user/`)

| Unit | Роль |
|---|---|
| `hermes-gateway.service` | шлюз Telegram ↔ профили, `Restart=always`, reload `SIGUSR1` |
| `hermes-omh-update.timer` | ежедневное self-update Hermes+OMH в 04:30 (clean-tree gated) |
| `hermes-integration-health.timer` | пробник интеграций (google/helium/bitwarden) |
| `hermes-whatsapp-reader.service` | WhatsApp-ридер личного intake Y |
| `agent-chrome.service` | агентский Chrome-профиль (CDP :9333) |
| `hermes-chrome-cdp-relay.service` | одобренный CDP-мост к Chrome/Helium владельца |
| `hermes-inhibit-sleep.service` | запрет сна на недели удалённой работы |
| `actions-runner-*.service` | self-hosted GitHub runners: hermes-pro, oynote, voicebridge, branding |

## Правила времени

- Часовой пояс сетапа — `Asia/Almaty` (в конфигах и скриптах).
- Долгие операции ждутся одним вызовом (до 5 минут) или watcher'ом
  в cron — не опросом каждую секунду: каждый возврат заново шлёт весь
  разговор в модель.
