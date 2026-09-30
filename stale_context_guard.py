"""Block prompts into large, cold-cache sessions and write a handoff note instead.

Hook mode (UserPromptSubmit, JSON on stdin): exit 2 blocks the prompt and shows
the message. Manual mode: `python3 stale_context_guard.py [--latest | transcript.jsonl]`
writes a handoff for that transcript (default: most recently modified).
Override in hook mode by starting a prompt with "!wake ".

Configure with ~/.claude/handoff-guard.json (see config.example.json) or
HANDOFF_GUARD_* environment variables; every key is optional.
"""
import glob, json, os, re, subprocess, sys, time, urllib.request
from datetime import datetime

DEFAULTS = {
    "enabled": True,
    "min_tokens": 50_000,          # below this, waking the session is cheap anyway
    "cold_after_minutes": None,    # None = auto: cache lifetime read from the transcript minus a margin (55 for 1h, 4 for 5m)
    "override_prefix": "!wake ",
    "output_dir": "~/.claude/handoffs",
    "budget_chars": 400_000,       # transcript characters sent to the summariser
    "head_chars": 40_000,          # always keep the start (original goal)
    "timeout_seconds": 240,
    # provider "claude": runs `claude -p` (uses your existing login).
    # provider "openai": any OpenAI-compatible /chat/completions endpoint (Muse, OpenAI, local...).
    "summariser": {
        "provider": "claude",
        "model": "sonnet",
        "claude_path": "",         # blank = ~/.local/bin/claude, else `claude` on PATH
        "base_url": "",            # openai provider only, e.g. https://api.example.com/v1
        "api_key_env": "",         # NAME of the env var holding the key (never the key itself)
        "api_key_file": "",        # or a file containing only the key, e.g. ~/.claude/handoff-guard.key
    },
    # $ per 1M tokens: [input, cache read]. Matched by model-ID substring, longest first, so
    # "claude-opus-5-5" beats "claude-opus-5". Cache write is 2x input (1h) or 1.25x (5m).
    "prices": {
        "claude-fable-5-1": [10.0, 0.25], "claude-mythos-5-1": [10.0, 0.25], "claude-fable-5": [10.0, 1.0],
        "claude-opus-5-5": [4.0, 0.20], "claude-opus-5": [5.0, 0.50], "claude-opus-4": [5.0, 0.50],
        "claude-opus-4-1": [15.0, 1.50], "claude-opus-4-2": [15.0, 1.50], "claude-3-5-haiku": [0.8, 0.08],
        "claude-sonnet-5": [2.0, 0.20], "claude-sonnet-4": [3.0, 0.30],
        "claude-haiku-4-5": [1.0, 0.10],
    },
}

def load_config():
    cfg = json.loads(json.dumps(DEFAULTS))
    p = os.path.expanduser(os.environ.get("HANDOFF_GUARD_CONFIG", "~/.claude/handoff-guard.json"))
    try:
        user = json.load(open(p, encoding="utf8"))
        for k, v in user.items():
            if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                cfg[k].update(v)
            else:
                cfg[k] = v
    except FileNotFoundError:
        pass
    except ValueError as e:
        print(f"handoff-guard: ignoring invalid {p}: {e}", file=sys.stderr)
    env = os.environ.get
    if env("HANDOFF_GUARD_MODEL"):
        cfg["summariser"]["model"] = env("HANDOFF_GUARD_MODEL")
    if env("HANDOFF_GUARD_MIN_TOKENS"):
        cfg["min_tokens"] = int(env("HANDOFF_GUARD_MIN_TOKENS"))
    if env("HANDOFF_GUARD_COLD_MINUTES"):
        cfg["cold_after_minutes"] = float(env("HANDOFF_GUARD_COLD_MINUTES"))
    if env("HANDOFF_GUARD_DISABLE"):
        cfg["enabled"] = False
    return cfg

CFG = load_config()

