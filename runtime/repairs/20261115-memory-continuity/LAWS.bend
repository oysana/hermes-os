import Base

type Surface is Data:
  OwnerDirect{}
  OperatorConsole{}
  Background{}
  Shared{}
  Direct{}
  Channel{}
  Unknown{}

def owner_file_write(s: Surface) -> Bool:
  match s:
    case OwnerDirect{}:
      True{}
    case OperatorConsole{}:
      True{}
    case _:
      False{}

law non_owner_is_confined:
  {Bool.or(owner_file_write(Background{}),
    Bool.or(owner_file_write(Shared{}),
      Bool.or(owner_file_write(Direct{}),
        Bool.or(owner_file_write(Channel{}), owner_file_write(Unknown{}))))) == False{} : Bool}

def non_owner_is_confined():
  {==}

law owner_learning_stays_available:
  {Bool.and(owner_file_write(OwnerDirect{}), owner_file_write(OperatorConsole{})) == True{} : Bool}

def owner_learning_stays_available():
  {==}
