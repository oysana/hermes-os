import Base
import admission.bend as a

law live_work_refused:
  for +healthy: Bool
  {a.admit(healthy, a.VerifiedBusy{}) == False{} : Bool}

law unavailable_refused:
  for +healthy: Bool
  {a.admit(healthy, a.Invalid{}) == False{} : Bool}

law unhealthy_refused:
  for +snapshot: a.Current
  {a.admit(False{}, snapshot) == False{} : Bool}

law current_idle_admitted:
  {a.admit(True{}, a.VerifiedIdle{}) == True{} : Bool}
