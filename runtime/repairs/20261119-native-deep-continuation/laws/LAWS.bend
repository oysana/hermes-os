import Base
import registration.bend as r

law explicit_registration_creates_goal:
  for +t: r.Text
  {r.register(r.Absent{}, t) == r.Active{t, r.Fresh{}} : r.Row}

law registration_replay_is_idempotent:
  for +row: r.Row
  for +t: r.Text
  {r.register(r.register(row, t), t) == r.register(row, t) : r.Row}

law active_budget_and_objective_are_preserved:
  for +old: r.Text
  for +incoming: r.Text
  for +b: r.Budget
  {r.register(r.Active{old, b}, incoming) == r.Active{old, b} : r.Row}

law owner_pause_survives_registration:
  for +old: r.Text
  for +incoming: r.Text
  for +b: r.Budget
  {r.register(r.Paused{old, b}, incoming) == r.Paused{old, b} : r.Row}

law owner_clear_survives_registration:
  for +old: r.Text
  for +incoming: r.Text
  for +b: r.Budget
  {r.register(r.Cleared{old, b}, incoming) == r.Cleared{old, b} : r.Row}

law completed_replay_does_not_restart:
  for +t: r.Text
  for +b: r.Budget
  {r.register(r.FinishedRow{t, b}, t) == r.FinishedRow{t, b} : r.Row}

law pause_orders_without_a_stale_write:
  for +old: r.Text
  for +incoming: r.Text
  for +b: r.Budget
  {r.register(r.pause(r.Active{old, b}), incoming) == r.pause(r.register(r.Active{old, b}, incoming)) : r.Row}

law other_surfaces_never_activate:
  for +row: r.Row
  for +text: r.Text
  {r.register_on(r.OtherSurface{}, row, text) == row : r.Row}

law telegram_uses_native_registration:
  for +row: r.Row
  for +text: r.Text
  {r.register_on(r.Telegram{}, row, text) == r.register(row, text) : r.Row}
