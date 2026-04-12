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
  "project_id": "demo",
  "error": null,
  "affected_files": ["README.md"],
  "errors": [],
  "applied_count": 1
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
  "project_id": "demo",
  "project_type": "safe_mock",
  "operation": "build",
  "command": ["python", "-c", "print('safe_mock_build_ok')"],
  "exit_code": 0,
  "duration_ms": 18,
  "log_source": "execution_logs/demo/build.log",
  "output_preview": "safe_mock_build_ok\n",
  "affected_files": ["demo/build.log"]
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
  "project_id": "demo",
  "source": "build",
  "line_count": 1,
  "truncated_bytes": 19,
  "affected_files": ["execution_logs/demo/build.log"],
  "content": "safe_mock_build_ok"
}
```

## Notes

- Existing retrieval loop behavior and read-only grounding tools are preserved.
- Plain chat behavior is unchanged when tools are not in use.
