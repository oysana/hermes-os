# Recall and everyday tools

H/S/Y need visible recall, planning and scheduling tools. Search must find relevant earlier versions even when cron history is large.

## Sub-features

- `recall-engine` preserves matching earlier revisions, archived history, source policy and fallback search.
- `tools-visible` checks native toolset rows and deferral policy for H/S/Y × CLI/Telegram.
- `recall-owner` confirms that Y finds the owner's actual earlier versions in Telegram.

## How to get to it (user POV)

Ask H/S/Y to recall earlier work in CLI or Telegram. The native tool catalog is also available through Hermes tools-list handling. Owner acceptance of private recall is separate from synthetic search fixtures.

## Driving it with native CLI contracts

Preconditions: the installed native activation and bubblewrap are available; the selected interpreter is current. Use the main skill's evidence directory.

- Run `bash tests/native-memory/run.sh > "$verify_run/native-memory.log" 2>&1`. Capture actual rc. The native listing runs in read-only, network-isolated children with explicit profile homes; search fixtures use disposable databases. Read `run.sh` for current environment and evidence paths.
- Require one PASS per H/S/Y and CLI/Telegram pair. Require all collected pytest cases to pass, with no unexpected skips. Retain the reported test count as an observation, not a permanent expected constant.
- The regression fixtures must reject disabled/missing toolsets, deferrable critical tools and failed native config reads. Disabled tool-search must pass because tools remain direct. Fixtures change no installed configuration.
- `recall-owner` stays unverified until the owner confirms the relevant versions from an actual Telegram request. Ask for the verdict without asking for private text. Save that acceptance in the existing MR01 evidence and REQUESTS.

## Gotchas

Native toolset presence is not the model's final individual schema list. A plugin can override an individual tool inside an enabled set. The current gate documents that gap in `tests/native-memory/native_list.py`. A fresh CLI result does not prove a cached gateway or real recall action. Do not read real histories or invent an owner PASS from fixture counts.