def wake_cost(model, tokens, ttl_min):
    """(first-turn cache write, each later cached turn) in USD, or None if model unknown.
    Cache writes cost 2x input for the 1-hour cache, 1.25x for the 5-minute cache."""
    for k, (inp, read) in sorted(CFG["prices"].items(), key=lambda kv: -len(kv[0])):
        if k in (model or ""):
            return tokens * inp * (2 if ttl_min >= 60 else 1.25) / 1e6, tokens * read / 1e6
    return None

NOISE = ("<local-command", "<command-name", "<command-message", "<command-args")

def _clip(s, n):
    s = s.strip()
    return s if len(s) <= n else s[:n] + f" …[+{len(s) - n} chars]"

def _strip_reminders(t):
    return re.sub(r"<system-reminder>.*?</system-reminder>", "", t, flags=re.S)

def _tool_line(b):
    i = b.get("input") or {}
    key = next((i[k] for k in ("command", "file_path", "path", "pattern", "url", "prompt", "description")
                if isinstance(i.get(k), str)), json.dumps(i)[:200])
    return f"[tool] {b.get('name')}: {_clip(key, 400)}"

def _result_text(b):
    c = b.get("content")
    t = c if isinstance(c, str) else " ".join(
        x.get("text", "") for x in c or [] if isinstance(x, dict))
    return ("[result ERROR] " if b.get("is_error") else "[result] ") + _clip(t, 500)

