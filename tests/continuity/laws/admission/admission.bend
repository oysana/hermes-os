import Base

type Work is Data:
  Idle{}
  Busy{}
  Unknown{}

def admit(+work: Work, +other_guards: Bool) -> Bool:
  match work:
    case Idle{}:
      other_guards
    case Busy{}:
      False{}
    case Unknown{}:
      False{}
