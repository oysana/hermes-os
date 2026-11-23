# Native goal registration — source candidate for request 6524

Base implementation: `f6f1872799bf3267d63b8e36767fba8cc351583a`.
This packet adds an explicit `goal` argument to the existing native `todo_list` tool.
It does not infer a goal from the owner's wording, install code, or change profile settings.
The 19:44 Telegram follow-up limits the new argument to trusted `agent.platform == "telegram"`.
CLI, LOCAL, WhatsApp and other surfaces cannot activate this new argument. Native `/goal` remains unchanged.

## Demonstrated gap

An ordinary todo plan does not activate `GoalManager`. With no active `goal:<session_id>`
row, the gateway's native post-turn hook returns without scheduling continuation.
Todo plans can be restored from the transcript; this change is not a plan-persistence fix.

The reported Y turn lasted 150.009 seconds and ended with `finish_reason=stop`.
There were three write-file calls and no registered goal in the profile stores.
That metadata does not establish whether its answer or artifacts satisfied the request.

## Source changes

- `hermes_cli/goals.py`: `register_session_goal` uses the existing SessionDB write transaction.
  Registration reads the current row and commits atomically. Exact replay preserves the contract,
  progress and turn count. Paused and cleared rows are refused. Replaying a completed objective
  leaves it completed. A different active objective cannot be overwritten. Persistence refusal
  returns `ok: false`. The existing configured turn budget remains the native default.
- `tools/todo_tool.py`: optional `goal{objective,contract}` with the native contract fields
  `outcome`, `verification`, `constraints`, `boundaries`, `stop_when`. Plan validation runs
  before registration. Ordinary calls retain their existing result shape.
- `agent/inline_tool_executors.py`: forwards the new argument, the executing agent's session ID and its trusted platform. A model-supplied platform field is ignored.

No new tool, scheduler, worker, profile overlay, model choice, effort setting or budget is added.
The test overlay contains exactly these three files; the remaining gateway code is installed native code.

## Evidence and boundaries

`test_lifecycle.py` covers owner-pause interleaving, terminal replay, immutable contracts,
invalid plans and a real SQLite write refusal. `test_turn_handoff.py` runs the native gateway
post-turn hook, FIFO enqueue and adapter drain against temporary profile stores. It executes
phase B only in the automatically spawned second handler. The messenger send and model judge
are local fixtures. The judge returns blocked after executable artifact checks pass.
This verifies continuation mechanics, not a real model's decision to use the new argument.

The fixture clears inherited `HERMES_DELEGATED_CHILD_CONTEXT`. A separate negative test sets
that real environment marker and verifies that a child cannot register a goal.
The Bend model covers registration order and replay. It does not prove model quality or transport delivery.

Telegram continuation still uses an in-memory FIFO. A process failure can lose that queued input
while the goal row survives. This packet does not repair that crash window. Native LOCAL sessions
use durable admission, but this packet's gateway drain test exercises the offline Telegram adapter.
No exactly-once delivery guarantee across process failures is claimed.
`stop_when` is contract text interpreted by the native judge; it is not a deterministic output filter.
The synthetic stop-before-questions result therefore depends on the controlled judge verdict.

## Application and rollback

Nothing has been installed. `manifest.tsv` pins all three installed before-hashes and candidate after-hashes.
Forward and reverse patch checks must pass at fuzz zero against copied files before any future install.
Preserve private backups of those exact native files and restore them for rollback.

There is no verified hot-reload mechanism for replacing these already-imported core modules in the
running shared gateway. Writing the files alone does not activate this API in an existing process.
Hold this packet for an authorized normal gateway maintenance window. Do not restart the busy gateway,
reload modules by hand, or claim a per-session reset activates the patch.
No live H/Y/S acceptance, provider call or recipient delivery was performed.

## Earlier qualification of 5ef550b3 on 19 November

- Candidate suite with inherited child marker: 30 passed, rc 0.
- Original f6 candidate counter-test: 5 failed, 1 passed, rc 1. The failures cover
  terminal replay, contract replacement, pause interleaving and invalid-plan activation.
- Packet forward/reverse check: 3 targets, fuzz zero, rc 0. Installed hashes unchanged.
- Bend 2.0.40: all seven registration laws check, rc 0.
- `HERMES_DISABLE_LAZY_INSTALLS=1 scripts/check.sh`: rc 1. Only `custom-ratchet` fails
  because counted installed custom code is 3402 lines against a 3397-line baseline.
  This source-only packet changes none of its counted roots. No baseline override used.

Full commands, logs and the final receipt are in
`.artifacts/verification/native-deep-continuation-20261119/`. Earlier worker evidence
in this packet describes the original f6 revision, not the corrected candidate.

## Telegram admission follow-up

The extra five counted lines came from the published LCM-X companion, not this packet.
Its installer-owned metadata pins `electricsheephq/lcm-x` revision
`6260c31fbce11e4cd542878f1f6a37410d678ae8`, subdirectory
`companions/hermes-lcm-x-local-embeddings`. All three installed files match GitHub's
pinned contents byte for byte. The local installation is recorded in CURRENT_WORK.md at 09:40.
No separate active T3 implementation owner was present at the follow-up inspection.

`custom-ratchet.py` now classifies only this exact recorded revision and exact three-file
content set as maintained upstream. A local edit, extra file, missing file, different origin,
different revision or absent pin restores ordinary counting. The baseline is unchanged.
The host post-audit lane verifies these counter-examples. No plugin or live setting changed.

The Telegram candidate passes 39 offline tests and nine Bend laws. Native restart-when-idle
was exercised with an isolated runner: the existing turn and delegation settle before
`stop` is called, admission stays open meanwhile, and repeated requests use one task.
Private backup, atomic file publication and rollback after partial publication restore
all three copied targets byte-for-byte with their original modes. This is a rehearsal,
not a real service shutdown or Telegram delivery proof.

See MAINTENANCE.md for the operator boundary. Follow-up logs are under
`.artifacts/verification/native-deep-continuation-20261119/telegram-admission/`.

Final follow-up project qualification: `HERMES_DISABLE_LAZY_INSTALLS=1 scripts/check.sh`
returned rc0 in 65 seconds, with all 31 start/ok pairs and `all checks passed`.
The ratchet baseline remained byte-identical to 5ef550b3. This supersedes the earlier
red source qualification, not the separate live-readiness refusal.
