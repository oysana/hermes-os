# Personal addressing and intake

Telegram and WhatsApp should recognize direct questions to Yusuf without treating quoted names or URL fragments as a real address.

## Sub-features

- `address-common` gives both installed lexical parsers the same published positive and negative inputs.
- `address-native` preserves native mention/reply metadata and parser bounds.
- `intake-ready` shows that the owning services and configured platforms are connected.
- `intake-natural` proves a real new addressed message routes and completes correctly.

## How to get to it (user POV)

A person addresses Yusuf by name or native mention in personal Telegram/WhatsApp intake. The owner's bots and ordinary CLI have different entry points; a private CLI test is not a received group message.

## Driving it with the published parser suite

Preconditions: run from the project root. Default selectors must point at installed sources; record any staged override explicitly.

- Before and after the command, hash the runner, shared corpus, Python/Node tests, selected parser modules and `~/.hermes/shared/communications/owner-addressing.json`. Both parsers load that alias file; parser hashes alone do not identify all lexical inputs. Require each selected input to remain unchanged during the run.
- Run `bash tests/group-addressing/run.sh > "$verify_run/group-addressing.log" 2>&1`. Capture actual rc. Require all published Python cases and all Node tests to pass without weakening the corpus or adding skips.
- Inspect a direct positive such as `Как дела, Yusuf?` and the quoted/code/URL negatives in the shared corpus. Native mention/reply cases remain independent of lexical aliases.
- For `intake-ready`, use the main skill's approved metadata doctor. If unit metadata is needed, use `systemctl --user show hermes-gateway.service hermes-whatsapp-reader.service --property=ActiveState,SubState,MainPID,ExecMainStartTimestamp`. Do not read message-bearing journals. A restart is a separate authorized maintenance action, never a test cleanup step.
- `intake-natural` needs a matching actual incoming event and routing/delivery evidence. Do not send to contacts to manufacture it. Preserve this entry point as unverified until suitable authorized evidence exists.

## Gotchas

Disk hashes do not hot-reload Python or ESM imports. Record installed bytes and service start/PID separately. Readiness is not a reservation. Do not use personal-replies status for a read-only probe because its constructors can migrate state. Parser PASS, connected channels and real delivery are separate verdicts.
