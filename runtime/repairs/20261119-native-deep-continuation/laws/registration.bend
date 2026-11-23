import Base

type Text is Data:
  A{}
  B{}

type Budget is Data:
  Fresh{}
  Spent{}

type Row is Data:
  Absent{}
  Active{text: Text, spent: Budget}
  Paused{text: Text, spent: Budget}
  Cleared{text: Text, spent: Budget}
  FinishedRow{text: Text, spent: Budget}

def same_text(a: Text, b: Text) -> Bool:
  match a:
    case A{}:
      match b:
        case A{}: True{}
        case B{}: False{}
    case B{}:
      match b:
        case A{}: False{}
        case B{}: True{}

def register_finished(equal: Bool, old: Text, text: Text, spent: Budget) -> Row:
  match equal:
    case True{}: FinishedRow{old, spent}
    case False{}: Active{text, Fresh{}}

def register(row: Row, +text: Text) -> Row:
  match row:
    case Absent{}:
      Active{text, Fresh{}}
    case FinishedRow{+old, spent}:
      register_finished(same_text(old, text), old, text, spent)
    case Active{old, spent}:
      Active{old, spent}
    case Paused{old, spent}:
      Paused{old, spent}
    case Cleared{old, spent}:
      Cleared{old, spent}

def pause(row: Row) -> Row:
  match row:
    case Active{text, spent}:
      Paused{text, spent}
    case _:
      row

type Surface is Data:
  Telegram{}
  OtherSurface{}

def register_on(surface: Surface, row: Row, text: Text) -> Row:
  match surface:
    case Telegram{}:
      register(row, text)
    case OtherSurface{}:
      row
