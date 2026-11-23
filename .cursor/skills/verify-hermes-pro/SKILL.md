---
name: verify-hermes-pro
description: Verify Hermes Pro changes through existing native recall, turn-hook, parser and acceptance interfaces. Separate source checks, running-service readiness and real H/S/Y delivery proof.
---

# Verify Hermes Pro

This checkout coordinates the installed H/S/Y runtime. Read `AGENTS.md`, `CURRENT_WORK.md`, and [the feature map](features/README.md). Preserve profiles, models, providers, histories and permissions. Project edits do not install runtime changes.

## Launch

Confirm `realpath .` and Git top-level are `/home/yqecea/coding-projects/hermes-pro`. Read the current status and source map. The ordinary gateway is already an owner-managed instance. Source qualification starts short-lived commands whose tests create their own disposable stores; it needs no second gateway, profile, credential copy or model process.

Create one evidence directory from the repository root:

```bash
mkdir -p .artifacts/verification
verify_run=$(mktemp -d "$PWD/.artifacts/verification/run-XXXXXX")
printf '%s
' "$verify_run"
git rev-parse HEAD > "$verify_run/checkout.txt"
```

Keep the returned absolute path for all commands in this run. Inspect current test sources and selected runtime paths before execution. Staged source selectors must be explicit; label their results staged.

## Doctor

Read `CURRENT_WORK.md` for the current admission contract. The approved metadata projection is:

```bash
python3 -B scripts/run-selected.py scripts/admission.py > "$verify_run/admission.json"
```

Require its actual exit code 0 and `idle_admitted: true` before live installation, reload or model acceptance. Inspect its source and current approved pin before invoking it. It selects counts, readiness and job metadata, without returning session/job bodies. It requires a successful live `active-work` control answer from the current PID, including async delegations. Recent session activity is diagnostic. It is a snapshot, not a reservation. A refusal blocks dependent live work; independent isolated source checks can continue.

Do not substitute `hermes status --full`, raw cron lists or personal-replies status. Their readers can expose private data or change schema. Read no credentials, session files or real message bodies. Existing native listing children may load opaque credentials internally; a read-only mount does not prevent those reads.

## Drive

Choose one feature and follow its exact recipe. Existing public interfaces are the harness; add no parallel test runner.

- [Recall and tools](features/recall-and-tools.md) drives native search fixtures and H/S/Y platform listings.
- [Personal addressing](features/personal-addressing.md) drives both installed lexical interfaces and separates service readiness from message delivery.

For a complete installation qualification, run once after the accepted unit is frozen:

```bash
bash scripts/check.sh > "$verify_run/check.log" 2>&1
```

Require actual process rc0, every configured step's start and matching `ok` marker, no failed step and final `all checks passed`. Read the whole relevant output. A tail or stdout marker alone is insufficient. Compare hashes of the selected source/test inputs before and after. Preserve the original failure if a check goes red.

A single-UID user namespace is not equivalent to the host for the whole gate. An unmapped foreign-owner fixture can fail `chown` with `EINVAL`, while the normal host returns `EPERM`; changing the visible UID does not repair that mapping. Use the supported source selector rather than changing or skipping the ownership test. Keep namespace qualification for native suites that explicitly define that execution scope. A source-selection view is not a filesystem security sandbox.

The separate native acceptance runner and judge live under `.artifacts/hermes-bridge-20261011/live-repair/proactivity-completion-20261012/prd-wave1/acceptance` (`python3 run_suite.py --only <IDs>`). Inspect the selected scenario, capture scope and fresh admission before a model run. Capture is not a filesystem sandbox. Use only existing profiles and authorized routes. Tests send no messages to contacts; any authorized owner delivery goes only to Yusuf. Source PASS cannot close a real-world REQUESTS item.

## His live sessions, not a fresh one

A fresh `hermes chat --oneshot` or a new test topic builds today's prompt, tools and model, so it proves the source, never what Yusuf gets. His Telegram topics are long-lived sessions that keep their stored prompt, tools[] pin (including the delegate_task variant) and any persisted `/model` override from the day they started (18.11: «Главное» ran a 14.11 prompt, a 6.1 model override and the old joined delegate_task while every CLI probe passed). A change to H/S/Y prompt, skills, tools, delegation, model or config is accepted only when his live owner sessions carry it: `hermes hermes-pro drift` reports no drift for them (until it is installed: compare the session's pin and model in state.db read-only, and heal with `hermes sessions refresh-prompts`, `/model reset`, or `.artifacts/handover-h-20261117/native-audit/unpin-joined-delegate.py` inside the maintenance slot). Report "source PASS, his sessions not yet updated" as such.

## Evidence

Keep command, actual exit code, complete sanitized log, selected source paths/hashes, profile/platform and expected/observed result together under `verify_run`. For service claims capture unit PID/start time and fresh readiness. For natural delivery retain the matching accepted send/delivery receipt; a local hash is insufficient. Keep real message bodies and credentials out of evidence.

There is no running Hermes Pro GUI in this checkout. Browser viewport/theme claims belong to the actual visual artifact's finish receipt, not these CLI tests. Keep desktop/phone, light/dark and host/standalone variants separate when verifying visual output.

## Cleanup

Wait for each owned short-lived command to finish. The fixture suites close and remove their owned synthetic stores. Stop only a process this run started, using its exact PID; never stop the owner's services as cleanup. Remove only positively identified disposable scratch data after checking recovery value. Keep this run's evidence, original failures and required private backups. Confirm the retained logs and receipts still exist after cleanup. Report source, installed-code, service, model and delivery verdicts separately.
