# claude-handoff-guard

A Claude Code `UserPromptSubmit` hook that stops you resuming a large session whose prompt cache has gone cold, and writes a handoff note instead.

When a prompt is sent to a session over `min_tokens` that has been idle longer than `cold_after_minutes`, the hook:

1. blocks the prompt (exit code 2);
2. shows what waking the session would have cost (cache rewrite at 1.25x-2x input price, then the cache-read price per message);
3. reads the transcript file and asks a separate model to write a handoff note, so the old context is never re-sent;
4. saves the note to `~/.claude/handoffs/<session>.md` and prints a ready-to-paste line: `Read <file> and continue from it.`

`<session>.prompt.txt` next to it holds the exact text the summariser received (user/assistant text, tool calls, and truncated tool results).

Start a prompt with `!wake ` to override.

## Install (Linux/WSL/macOS)

```bash
mkdir -p ~/.claude/hooks
cp stale_context_guard.py ~/.claude/hooks/
```

Add to `~/.claude/settings.json`:

```json
"hooks": {
  "UserPromptSubmit": [
    { "hooks": [ { "type": "command", "command": "python3 ~/.claude/hooks/stale_context_guard.py", "timeout": 300 } ] }
  ]
}
```

On Windows use an absolute path; `%USERPROFILE%` is not expanded. Run it in the same environment as Claude Code (for WSL users, inside WSL).

## Configure

Everything is optional. Copy `config.example.json` to `~/.claude/handoff-guard.json` and change what you want; unspecified keys keep their defaults.

| Key | Default | Meaning |
|---|---|---|
| `enabled` | `true` | Turn the hook off without uninstalling |
| `min_tokens` | `50000` | Only guard sessions at least this large |
| `cold_after_minutes` | `55` | Idle time after which the cache counts as cold |
| `override_prefix` | `"!wake "` | Prefix that lets a prompt through |
| `output_dir` | `~/.claude/handoffs` | Where handoffs are written |
| `budget_chars` / `head_chars` | `400000` / `40000` | Transcript size sent to the summariser; the start is always kept, the middle is trimmed |
| `timeout_seconds` | `240` | Summariser timeout |
| `summariser.provider` | `"claude"` | `"claude"` runs `claude -p` with your login; `"openai"` calls any OpenAI-compatible endpoint |
| `summariser.model` | `"sonnet"` | Model name for that provider |
| `summariser.claude_path` | | Path to the `claude` binary if not `~/.local/bin/claude` |
| `summariser.base_url` | | OpenAI-compatible base URL, e.g. `https://host/v1` |
| `summariser.api_key_env` | | **Name** of the environment variable holding the key |
| `prices` | see script | `{"model-substring": [input, cache_read]}` in $ per 1M tokens |

Environment overrides: `HANDOFF_GUARD_CONFIG`, `HANDOFF_GUARD_MODEL`, `HANDOFF_GUARD_MIN_TOKENS`, `HANDOFF_GUARD_COLD_MINUTES`, `HANDOFF_GUARD_DISABLE`.

### Using another model (e.g. Muse)

```json
{
  "summariser": {
    "provider": "openai",
    "model": "your-model-name",
    "base_url": "https://your-endpoint/v1",
    "api_key_env": "MUSE_API_KEY"
  }
}
```

Export the key in the environment Claude Code runs in (for example in `~/.profile`); never put the key in the config file or commit it.

If summarising fails, the prompt is still blocked and the error is shown, and `!wake ` still gets you through.

## Manual "button"

`python3 stale_context_guard.py [transcript.jsonl]` writes a handoff for any session on demand (default: most recently modified).
