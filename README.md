# Correlated prompt transport

This H2b review boundary builds on H2a's observations and H1's operation core.
It adds a shared complete-frame writer, bounded receive-copy buffer, prompt
session channel and durable prompt-operation backend. No physical device opener
or production printer adapter is included.

The writer prevents frame interleaving and rejects prompt responses sent through
the ordinary job-command entry point. An interrupted, partial or unconfirmed
write latches uncertainty. The receive hook returns the sender's original reply;
its independent copy is bounded and a loss invalidates the channel. These
components cannot enforce ownership if an enclosing sender bypasses them.

Prompt responses bind to a negotiated session, request and exact prompt token.
Only the generic one-button user-wait profile is supported. Stale tokens,
unsupported choices, faulted channels and uncertain completion remain blocked.
A successful prompt receipt means the tested firmware wait flag cleared; it does
not mean a print resumed or that physical intervention is complete.

## Reproduce

Use Python 3.11, Git and `clang++` on macOS or Linux:

```sh
uv venv --python 3.11 .venv
uv pip install --python .venv/bin/python pytest==8.3.5
uv pip install --python .venv/bin/python --no-deps .
.venv/bin/python scripts/prepare_firmware.py --repository /path/to/reviewed/firmware-repository
.venv/bin/python -m pytest -q tests
```

The reviewed repository must contain the commit/tree in
`firmware-test-source.json`; the public fork is not published yet. The native
harness runs actual firmware prompt functions with physical dependencies
substituted. Channel/backend tests exchange frames with this harness through
process pipes, not USB. Writer tests also force partial writes and concurrent
calls. Tests do not connect to a printer, require credentials or invoke a model.

Virtual jobs, powered-pause/restore execution, setup/maintenance, the LLM server
and physical commissioning remain later review units. The enclosing transport
must establish device identity, exclusive ownership and its permitted profile
before adopting these components. No reconnect or automatic fault recovery is
provided. No PR has been submitted.

Original host source is AGPL-3.0-or-later. Native firmware retains its GPL terms
and is fetched separately; third-party notices are included.
