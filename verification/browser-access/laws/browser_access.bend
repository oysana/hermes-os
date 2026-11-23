import Base

# Spec twin of scripts/browser-access.py::may_launch — the only
# safety-critical decision in the helper: the owner Chrome desktop entry may
# be launched iff the configured loopback CDP endpoint is down AND --ensure
# was requested AND a matching .desktop entry exists. Anything already
# listening means an active browser; it is never restarted or duplicated.
def may_launch(listening: Bool, ensure: Bool, loopback: Bool, entry: Bool) -> Bool:
  match listening:
    case True{}:
      False{}
    case False{}:
      match ensure:
        case False{}:
          False{}
        case True{}:
          match loopback:
            case False{}:
              False{}
            case True{}:
              entry
