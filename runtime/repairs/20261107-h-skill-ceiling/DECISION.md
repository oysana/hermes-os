# Decision: how the skill-ceiling hook gets enabled (t_a58edb96)

## Problem

The 07.11 run built the plugin and proved the veto, but its brief allowed only
`.hermes/plugins/`, `.hermes/scripts/`, `runtime/`, `tests/` — the enabling edit
in `~/.hermes/config.yaml` (`plugins.enabled`) was outside those paths, so
proof_check rejected the report and the card stayed blocked even though the
plugin itself was verified.

## Options

**A. Keep `plugins.enabled` in `config.yaml`; sanction the enable step.**
`config.yaml` is the canonical settings surface for every Hermes instance —
upstream `hermes-agent/AGENTS.md` fixes the rule: "`.env` is secrets only,
behaviour goes in `config.yaml`". Enablement already has an owning interface:
`hermes plugins enable <name>` writes exactly this key, with validation, and
`hermes plugins disable` / `plugins pack` speak the same contract.

**B. Move `plugins.enabled` out of `config.yaml`.** A second enablement source
(`plugins/enabled`, a directory, a marker file) means teaching the plugin
loader — which lives in `hermes-agent` core — to merge two sources of truth,
migrate the existing list, and keep `hermes plugins list|enable|disable`
consistent forever. The same brief that produced this plugin also fixed the
boundary "do not touch Hermes core". A second enable path would also let a
brief-scoped worker bypass the single place where the owner reviews what is
active — the owner-facing config is the audit point, not an obstacle.

## Chosen: A.

The defect was procedural, not architectural: the brief's path list forgot the
enable step. The fix that holds for every future plugin brief is to name the
operation, not to reroute the loader:

- enabling/disabling an installed plugin is done only through
  `hermes plugins enable|disable <name>` (the owning CLI), never by editing
  `plugins.enabled` by hand;
- a brief that ships a plugin must list this step in its scope explicitly —
  this brief does, which is why the enable here is sanctioned;
- `plugins.enabled` stays the single owner-reviewed list of what loads.

No code change follows from this decision — the mechanism already exists and
the running gateway had loaded the hook after the enable and restart.

## Current verified state

- `hermes config get plugins.enabled` lists `skill-ceiling` (since 07.11).
- `hermes plugins list` reports `skill-ceiling enabled 1.0.0`;
  `hermes plugins doctor skill-ceiling` → 1 hook registered.
- `~/.hermes/logs/skill-ceiling.jsonl` shows live `blocked` events on real
  skills after gateway start 08.11 18:15 +05: `oyvoice-project` at 19:14 and
  19:42, `autonomous-ai-agents/hermes-agent` at 09:19 — the hook fires under
  the running service, not only under tests.
