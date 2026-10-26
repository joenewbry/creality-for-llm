# Printer control operation core

This is the first review boundary of the LLM printer-control project. It contains
request validation, durable receipts, single-controller ownership, explicit
device assignment and immutable powered-pause checkpoints. Backend dispatch is
an interface; the tests supply a deterministic fake backend. It cannot operate
a physical printer or complete a print by itself.

A request binds its ID to printer identity, connection generation, expected state
version and deadline. Duplicate requests return prior receipts. Dispatch is
claimed durably before calling a backend; uncertain results are not blindly
replayed. Completion requires backend evidence. Checkpoints describe conditions
that must still hold before an in-place resume; they are not replay instructions.

## Reproduce this boundary

Use Python 3.11 on macOS or Linux (controller locking uses POSIX flock):

```sh
uv venv --python 3.11 .venv
uv pip install --python .venv/bin/python pytest==8.3.5
uv pip install --python .venv/bin/python --no-deps .
.venv/bin/python -m pytest -q tests
```

Tests exercise identity rejection before open, topology-change cleanup,
idempotency, stale observations, durable restart behavior, concurrency, ownership
and checkpoint invalidation. They use no physical serial ports, firmware builds,
OctoPrint, model service or credentials. The service retains extension slots and
journal records needed by later thermal/restore adapters; those integrations are
not present or qualified at this boundary.

## Subsequent review boundaries

Virtual jobs, native firmware behavior, thermal/restore execution, serial events,
setup and maintenance adapters follow in H2. LLM tools and model evaluation follow
in H3. Release evidence, commissioning, rollback and the explanatory page follow
in H4. These later boundaries are not yet independently assembled or tested.

The AGPL-3.0-or-later license applies to original host code. Third-party notices
cover the complete project's provenance; dependencies and firmware described
there are not bundled in this core boundary. No GitHub PR has been submitted.
