# Harness configuration exports

Reviewed copies of a few native harness files. Nothing loads this directory, and
editing it installs nothing. The live files sit in each tool's user config
directory, listed in `~/coding-projects/SETUP.md` under «Что установлено в харнессах».

- Global instructions: `~/.codex/AGENTS.md`, reached by symlinks from Claude,
  OpenCode, Antigravity and `~/.agents`, and by Devin through Claude's `CLAUDE.md`.
- T3 roles: `~/.config/t3-pstack/models.md` is the only sheet; `tests/harness-native`
  reads it directly. There is no copy here.
- `claude/`, `codex/`, `devin/`, `opencode/explore.md`: sources of the installed
  native helper agents (byte-identical to the live files on 16.11.2026).
- `t3-defaults.json`: T3 default model choices that `tests/harness-native` checks.

Changing a live file needs a backup, the targeted checks
(`bash scripts/check-t3-harnesses.sh`, no model calls) and a real start of that harness.
