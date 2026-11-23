# Laws for the browser-access ensure decision (browser-access.py helper).
# Open claims; proofs in PROOF.bend. Do not edit claims without an
# owner-visible reason.

import Base
import browser_access.bend as b

# An endpoint that already answers is an active browser: never launch.
law listening_never_launches:
  for +ensure: Bool
  for +loopback: Bool
  for +entry: Bool
  {b.may_launch(True{}, ensure, loopback, entry) == False{} : Bool}

# Without --ensure the helper is read-only: never launch.
law no_ensure_never_launches:
  for +listening: Bool
  for +loopback: Bool
  for +entry: Bool
  {b.may_launch(listening, False{}, loopback, entry) == False{} : Bool}

# --ensure only covers the owner loopback endpoint: never launch for a
# remote CDP target.
law non_loopback_never_launches:
  for +listening: Bool
  for +ensure: Bool
  for +entry: Bool
  {b.may_launch(listening, ensure, False{}, entry) == False{} : Bool}

# The one admitted case: down + --ensure + loopback + matching entry.
law down_ensure_entry_launches:
  {b.may_launch(False{}, True{}, True{}, True{}) == True{} : Bool}

# Down but no matching .desktop entry: refused, never a fresh profile.
law down_no_entry_refused:
  {b.may_launch(False{}, True{}, True{}, False{}) == False{} : Bool}
