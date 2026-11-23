# AGENTS.md — agent map of the setup

This repository describes and checks Yusuf's agent setup. It is not executed:
the live system runs from `~/.hermes`, `~/.agents`, the harness homes and
`~/coding-projects`. Remote: https://github.com/oysana/hermes-pro
(repository ID 1423571959). OyMessage (ID 1417001225) and VoiceBridge are
separate projects; Hermes work never writes into them.

Read `README.md` for the picture, `SETUP-MAP.md` for the generated live
map and `docs/` per layer. What follows is the working map for an agent.

## Architecture (verified against live configs, 23.11.2026)

- **H** is plain Hermes, profile `default` (`~/.hermes`), the only Telegram
  interlocutor and the outer loop. Custom layers around H stay deleted
  (H2 dispatcher, `hermes-pro` plugin, turn hook, task ledger; archives in
  `.artifacts/debloat-hp-h2-20261118/`).
- **Y** (`~/.hermes/profiles/yqecea`), **S** (`sqecea`), **J** (`jqecea`)
  are separate Hermes profiles behind the same `hermes-gateway` service.
- **T3 Code is the inner loop**: Devin, Codex, Antigravity, OpenCode +
  pstack. H reaches it only through T3's official MCP connector
  (`mcp_servers.t3` → `t3_*` tools), per `~/.hermes/skills/t3-work-bridge/SKILL.md`.
- **Shared memory H↔T3 is OMH** (`omh memory recall/capture
  --scope-kind user-global --scope-ref default`). Never point workers at
  `~/.hermes/memories`, never add a parallel memory channel.
- H context engine: `hermes-lcm-x` (lossless). Decisions: `gemini-decision-1`
  via `~/.agents/lib/decisions.py` — the only decision model.
- Live model truth is the config, not prose: run
  `python3 scripts/setup-map.py` for the current map. `~/.config/t3-pstack/models.md`
  is the T3 role sheet.

## Where things live

| What | Path | Rule |
|---|---|---|
| Owner requests (PRD) | `~/.hermes/shared/REQUESTS.md` | Every new request goes here in the same turn, and into `PLAN.md` with a day. Tick only on a real-world PASS with evidence. |
| Internal state | `CURRENT_WORK.md`, `PLAN.md` here; `~/.hermes/shared/STATUS.md` | Agent bookkeeping, never a substitute for a clear answer to Yusuf. |
| Rule → enforcer table | `~/.hermes/shared/RULES.md` | One row per rule; a real test or `prose`. |
| Character / SOUL | `~/.hermes/shared/CHARACTER.md` → `bash ~/.hermes/shared/build-soul.sh` | SOUL cap 20,000 chars. |
| Telegram/WhatsApp intake | `~/.hermes/shared/communications/` | Y's intake; reply policy in `~/.hermes/profiles/yqecea/plugins/personal-auto-reply/`. |
| Skills | `~/.hermes/skills/`, `~/.hermes/profiles/*/skills/` | H's self-written skills and the post library are its learning: keep them. |
| Live scripts | `~/.hermes/scripts/`, `~/.agents/lib/`, `~/.agents/bin/` | Total may only shrink (`scripts/custom-ratchet.py`). |
| Repo scripts | `scripts/` here | `morning-diff-report.py` and `verify-quota-outcome.py` are invoked by live `~/.hermes/scripts`; the rest serve `check.sh`. |
| Evidence | `.artifacts/` here (gitignored) | New work writes evidence here; backups under `.artifacts/<task>/backups/`. |
| Decision log | `decisions.tsv` here (gitignored) | Append only. |
| Research record | Hermes NotebookLM | Follow [docs/NOTEBOOKLM_WORKFLOW.md](docs/NOTEBOOKLM_WORKFLOW.md) before adding Sources. |
| Live workspace | `work/` here (gitignored) | `~/.hermes/work` symlinks into it — never publish or clean it as repo content. |

## Fixed rules

