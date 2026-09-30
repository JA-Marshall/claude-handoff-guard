# claude-handoff-guard

A Claude Code `UserPromptSubmit` hook that stops you resuming a large session whose prompt cache has gone cold, and writes a handoff note instead.

When a prompt is sent to a session holding 50k+ tokens that has been idle for 55+ minutes, the hook:

1. blocks the prompt (exit code 2);
2. reads the transcript file and asks Haiku, in a separate `claude -p` call, to write a handoff note, so the old context is never re-sent;
3. saves the note to `~/.claude/handoffs/<session>.md` and tells you to start a new session with `Read <file> and continue.`

Start a prompt with `!wake ` to override. Thresholds are constants at the top of the script.

## Install (Linux/WSL)

```bash
mkdir -p ~/.claude/hooks
cp stale_context_guard.py ~/.claude/hooks/
```

Add to `~/.claude/settings.json`:

```json
"hooks": {
  "UserPromptSubmit": [
    { "hooks": [ { "type": "command", "command": "python3 ~/.claude/hooks/stale_context_guard.py", "timeout": 200 } ] }
  ]
}
```

Use an absolute path on Windows; `%USERPROFILE%` is not expanded. The script looks for `claude` at `~/.local/bin/claude`, so adjust `claude_exe()` if yours lives elsewhere.

## Manual "button"

`python3 stale_context_guard.py [transcript.jsonl]` writes a handoff for any session on demand (default: most recently modified).
