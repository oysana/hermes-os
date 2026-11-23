export const title = "T3 return: card waits, sweep receipts, origin must wake";

export function state(v) {
  if (v.cardStatus === "done") return "Card closed — origin was told";
  if (v.originAwake) return v.ingestedOrReason
    ? "Origin ingested — closing follows"
    : "Origin awake, ingestion pending";
  if (v.receipt === "queued" && v.wakeEvent === "sent")
    return "Receipt parked, wake on the way";
  if (v.receipt === "queued") return "Receipt parked — nobody is woken";
  if (v.runState === "terminal") return "Run finished, sweep not yet seen";
  return "Run in flight";
}

export function step(action, from, to) {
  switch (action) {
    case "RunTerminates": return "the T3 run ends";
    case "SweepQueueReceipt": return "sweep writes the return receipt";
    case "SweepEmitWakeOnPark": return "sweep posts a wake event for the parked card";
    case "SweepClose": return "sweep closes the card [with ingestion or a reason]";
    case "NotifierWakes": return "the notifier wakes the origin session";
    case "OriginIngests": return "the origin records ingestion or a reason";
    case "Done": return null;
    default: return action;
  }
}

export function group(v) {
  if (v.cardStatus === "done") return "settled";
  if (v.receipt === "queued" && v.wakeEvent === "none" && !v.originAwake) return "stuck";
  if (v.originAwake) return "returning";
  return "waiting";
}

export const colors = {
  waiting: "blue",
  stuck: "orange",
  returning: "teal",
  settled: "pink",
};
