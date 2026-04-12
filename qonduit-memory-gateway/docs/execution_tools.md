# Phase E Execution Tools

This document describes the **project-scoped execution tools** added for
agentic development workflows.

## Supported tools

The gateway now supports these tools (in addition to existing retrieval and
read-only grounding tools):

1. `apply_patch`
2. `run_build`
3. `run_tests`
4. `tail_logs`

## Security model

All execution tools are constrained by the same project-scoped isolation model:

- The active `project_id` resolves to one project root under `PROJECTS_ROOT`.
- No writes outside the active project root are allowed.
- Paths that escape the root (for example `../..`) are blocked.
- Absolute path writes are blocked for patch operations.
- Symlink-based escape attempts are blocked.
- Build/test commands come from a fixed allowlist.
- The model cannot pass arbitrary command strings to the shell.
- Log access is restricted to known per-project execution logs and bounded
  output.

## `apply_patch`

`apply_patch` uses a constrained JSON patch format to keep writes explicit and
reviewable.

### Request shape

```json
{
  "patch": "{\"format\":\"qonduit.patch/v1\",\"operations\":[{\"action\":\"write\",\"path\":\"README.md\",\"content\":\"updated text\"}]}"
}
```

### Supported operations

- `write`: overwrite or create a file.
- `create`: create a new file and fail if it already exists.

### Response shape

```json
{
  "ok": true,
  "status": "success",
  "applied": true,
  "project_id": "demo",
  "error": null,
  "files_changed": ["README.md"],
  "affected_files": ["README.md"],
  "errors": [],
  "applied_count": 1,
  "summary": "Applied 1 patch operation(s)."
}
```

## `run_build`

`run_build` executes only allowlisted build commands based on detected project
markers.

### Allowlisted behavior

- **Flutter**: `flutter build apk --debug`
- **Android/Kotlin (Gradle)**: `android/gradlew assembleDebug --no-daemon`
- **Web (npm)**: `npm run build`
- **Safe mock marker** (`.qonduit_safe_mock`): fixed Python mock build command

No free-form command input is accepted from the model.

### Response shape

```json
{
  "ok": true,
  "status": "success",
  "applied": true,
  "project_id": "demo",
  "project_type": "safe_mock",
  "operation": "build",
  "command": ["python", "-c", "print('safe_mock_build_ok')"],
  "exit_code": 0,
  "duration_ms": 18,
  "log_source": "execution_logs/demo/build.log",
  "output_preview": "safe_mock_build_ok\n",
  "files_changed": ["demo/build.log"],
  "affected_files": ["demo/build.log"],
  "summary": "build completed successfully."
}
```

## `run_tests`

`run_tests` is identical in policy to `run_build`, but runs the matching
allowlisted test command.

### Allowlisted behavior

- **Flutter**: `flutter test --reporter expanded`
- **Android/Kotlin (Gradle)**: `android/gradlew test --no-daemon`
- **Web (npm)**: `npm run test`
- **Safe mock marker** (`.qonduit_safe_mock`): fixed Python mock test command

## `tail_logs`

`tail_logs` returns bounded output from known execution logs only.

### Request shape

```json
{
  "source": "build",
  "max_lines": 120
}
```

- `source` is restricted to `build` or `tests`.
- Output is byte-bounded and line-bounded.

### Response shape

```json
{
  "ok": true,
  "status": "success",
  "applied": true,
  "project_id": "demo",
  "source": "build",
  "line_count": 1,
  "truncated_bytes": 19,
  "files_changed": ["execution_logs/demo/build.log"],
  "affected_files": ["execution_logs/demo/build.log"],
  "summary": "Returned up to 120 log line(s).",
  "content": "safe_mock_build_ok"
}
```

## Notes

- Existing retrieval loop behavior and read-only grounding tools are preserved.
- Plain chat behavior is unchanged when tools are not in use.

## Troubleshooting tool execution loop

If a model response contains `finish_reason: "tool_calls"` but the tool does
not execute, check these points:

1. The gateway tool loop must:
   - append the assistant `tool_calls` message to conversation history,
   - execute each tool,
   - append each `role: "tool"` result with matching `tool_call_id`,
   - and make a follow-up model call.
2. Verify logs for:
   - `tool_loop_tool_calls_detected`
   - `tool_loop_executing_tool`
   - `tool_loop_tool_executed`
   - `tool_loop_followup_model_call`
3. If tool arguments are malformed, the gateway should emit a structured tool
   error payload instead of returning raw `tool_calls` to the client.
4. If max tool iterations is reached, the gateway should return a clear final
   assistant message rather than leaking unfinished raw `tool_calls`.

## Tool loop convergence troubleshooting

If tool calls execute but the model keeps calling the same tool repeatedly:

- Check tool result payloads for explicit completion signals:
  - `status: "success"`
  - `applied: true`
  - `files_changed`
  - `summary`
- Verify logs include argument signatures/hashes and repeat detection entries.
- Confirm repeated calls with identical args are blocked to prevent pointless
  loops.
- Confirm the follow-up system guidance message is present, instructing the
  model to summarize and stop after successful tool output.
- If all tool calls in an iteration are repeats, the gateway should exit the
  loop with a final assistant message instead of spinning until max iterations.
