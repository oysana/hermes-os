import Base
import admission.bend as a

law busy_never_admitted:
  for +guards: Bool
  {a.admit(a.Busy{}, guards) == False{} : Bool}

law unknown_never_admitted:
  for +guards: Bool
  {a.admit(a.Unknown{}, guards) == False{} : Bool}

law idle_requires_other_guards:
  {a.admit(a.Idle{}, False{}) == False{} : Bool}

law verified_idle_admitted:
  {a.admit(a.Idle{}, True{}) == True{} : Bool}
