# Proofs for LAWS.bend (skill-ceiling gate). Gate:
#   ~/.bend/bin/bend PROOF.bend            -> ALL PROOFS CHECK
#   ~/.bend/bin/bend PROOF.bend --verdict  -> kernel recheck

import Base
import LAWS.bend as LAWS
import skill_ceiling_gate.bend as skill_ceiling_gate

def LAWS.veto_exact(prior, new, cap):
  {==}

def LAWS.overgrowth_denied(prior, new, over_cap, growing):
  %Equal.sym(Bool, Nat.is_gt(new, 25000n), True{}, over_cap) : {Bool.and(_, Nat.is_gt(new, prior)) == True{} : Bool}
  %Equal.sym(Bool, Nat.is_gt(new, prior), True{}, growing) : {Bool.and(True{}, _) == True{} : Bool}
  {==}

def LAWS.probe_case_denied():
  {==}

def LAWS.shrink_admitted():
  {==}

def LAWS.under_cap_admitted():
  {==}

def main() -> Unit:
  Unit{}
