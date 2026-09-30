# claude-handoff-guard

A Claude Code `UserPromptSubmit` hook that stops you resuming a large session whose prompt cache has gone cold, and writes a handoff note instead.

When a prompt is sent to a session holding 50k+ tokens that has been idle for 55+ minutes, the hook:

1. blocks the prompt (exit code 2);
2. reads the transcript file and asks Haiku, in a separate `claude -p` call, to write a handoff note, so the old context is never re-sent;
3. shows what waking it would have cost (cache rewrite at 1.25x-2x input price, then the cache-read price per message; edit `PRICES` if rates change);
4. saves the note to `~/.claude/handoffs/<session>.md` and prints a ready-to-paste line: `Read <file> and continue from it.`

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

Next to each handoff, `<session>.prompt.txt` holds the exact text sent to Haiku (user/assistant text only, each message cut to 1500 chars, last 120k chars kept; tool calls and results are not included).

`python3 stale_context_guard.py [transcript.jsonl]` writes a handoff for any session on demand (default: most recently modified).
