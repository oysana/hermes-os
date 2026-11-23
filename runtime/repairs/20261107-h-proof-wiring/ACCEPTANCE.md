# h-proof-wiring-20261107 — приёмка

Бриф: ~/.hermes/work/briefs/h-proof-wiring-20261107.md (владелец yqecea, 07.11.2026).
Стек: поверх PR #6 fix/h-wake-steer-20261107 (HEAD 20bb659).

## Слой 1 — хук запуска

Новый pre_tool_call хук `~/.hermes/agent-hooks/t3-proof-gate.py`, зарегистрирован
в `~/.hermes/config.yaml` (matcher `mcp__t3__(delegate_task|t3_thread_send)|terminal`).
Отклоняет запуск и прогон T3 без раздела «Что вернуть» с блоком PROOF; покрыт и
обход через `t3-mcp-call.py`. Ошибка хука — fail-open с записью в
`~/.hermes/state/t3-proof-gate.log`. Обход владельца:
`~/.hermes/state/allow-t3-no-proof`.

- Тест: `cd ~/.hermes/agent-hooks && python3 -B -m unittest test_t3_proof_gate` → 9/9 OK, rc=0.
- Живой отказ: `echo '{"tool_name":"mcp__t3__delegate_task","tool_input":{"task":"сделай фичу"}}' | python3 t3-proof-gate.py` → `{"action":"block",...}` rc=0.
- Пропуск с шаблоном: payload с «## Что вернуть» + PROOF + «код возврата» → пустой вывод, rc=0.
- Fail-open: `echo 'not json' | python3 t3-proof-gate.py` → rc=0, ошибка в журнале.

## Слой 2 — возврат

`t3-return-sweep.py`: на завершённом запуске вызывается `default_proof` →
`proof_check.thread_text+judge` (только status=completed). Принято — блок PROOF
дословно в reason wake-события. Не принято — `default_steer` шлёт
`t3_thread_send mode=steer` в тот же поток со списком недостающего, пишет маркер
`T3-Steer:` (один steer на run), карточка не закрывается; при недоступности
steer — обычный парк+wake с перечнем недостающего. Сбой проверки — вердикт
unavailable, старый путь парковки.

- Тест: `cd ~/.hermes/scripts && python3 ~/.hermes/shared/proactive/run-selected.py test_t3_return_sweep.py` → 17/17 OK, rc=0.

## Слой 3 — ответ владельцу

`turn-brief.py::t3_ingestion_brief` +1 строка (68 знаков):
«Блок PROOF из отчёта T3 владельцу передавай дословно, без пересказа.»

- Тест: `test_turn_brief.py::test_return_brief_orders_verbatim_proof_block_once` → OK.
- test_turn_brief_size.py → 29/29 OK.

## Полный прогон затронутых сьютов

- test_t3_proof_gate + test_no_self_verify: 14/14 OK.
- test_t3_return_sweep: 17/17 OK.
- test_turn_brief: 139/140; 1 сбой `test_t3_bridge_skill_keeps_thresholds_and_honest_failure` —
  предсуществующий: t3-work-bridge/SKILL.md сокращён другой работой 07.11 23:20,
  строка 'A written brief is not a sent task.' удалена из навыка, тест читает живой файл.
- test_completion_gate: 30/30 OK. test_proactive: 75/75 OK.
- proof_check.py --selftest → SELFTEST OK, rc=0.
- verify-harness.py: предсуществующий сбой 'runtime persona missing or truncated'
  (SOUL.md 22290 знаков > cap 20000, loader усекает); до моих правок то же самое.

## Файлы

- `~/.hermes/agent-hooks/t3-proof-gate.py` (новый), `test_t3_proof_gate.py` (новый)
- `~/.hermes/config.yaml` (+3 строки hooks.pre_tool_call)
- `~/.hermes/scripts/t3-return-sweep.py`, `test_t3_return_sweep.py`
- `~/.hermes/shared/proactive/turn-brief.py` (+1 строка), `test_turn_brief.py` (+1 тест)

## Не проверено

- Сквозной прогон: реальный завершённый T3-запуск, принятый/отклонённый свипом
  (нужен живой возврат; юнит-ветки покрыты).
- Реплей 10 возвратов для слоя 3 — замер после того, как свип накопит приёмы.
- Доля верхнеуровневых запусков с картой цели (замер из брифа) — после запуска хука.