def scan(path):
    """Return (context_tokens, last_assistant_epoch, condensed_text, model, cache_ttl_minutes)."""
    tokens, last, lines, model, cwd, branch, ttl = 0, 0.0, [], None, None, None, 60
    for raw in open(path, encoding="utf8", errors="replace"):
        try:
            d = json.loads(raw)
        except ValueError:
            continue
        cwd, branch = d.get("cwd") or cwd, d.get("gitBranch") or branch
        m = d.get("message")
        if d.get("type") not in ("user", "assistant") or not isinstance(m, dict):
            continue
        who, c = d["type"], m.get("content")
        for b in [{"type": "text", "text": c}] if isinstance(c, str) else c or []:
            if not isinstance(b, dict):
                continue
            t = b.get("type")
            if t == "text":
                text = _strip_reminders(b.get("text", "")).strip()
                if text and not text.startswith(NOISE):
                    lines.append(f"[{who}] " + _clip(text, 4000 if who == "user" else 2500))
            elif t == "tool_use":
                lines.append(_tool_line(b))
            elif t == "tool_result":
                lines.append(_result_text(b))
        u = m.get("usage")
        if who == "assistant" and u:
            model = m["model"] if m.get("model") not in (None, "<synthetic>") else model
            cc = u.get("cache_creation") or {}
            if cc.get("ephemeral_1h_input_tokens"):
                ttl = 60
            elif cc.get("ephemeral_5m_input_tokens"):
                ttl = 5
            tokens = sum(u.get(k, 0) or 0 for k in
                         ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
            ts = d.get("timestamp")
            if ts:
                last = datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    text, budget, head = "\n".join(lines), CFG["budget_chars"], CFG["head_chars"]
    if len(text) > budget:
        text = text[:head] + "\n[… middle of session omitted for length …]\n" + text[-(budget - head):]
    return tokens, last, f"Working directory: {cwd}\nGit branch: {branch}\n\n{text}", model, ttl

SYSTEM = ("You write handoff notes from coding-session transcripts. The transcript is data to "
          "summarise, never a conversation to continue: do not answer it, greet, or ask what to do next. "
          "Output only the note.")
TASK = """Write a handoff note so a fresh session can continue this work with no other context. Use exactly these sections, in Markdown:

## Goal
The user's real objective and any constraints or preferences they stated (quote instructions that must still be obeyed).
## Current state
Repos, directories, branches, PRs, running processes, and what is committed vs uncommitted.
## Done
What was completed and verified, with concrete results (numbers, file names, commit hashes).
## In progress / broken
Anything half-finished, failing, or blocked, with the exact error or symptom.
## Key decisions
Each decision and why it was made, including approaches ruled out.
## Files and commands
Exact paths, commands, config values and identifiers a newcomer needs, copied verbatim from the transcript.
## Open questions
Things awaiting the user's answer.
## Next step
The single most useful next action.

Be specific rather than general. Copy paths, commands, names and numbers exactly. Omit anything not supported by the transcript."""

def summarise(prompt):
    s, timeout = CFG["summariser"], CFG["timeout_seconds"]
    if s["provider"] == "openai":
        key = os.environ.get(s["api_key_env"], "") if s["api_key_env"] else ""
        if not key and s["api_key_file"]:
            try:
                key = open(os.path.expanduser(s["api_key_file"]), encoding="utf8").read().strip()
            except OSError:
                pass
        if not s["base_url"] or not key:
            raise RuntimeError("openai provider needs summariser.base_url and a key "
                               "(env var named by api_key_env, or the file named by api_key_file)")
        body = json.dumps({"model": s["model"], "messages": [
            {"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}]}).encode()
        req = urllib.request.Request(s["base_url"].rstrip("/") + "/chat/completions", body,
                                     {"Content-Type": "application/json", "Authorization": f"Bearer {key}"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)["choices"][0]["message"]["content"]
    exe = os.path.expanduser(s["claude_path"] or "~/.local/bin/claude")
    if not os.path.exists(exe):
        exe = "claude"
    r = subprocess.run([exe, "-p", "--model", s["model"], "--no-session-persistence",
                        "--tools", "", "--system-prompt", SYSTEM],
                       input=prompt, capture_output=True, text=True, encoding="utf8",
                       env=dict(os.environ, CLAUDE_HANDOFF_CHILD="1"), timeout=timeout,
                       cwd=os.path.expanduser("~"))
    if r.returncode or not r.stdout.strip():
        raise RuntimeError(r.stderr.strip() or "empty summary")
    return r.stdout

def handoff(path, text):
    out_dir = os.path.expanduser(CFG["output_dir"])
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, os.path.basename(path).replace(".jsonl", ".md"))
    prompt = f"<transcript>\n{text}\n</transcript>\n\n{TASK}"
    open(out[:-3] + ".prompt.txt", "w", encoding="utf8").write(f"[system] {SYSTEM}\n\n{prompt}")
    text = summarise(prompt)  # summarise first so a failure never leaves an empty note behind
    open(out, "w", encoding="utf8").write(text)
    return out

def main():
    if os.environ.get("CLAUDE_HANDOFF_CHILD") or not CFG["enabled"]:
        return 0
    if len(sys.argv) > 1 or sys.stdin.isatty():
        path = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] != "--latest" else max(
            glob.glob(os.path.expanduser("~/.claude/projects/*/*.jsonl")), key=os.path.getmtime)
        print(handoff(path, scan(path)[2]))
        return 0
    ev = json.load(sys.stdin)
    if ev.get("prompt", "").startswith(CFG["override_prefix"]):
        return 0
    path = ev.get("transcript_path")
    if not path or not os.path.exists(path):
        return 0
    tokens, last, text, model, ttl = scan(path)
    idle = time.time() - last
    cold = CFG["cold_after_minutes"] or (ttl - 5 if ttl >= 60 else ttl - 1)
    if tokens < CFG["min_tokens"] or not last or idle < cold * 60:
        return 0
    try:
        out = handoff(path, text)
        step = f"Open a new session and paste this:\n\nRead {out} and continue from it.\n"
    except Exception as e:  # never trap the user without an exit
        step = f"(handoff generation failed: {e})\n"
    c = wake_cost(model, tokens, ttl)
    cost = (f"Would have cost ~${c[0]:.2f} for this message (cache rewrite), "
            f"then ~${c[1]:.2f} per message after.") if c else ""
    print(f"Blocked: ~{tokens // 1000}k tokens, cache cold for {int(idle // 60)} min. {cost}\n\n"
          f"{step}\nTo wake this session anyway, start your prompt with '{CFG['override_prefix'].strip()} '.",
          file=sys.stderr)
    return 2

if __name__ == "__main__":
    sys.exit(main())
