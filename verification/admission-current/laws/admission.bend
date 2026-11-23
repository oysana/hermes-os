import Base

type Current is Data:
  VerifiedIdle{}
  VerifiedBusy{}
  Invalid{}

def admit(healthy: Bool, snapshot: Current) -> Bool:
  match healthy:
    case False{}:
      False{}
    case True{}:
      match snapshot:
        case VerifiedIdle{}:
          True{}
        case VerifiedBusy{}:
          False{}
        case Invalid{}:
          False{}
