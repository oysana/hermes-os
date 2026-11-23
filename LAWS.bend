# Laws for the skill-ceiling gate (t_a58edb96). Open claims; proofs in
# PROOF.bend. Do not edit claims without an owner-visible reason.

import Base
import skill_ceiling_gate.bend as skill_ceiling_gate

# The implementation is exactly the spec: refuse iff over cap and growing.
law veto_exact:
  for +prior: Nat
  for +new: Nat
  for +cap: Nat
  {skill_ceiling_gate.veto(prior, new, cap) ==
   Bool.and(Nat.is_gt(new, cap), Nat.is_gt(new, prior)) : Bool}

# Every violating write (new > 25000 and new > prior) is denied.
law overgrowth_denied:
  for +prior: Nat
  for +new: Nat
  {Nat.is_gt(new, 25000n) == True{} : Bool} ->
  {Nat.is_gt(new, prior) == True{} : Bool} ->
  {skill_ceiling_gate.veto(prior, new, 25000n) == True{} : Bool}

# Concrete violation from the live probe is denied.
law probe_case_denied:
  {skill_ceiling_gate.veto(30000n, 30490n, 25000n) == True{} : Bool}

# A shrinking write is admitted.
law shrink_admitted:
  {skill_ceiling_gate.veto(30000n, 24000n, 25000n) == False{} : Bool}

# A write that stays under the cap is admitted.
law under_cap_admitted:
  {skill_ceiling_gate.veto(20000n, 24999n, 25000n) == False{} : Bool}
