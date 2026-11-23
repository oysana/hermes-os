import Base
import registration.bend as r
import LAWS.bend as LAWS

def LAWS.explicit_registration_creates_goal(t):
  {==}

def LAWS.registration_replay_is_idempotent(row, t):
  match row:
    case r.Absent{}:
      {==}
    case r.FinishedRow{old, spent}:
      match old:
        case r.A{}:
          match t:
            case r.A{}: {==}
            case r.B{}: {==}
        case r.B{}:
          match t:
            case r.A{}: {==}
            case r.B{}: {==}
    case r.Active{old, spent}:
      {==}
    case r.Paused{old, spent}:
      {==}
    case r.Cleared{old, spent}:
      {==}

def LAWS.active_budget_and_objective_are_preserved(old, incoming, b):
  {==}

def LAWS.owner_pause_survives_registration(old, incoming, b):
  {==}

def LAWS.owner_clear_survives_registration(old, incoming, b):
  {==}

def LAWS.completed_replay_does_not_restart(t, b):
  match t:
    case r.A{}: {==}
    case r.B{}: {==}

def LAWS.pause_orders_without_a_stale_write(old, incoming, b):
  {==}

def LAWS.other_surfaces_never_activate(row, text):
  {==}

def LAWS.telegram_uses_native_registration(row, text):
  {==}

def main() -> Unit:
  Unit{}
