# CLI adapter wiring

The v1 adapter for real coding-assistant CLIs is subprocess-only, behind the
`AgentAdapter` interface. No vendor SDK is used.

## Wiring a CLI

```python
from harness.agents.cli import CliAgent

adapter = CliAgent(
    adapter_id="my-cli",
    command=["my-cli", "--task", "{task}"],  # literal argv, no interpolation
    required_capabilities=("fs.read", "fs.write.isolated"),
    timeout_s=300,
)
```

Notes:

- `command` is a fixed argv list; the token `"python"` is mapped to
  `sys.executable`. There is no shell and no string interpolation.
- The subprocess runs with `cwd` set to the isolated workspace and a
  sanitized environment (allowlisted keys only; secret-looking values are
  never forwarded, even via `extra_env`).
- Read-only `HARNESS_*` variables are injected (`HARNESS_PHASE`,
  `HARNESS_RUN_ID`, `HARNESS_TASK_ID`, `HARNESS_ATTEMPT`).
- If any `required_capabilities` entry is outside the phase's effective
  permission set, the adapter **refuses before spawning** and reports the
  missing capabilities (the engine then escalates the run).

## Result contract

The CLI must print a single JSON object on stdout (last line is parsed):

```json
{
  "status": "succeeded",
  "summary": "what was done",
  "changed_paths": ["impl.py"],
  "artifact_digest": "sha256:… (optional; computed if omitted)",
  "capabilities_used": ["fs.read", "fs.write.isolated"],
  "requested_capabilities": [],
  "error": ""
}
```

Malformed output, a non-zero exit without valid JSON, or a timeout is a
failure (fail closed). Timeouts raise `AgentTimeout` to the driver, which
records an `AGENT_FAILED` event.

## Status in v1

The adapter is implemented and integration-tested (success, malformed
output, capability refusal without spawning, timeout, and a full
driver run). It is **excluded from the measured evaluation**, which uses
the deterministic stub suite only.
