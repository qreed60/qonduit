# Coding Mode Behavior

## Mode selection precedence

The gateway resolves mode with this precedence:

1. request `mode`
2. `X-Gateway-Mode` header
3. model alias `default_mode`
4. per-project defaults (`PROJECT_DEFAULT_MODE_MAP`)
5. environment defaults (`PROJECT_DEFAULT_MODE`, then `DEFAULT_MODE`)

Supported modes:

- `chat`
- `coding`

## What coding mode changes

- Uses a coding-focused system prompt.
- Keeps a larger recent-message window.
- Applies technical-message protection during trimming.
- Uses deterministic technical summarization designed to preserve:
  - file paths and filenames
  - symbols (classes/functions/interfaces/enums)
  - APIs/endpoints
  - build/test commands
  - exact errors/log lines
  - active task
  - decisions made
  - unresolved issues
  - constraints/rejected approaches

## Trimming protection signals

Messages are treated as technical/high-value if they include patterns like:

- code fences
- stack traces/errors
- source-like filenames/extensions
- build/test command lines
- explicit constraints

## Operational note

Coding mode is intended for long-running implementation/debug sessions where
retaining precise technical details is more important than conversational
brevity.