- Reviews run at most two rounds; each re-review covers only the new diff.
- Hermes keeps max permissions. Memory is append-only: add, never rewrite or delete.
- Never change a Hermes model, provider or fallback order without Yusuf's words.
- Never read `.env`, `~/.hermes/auth.json`, tokens, session files or real message bodies.
- Tests never send to contacts. An owner send goes only to Yusuf's own chat or topic.
- Restart the gateway in-band: `kill -USR1 "$(systemctl --user show -p MainPID --value hermes-gateway.service)"` — Hermes restarts when idle (`gateway/restart_when_idle.py`). `systemctl --user restart` SIGTERMs and kills running turns; use only when hung. SOUL/skill edits need no restart; MCP or `.env` changes do. Never run `hermes update` from an agent turn.
- Never run git on `/home/yqecea`.
- Compression stays at 0.5 of the window (H, Y, S); `lcm.model_thresholds`: Terra 0.5, Sol 0.3. Never lower Claude `autoCompactWindow` or Codex `model_auto_compact_token_limit` below 500000.
- Token spend is reported weighted (input×1 + cache_write×1.25 + cache_read×0.1 + output×5) alongside raw.
- No Claude model in a pstack role. Claude runs only as a coordinator session Yusuf opens himself.
- No "owner approved X" in a ledger unless the owner literally said X, quoted on the same line.
- The live setup changes only through this repo: edit `setup/home/**`, then `scripts/setup-apply.sh` (flock + backup + verify + rollback). Updates and gateway restarts go through `scripts/setup-lock.sh` / `setup-gateway-restart.sh`. Enforcement is `setup-sync.py drift` — not prose. Design: [docs/setup-as-code.md](docs/setup-as-code.md).

### Rule → enforcer

| Rule | Enforcer |
|---|---|
| pstack roles explicit, SWE-2 main workers, no Claude/default, Gemini only read-only explorer | `tests/harness-native/test_model_strategy.py` in `scripts/check-t3-harnesses.sh` |
| Compaction floors | `python3 ~/.hermes/scripts/compaction-floor.py.disabled-20261121` — parked by another actor 21.11; the t3-harnesses lane still calls the old name and stays red until it is restored or replaced |
| Custom code only shrinks | `scripts/custom-ratchet.py` in `check.sh`; growth only with `--allow-growth "<owner's verbatim words>"` |
| Live model slots follow the owner split | `python3 ~/.hermes/scripts/route-policy-check.py`, tested by `tests/outer-inner` |
| Usage counts dedup by API response id | `python3 scripts/harness-usage.py --selftest` |
| Paths cited in `ROLLBACK.md` exist | `scripts/check-rollback-links.py` |
| Permission claims carry a verbatim quote | permission-claim lint in `scripts/check-t3-harnesses.sh` |
| Doc links and generated map stay valid | `scripts/check-doc-paths.py` |

## Checks

```bash
scripts/check.sh                                  # every lane
python3 scripts/check-select.py --files <paths>   # only the lanes a change needs
```

Follow [.cursor/skills/verify-hermes-pro/SKILL.md](.cursor/skills/verify-hermes-pro/SKILL.md):
separate source checks from real H/S/Y acceptance. A synthetic pass never
closes a real-world request; the H↔T3 path is checked end to end through H
itself. CI `canonical-check` runs `scripts/check.sh` on the self-hosted
runner `actions-runner-hermes-pro.service`.

Known red on master (unrelated to your change): the compaction-floor
enforcer is parked under a `.disabled-20261121` name, and the cron-audit
script called by `check-t3-harnesses.sh` exists only as an uncommitted
file in the main checkout.

## How to work

- Report to Yusuf in plain Russian, result first; screenshots or a live artifact beat status files.
- On resume/provider switch/exhausted limits, read `CURRENT_WORK.md` first; update it after each verified unit.
- Fix the class: a type, test or check before prose.
- Before a change to live behavior, back the file up under `.artifacts/<task>/backups/`.
- A verified change is installed, not parked: back up, run the lanes `check-select.py` picks plus the change's own tests, install when no green lane turns red, check the live effect once, roll back if it regresses.
- Another actor may commit in this checkout: check `git status` and `gh pr list` before committing; commit only your own paths. Parallel lanes work in `~/.t3/worktrees/hermes-pro/*`.
- Write skills, AGENTS.md and CHARACTER yourself; delegate substantial independent code units to T3 workers.

## Memory rule (standing)

While `free -m` MemAvailable is below 3 GB: no second heavy job in parallel
(docker, cargo, browser runs, extra T3 workers). Yusuf's Helium and the
Pencil app are never touched.
