# Pure model of the installed skill-ceiling veto
# (~/.hermes/plugins/skill-ceiling/__init__.py).
# A skill_manage write is refused iff the resulting SKILL.md would be longer
# than CAP chars AND longer than before. Shrinking and equal writes pass;
# supporting files are outside this predicate by design.

import Base

def SKILL_CAP() -> Nat:
  25000n

def veto(+prior: Nat, +new: Nat, +cap: Nat) -> Bool:
  Bool.and(Nat.is_gt(new, cap), Nat.is_gt(new, prior))

def main() -> Bool:
  veto(30000n, 30490n, SKILL_CAP())
