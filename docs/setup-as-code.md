# Setup as code

Owner directive (23.11.2026): «больше никогда чтобы не было проблем с конфликтом
сетапа». Today several threads may edit the same live files (`config.yaml`,
`cron/jobs.json`, units) and any of them can restart the gateway mid-turn.
There is no single source of truth and no queue.

This mechanism makes the repo the source of truth for the **managed surface**
with three deterministic primitives — no prose discipline:

- **Queue**: one `flock` at `$HOME/.setup-sync/lock` serializes every mutation
  (apply, pull, update, restart). Whoever doesn't hold the lock waits.
- **Apply**: `scripts/setup-apply.sh` backs up, installs, verifies and rolls
  back on failure — the only way repo changes reach the live setup.
- **Drift**: `python3 scripts/setup-sync.py drift` (cheap, no model) names any
  live file edited out-of-band. Owner is notified only on real drift
  (a cron/timer is wired at cutover).

## Commands

| Command | Does |
|---|---|
| `scripts/setup-apply.sh [--prune]` | lock → backup → install `setup/home/` → verify → auto-rollback on error |
| `scripts/setup-pull.sh` | lock → copy live into `setup/home/` (json-state: strips volatile fields) → commit to bless a legitimate live change |
| `python3 scripts/setup-sync.py drift` | names out-of-band edits: CHANGED / MISSING / EXTRA-LIVE; exit 1 on any |
| `python3 scripts/setup-sync.py verify` | parses every managed yaml/json/toml; exit 1 on failure |
| `scripts/setup-lock.sh <cmd>` | runs a foreign command under the same lock — updates and the auto-update thread must wrap in this |
| `scripts/setup-gateway-restart.sh` | in-band `kill -USR1` under the lock; the only restart path |

Env: `SETUP_ROOT` (default `$HOME`) selects the live root — isolated tests use
a scratch dir; `SETUP_REPO` (default: this repo) selects the repo copy.

## Manifest

`setup/manifest.tsv` — `kind<TAB>home_rel<TAB>flags`. Repo path =
`setup/home/<home_rel>`.

- `file` — repo content wins on apply.
- `dir` — repo files copied in; live extras kept but flagged EXTRA-LIVE
  (deliberate removal is `setup-apply.sh --prune`). `__pycache__` excluded.
- `json-state volatile=cron` — for `cron/jobs.json`: config fields come from
  the repo, runtime fields (`last_run_at`, `next_run_at`, `fire_claim`, …) are
  carried over from live by job id, live-only jobs are kept and flagged.
  Apply never silently deletes a cron job; `drift` ignores scheduler writes.

`systemctl-reload` flag = a reminder to run `systemctl --user daemon-reload`
after apply; restarts go through `setup-gateway-restart.sh`.

## Semantics

- Hermes writes configs atomically (`atomic_config_write`) — managed files are
  **copied, never symlinked**: a symlink would be replaced by a real file on
  the first internal write.
- Repo wins on apply; live-only extras are kept and reported (no silent data
  loss) until `--prune` or `pull`+commit resolves them.
- "No half-off states": removing a component = deleting its manifest rows and
  `setup/home/` files in one commit; `--prune` deletes live files whose rows
  vanished since the last apply (tracked in `.setup-sync/state.json`).
- Not managed: secrets (`.env`, `auth.json`), runtime state (`state.db`,
  sessions, memories, logs, `work/`, `.artifacts`), generated files
  (`SOUL.profile.md`), plugin install trees, skills (H curates them itself),
  non-hermes systemd units, `.claude.json` (session state), the `omh` plugin
  (owned by `hermes-omh-update`).

## Cutover (only when H says «можно»)

1. `scripts/setup-pull.sh` — seed `setup/home/` from live; commit.
2. Add the line «live setup changes only via `hermes-pro` apply» to
   `~/.hermes/skills/t3-work-bridge/SKILL.md` (live file — edit at cutover).
3. Wrap `hermes-omh-update.service` ExecStart in `scripts/setup-lock.sh`.
4. Add an H cron job `setup-drift-check` (interval): on `drift` exit 1 post the
   diff summary to the owner topic; exit 0 = silent.
5. From then on: change = PR to `setup/home/**` + `setup-apply.sh`;
   Hermes-internal writes (update migration, cron tool) show as drift →
   `setup-pull.sh` + commit.

## Proof

`bash tests/setup-as-code/run.sh` runs the whole mechanism against an isolated
`$HOME` under `~/.hermes/cache/scratch` — concurrent applies serialize on the
lock (journal), out-of-band edits are caught by drift, broken repo config is
refused before touching live, mid-apply failure rolls back, and json-state
merge preserves scheduler runtime fields. 7 assertions, wired as the
`setup-as-code` lane of `scripts/check.sh`.
