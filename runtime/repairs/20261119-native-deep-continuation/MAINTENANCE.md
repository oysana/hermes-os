# Telegram activation boundary

The three native files are shared by the gateway process. Only the new `todo_list.goal`
argument is Telegram-only. Existing `/goal`, ordinary todo lists, profile roles, routes
and conversation data retain their native behavior. Telegram personal-dispatcher requests
that actually execute as LOCAL are not relabelled as Telegram.

Writing these files does not reload already imported core modules. No per-profile or
Telegram-only hot reload of this change is verified. A shared maintenance slot is needed.
There is no reason to force-stop active work to obtain it.

## Admission before H activates

1. Run the project gate and packet checks on the final revision. Require real rc0.
2. Obtain fresh rc0 from `python3 -B scripts/run-selected.py scripts/admission.py`.
   The 19:55 snapshot refused: PID 3487975 had two chats and two delegations; Y's
   `personal_replies` platform was `fatal`. This document does not clear that failure.
3. Use the installed native graceful maintenance path. `pause_gateway_for_update` uses
   `request_service_restart` and waits for foreground and background work. It does not
   force-stop at its wait budget. A request ACK alone does not prove the process stopped.
   Confirm the native pause completed and its supervisor is held before replacing files.
   The native updater can continue after an unsuccessful pause; this packet must not.
   No fallback SIGTERM, force flag or bare `systemctl restart` while work is active.
4. Compare all three current source hashes to `manifest.tsv`. Refuse source drift.
   Save exact copies and modes under a private 0700 backup directory. Keep configuration,
   databases, profiles, memory and credentials outside this file-only operation.
5. Publish all three qualified files while the service is held. Check all after-hashes.
   On any partial failure restore all three backups and verify bytes and modes before resume.
6. Resume through the native maintenance owner. Confirm the new PID, readiness and updated
   owner-session tool schema. Test Telegram only in an authorized own chat, without paid probes.

These are conditions for H's switch, not commands executed against the live system.
The isolated tests exercise native idle deferral and file rollback. A real supervisor
pause/resume, startup against live profiles and Telegram model acceptance remain untested.

## Remaining continuation limitation

The goal row is durable. Telegram's queued continuation is in memory. A process failure
can lose that continuation. This packet changes neither that queue nor its recovery.
`stop_when` remains judge-interpreted contract text, not an output filter.
