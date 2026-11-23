# Current work admission

A heartbeat and a control answer are different snapshots. A file count of1
cannot establish current work after the same live gateway reports0 and an
empty work list. The native active-work verb collects both on the gateway loop,
including delegations omitted by the heartbeat's total.

`scripts/admission.py` uses the current control answer for its work predicate.
The answer must have the expected PID, a strict integer zero count and an empty
list. Missing, malformed, mismatched or busy answers refuse admission. The
heartbeat must still be fresh, live, running, free of a restart request and have
a valid nonnegative integer count. Profile readiness and cron checks remain.
Both counts remain visible in the report. This is a snapshot, not a reservation.

The changed fixture fails on the old source and passes on the repaired source.
Other fixtures cover current work despite an idle heartbeat, malformed counts,
unresponsive control, PID mismatch, delegations, active cron and unready ingress.
The test selects its own checkout's source, avoiding a false pass from another
worktree.

Run `bend laws/PROOF.bend --verdict` from this directory. The four kernel-checked
laws cover healthy/current-idle admission and refusal for invalid, unhealthy or
busy snapshots. External PID validation, filesystem metadata and cron policy
are tested through the native interfaces, not proved by this small model.
