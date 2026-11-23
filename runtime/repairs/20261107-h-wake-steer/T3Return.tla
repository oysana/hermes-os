------------------------------ MODULE T3Return ------------------------------
\* T3 return path: card, sweep, notifier, origin session.
\* WAKE_ON_PARK models the fix: TRUE = sweep emits a wake-able event when it
\* parks a receipted card; FALSE = today's behaviour (wake only on close).

CONSTANT WAKE_ON_PARK

VARIABLES
  runState,        \* "running" | "terminal" — the bound T3 run
  cardStatus,      \* "blocked" | "done"
  receipt,         \* "none" | "queued"     — T3-Return receipt comment exists
  wakeEvent,       \* "none" | "sent"       — a claimable, wake-kind board event
  ingestedOrReason,\* guard input: ingested evidence or an explicit reason
  originAwake      \* origin session has taken a turn on the return

vars == <<runState, cardStatus, receipt, wakeEvent, ingestedOrReason, originAwake>>

Init ==
  /\ runState = "running"
  /\ cardStatus = "blocked"
  /\ receipt = "none"
  /\ wakeEvent = "none"
  /\ ingestedOrReason = FALSE
  /\ originAwake = FALSE

\* The bound run reaches a terminal state.
RunTerminates ==
  /\ runState = "running"
  /\ runState' = "terminal"
  /\ UNCHANGED <<cardStatus, receipt, wakeEvent, ingestedOrReason, originAwake>>

\* Sweep observes the terminal run and queues the idempotent receipt comment.
\* The 'commented' event it writes is never claimable, so it does not wake.
\* The fix (WAKE_ON_PARK): the same transaction ALSO emits one wake-able board
\* event when the card cannot close yet.
SweepQueueReceipt ==
  /\ runState = "terminal"
  /\ receipt = "none"
  /\ receipt' = "queued"
  /\ wakeEvent' = IF WAKE_ON_PARK /\ ~ingestedOrReason THEN "sent" ELSE wakeEvent
  /\ UNCHANGED <<runState, cardStatus, ingestedOrReason, originAwake>>

\* Sweep closes the card only when the guard passes; the 'completed' event is
\* itself wake-able.
SweepClose ==
  /\ receipt = "queued"
  /\ cardStatus = "blocked"
  /\ ingestedOrReason
  /\ cardStatus' = "done"
  /\ wakeEvent' = "sent"
  /\ UNCHANGED <<runState, receipt, ingestedOrReason, originAwake>>

\* The gateway notifier claims a wake-kind event and wakes the origin session.
NotifierWakes ==
  /\ wakeEvent = "sent"
  /\ ~originAwake
  /\ originAwake' = TRUE
  /\ UNCHANGED <<runState, cardStatus, receipt, wakeEvent, ingestedOrReason>>

\* The woken origin reads the run and records ingestion (or an explicit reason).
OriginIngests ==
  /\ originAwake
  /\ ~ingestedOrReason
  /\ ingestedOrReason' = TRUE
  /\ UNCHANGED <<runState, cardStatus, receipt, wakeEvent, originAwake>>

\* Terminal end state so NoDeadlock names progress, not a correct stop.
Done ==
  /\ cardStatus = "done"
  /\ UNCHANGED vars

Next ==
  \/ RunTerminates
  \/ SweepQueueReceipt
  \/ SweepClose
  \/ NotifierWakes
  \/ OriginIngests
  \/ Done

Spec == Init /\ [][Next]_vars

\* Owner rule (must hold in both versions): the card never closes without
\* ingested evidence or an explicit reason.
CloseNeedsIngestedOrReason == cardStatus = "done" => ingestedOrReason

\* The reported bug: a receipted card that cannot close still gets a wake on
\* the way. Without the fix TLC shows the parking path that violates this.
ParkedReceiptGetsWake ==
  receipt = "queued" /\ cardStatus = "blocked" /\ ~ingestedOrReason => wakeEvent = "sent"

=============================================================================
