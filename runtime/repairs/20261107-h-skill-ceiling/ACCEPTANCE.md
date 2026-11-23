# Skill-ceiling hook — acceptance record (t_a58edb96)

Brief: `~/.hermes/work/briefs/h-skill-ceiling-20261107.md` (07.11).
Card: `t_a58edb96` (blocked on the config-path question; resolved by
`DECISION.md` — option A, `hermes plugins enable`).

## What is installed

- `~/.hermes/plugins/skill-ceiling/` — `__init__.py`, `plugin.yaml`
  (`provides_hooks: [pre_tool_call]`), `test_skill_ceiling.py`. Snapshots in
  `skill-ceiling/` here are byte-identical to installed (`sha256` matches).
- `~/.hermes/scripts/self-audit.py` — `skill_review_audit()` + one output
  line; `self-audit.py.patch` is the exact delta (verified: applying it to
  the reconstructed pre-image reproduces the installed file).
- `~/.hermes/scripts/test_self_audit.py` — fixture tests for the new counter.
- `~/.hermes/config.yaml` — `plugins.enabled` contains `skill-ceiling`
  (via the owning enable interface; see DECISION.md).

## Gate results

| Check | Command | Result |
|---|---|---|
| Plugin unit tests | `cd skill-ceiling && python3 -B test_skill_ceiling.py` | 12 tests OK, rc=0 (also rerun 08.11 in this dir) |
| Self-audit tests | `cd <this dir> && python3 -B test_self_audit.py` | 3 tests OK, rc=0 |
| Config value | `hermes config get plugins.enabled` | `skill-ceiling` present |
| Plugin status | `hermes plugins list` / `doctor skill-ceiling` | enabled; 1 hook registered |
| Live veto | `~/.hermes/logs/skill-ceiling.jsonl` | blocked events: probe 07.11 23:23 (4x), oyvoice-project 08.11 19:14 + 19:42, autonomous-ai-agents/hermes-agent 08.11 09:19 (+05) |
| Self-audit live line | `python3 ~/.hermes/scripts/self-audit.py --write` (07.11) | `правок навыков фоновым ревью за 7 дней: 418 (по журналу), запусков с result=none: 91` (see verify-20261107.log) |

Live refusal text (probe, 07.11): `SKILL.md ceiling: правка даст 30490 знаков
при лимите 25000 (было 30000). перенеси подробности в references/ и сократи
SKILL.md`; shrinking write passes.

## Not verified

- Whether background review will stop retrying the same oversized skills on
  every run (the journal will show whether blocked count decays).
- Behaviour when a skill's SKILL.md sits exactly at 25,000 chars (boundary
  covered only by unit tests, not a live call).
