<div align="center">

# 🧊 claude-handoff-guard

**Stop paying to wake a cold Claude Code session. Get a handoff note instead.**

</div>

Resume a big session after a coffee break and the prompt cache has expired, so Claude Code re-bills the *entire* context at full price. This hook catches that moment, tells you what it would have cost, and writes a structured handoff note with a cheap model that reads the transcript file directly, so the expensive context is never touched.

```text
Blocked: ~385k tokens, cache cold for 6425 min. Would have cost ~$3.08 for this message (cache rewrite), then ~$0.08 per message after.

Open a new session and paste this:

Read /home/you/.claude/handoffs/b12944fc-….md and continue from it.

To wake this session anyway, start your prompt with '!wake '.
```

## Features

- **Cold-cache guard** blocks prompts into sessions over `min_tokens` (default 50k) idle past the cache lifetime.
- **Cost estimate** for the message you almost sent, from the session's own model and token count.
- **Structured handoff** with Goal, Current state, Done, In progress, Key decisions, Files and commands, Open questions and Next step. It reads tool calls and results, so paths, commands and branches survive.
- **Any summariser**: your Claude login (`claude -p`) or any OpenAI-compatible API such as Meta's Muse Spark.
- **Fully auditable**: the exact prompt sent is saved next to each note as `<session>.prompt.txt`.
- **Never traps you**: if summarising fails you still get the block and the error, and `!wake ` always gets through.
- **Zero dependencies**: one Python file, standard library only.

## Install

```bash
git clone https://github.com/JA-Marshall/claude-handoff-guard
cd claude-handoff-guard
python3 install.py
```

The installer copies the hook to `~/.claude/hooks/`, registers it in `~/.claude/settings.json` (backing the file up first), and drops a default config. It takes effect on your next prompt. Run it in the same environment as Claude Code (WSL users: inside WSL).

## Configure

Edit `~/.claude/handoff-guard.json`. Every key is optional; unset keys keep their defaults.

| Key | Default | Meaning |
|---|---|---|
| `enabled` | `true` | Switch the hook off without uninstalling |
| `min_tokens` | `50000` | Only guard sessions at least this large |
| `cold_after_minutes` | auto | Idle time after which the cache counts as cold. Auto reads the cache lifetime from the transcript (1 hour or 5 minutes) and subtracts a margin: 55 or 4 minutes |
| `override_prefix` | `"!wake "` | Prefix that lets a prompt through |
| `output_dir` | `~/.claude/handoffs` | Where notes are written |
| `budget_chars` / `head_chars` | `400000` / `40000` | Transcript size sent to the summariser; the start is always kept, the middle is trimmed |
| `timeout_seconds` | `240` | Summariser timeout (keep the hook timeout above it) |
| `summariser.provider` | `"claude"` | `"claude"` or `"openai"` (any OpenAI-compatible endpoint) |
| `summariser.model` | `"sonnet"` | Model name for that provider |
| `summariser.claude_path` | | Path to `claude` if it isn't `~/.local/bin/claude` |
| `summariser.base_url` | | OpenAI-compatible base URL, e.g. `https://host/v1` |
| `summariser.api_key_env` | | **Name** of the environment variable holding the key |
| `summariser.api_key_file` | | Or a file holding only the key (`chmod 600`); read on every run, no restart needed |
| `prices` | see script | `{"model-id-substring": [input, cache_read]}` in $ per 1M tokens; the longest matching ID wins, so `claude-opus-5-5` and `claude-opus-5` can differ |

Environment overrides: `HANDOFF_GUARD_CONFIG`, `HANDOFF_GUARD_MODEL`, `HANDOFF_GUARD_MIN_TOKENS`, `HANDOFF_GUARD_COLD_MINUTES`, `HANDOFF_GUARD_DISABLE`.

### Example: Muse Spark as the summariser

```json
{
  "summariser": {
    "provider": "openai",
    "model": "muse-spark-1.3",
    "base_url": "https://api.meta.ai/v1",
    "api_key_env": "MODEL_API_KEY",
    "api_key_file": "~/.claude/handoff-guard.key"
  }
}
```

Never put a key in the config or commit it. Use the env var or the key file.

## On-demand handoff

Write a note for any session without waiting for a block:

```bash
python3 ~/.claude/hooks/stale_context_guard.py --latest        # most recent session
python3 ~/.claude/hooks/stale_context_guard.py path/to/session.jsonl
```

## How the cost is estimated

A cold cache means the whole context is re-written at 2x (1-hour cache) or 1.25x (5-minute cache) the input price, detected from the transcript; every later message then reads it at the cache-read price (0.1x input; 0.05x on Opus 5.5, 0.025x on Fable 5.1). The estimate ignores output and thinking tokens, so the real bill is a little higher. Edit `prices` when rates change.

## Notes

- Timing depends on the summariser and the size of the session; a 385k-token session took about 2 minutes with Muse Spark 1.3.
- Handoffs are only as good as what the transcript contains. If a note looks thin, read the saved `.prompt.txt` to see what the model was given.
