"""Block prompts into large, cold-cache sessions and write a cheap handoff instead.

Hook mode (UserPromptSubmit, JSON on stdin): exit 2 blocks the prompt and shows
the message. Manual mode: `python stale_context_guard.py [transcript.jsonl]`
writes a handoff for that transcript (default: most recently modified).
Override in hook mode by starting a prompt with "!wake ".
"""
import glob, json, os, subprocess, sys, time
from datetime import datetime, timezone

MIN_TOKENS = 50_000   # below this, waking the session is cheap anyway
COLD_AFTER = 55 * 60  # prompt cache TTL is 1h; treat >55min idle as cold
MODEL = "haiku"
# $ per 1M tokens: (input, cache read). Cache write is 1.25x input (5m TTL) or 2x (1h TTL).
PRICES = {"fable": (10.0, 0.25), "opus": (4.0, 0.20), "sonnet": (2.0, 0.20), "haiku": (1.0, 0.10)}

def wake_cost(model, tokens):
    """(first-turn cost range low, high, each later cached turn) in USD, or None if model unknown."""
    for k, (inp, read) in PRICES.items():
        if k in (model or ""):
            return tokens * inp * 1.25 / 1e6, tokens * inp * 2 / 1e6, tokens * read / 1e6
    return None
OUT = os.path.expanduser("~/.claude/handoffs")

def scan(path):
    """Return (context_tokens, last_assistant_epoch, condensed_text, model)."""
    tokens, last, lines, model = 0, 0.0, [], None
    for raw in open(path, encoding="utf8", errors="replace"):
        try:
            d = json.loads(raw)
        except ValueError:
            continue
        m = d.get("message")
        if d.get("type") not in ("user", "assistant") or not isinstance(m, dict):
            continue
        c = m.get("content")
        text = c if isinstance(c, str) else " ".join(
            b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text")
        if text.strip():
            lines.append(f"[{d['type']}] {text.strip()[:1500]}")
        u = m.get("usage")
        if d["type"] == "assistant" and u:
            model = m.get("model") or model
            tokens = sum(u.get(k, 0) or 0 for k in
                         ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
            ts = d.get("timestamp")
            if ts:
                last = datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    return tokens, last, "\n".join(lines), model

def claude_exe():
    hits = [os.path.expanduser("~/.local/bin/claude")] if os.path.exists(os.path.expanduser("~/.local/bin/claude")) else []
    return hits[-1] if hits else "claude"

def handoff(path, text):
    os.makedirs(OUT, exist_ok=True)
    out = os.path.join(OUT, os.path.basename(path).replace(".jsonl", ".md"))
    prompt = ("Below is a condensed transcript of a coding session. Write a handoff note for a "
              "fresh session: goal, what is done, what is in progress, key decisions and why, "
              "exact file paths/branches/commands that matter, open questions, and the single "
              "next step. Be concise; no preamble.\n\n" + text[-120_000:])
    env = dict(os.environ, CLAUDE_HANDOFF_CHILD="1")
    r = subprocess.run([claude_exe(), "-p", "--model", MODEL, "--no-session-persistence"],
                       input=prompt, capture_output=True, text=True, encoding="utf8",
                       env=env, timeout=180, cwd=os.path.expanduser("~"))
    if r.returncode or not r.stdout.strip():
        raise RuntimeError(r.stderr.strip() or "empty summary")
    open(out, "w", encoding="utf8").write(r.stdout)
    return out

def main():
    if os.environ.get("CLAUDE_HANDOFF_CHILD"):
        return 0
    if len(sys.argv) > 1 or sys.stdin.isatty():
        path = sys.argv[1] if len(sys.argv) > 1 else max(
            glob.glob(os.path.expanduser("~/.claude/projects/*/*.jsonl")), key=os.path.getmtime)
        print(handoff(path, scan(path)[2]))
        return 0
    ev = json.load(sys.stdin)
    if ev.get("prompt", "").startswith("!wake "):
        return 0
    path = ev.get("transcript_path")
    if not path or not os.path.exists(path):
        return 0
    tokens, last, text, model = scan(path)
    idle = time.time() - last
    if tokens < MIN_TOKENS or not last or idle < COLD_AFTER:
        return 0
    try:
        out = handoff(path, text)
        step = f"Open a new session and paste this:\n\nRead {out} and continue from it.\n"
    except Exception as e:  # never trap the user without an exit
        step = f"(handoff generation failed: {e})\n"
    c = wake_cost(model, tokens)
    cost = (f"Would have cost ~${c[0]:.2f}-${c[1]:.2f} for this message (cache rewrite), "
            f"then ~${c[2]:.2f} per message after.") if c else ""
    print(f"Blocked: ~{tokens // 1000}k tokens, cache cold for {int(idle // 60)} min. {cost}\n\n"
          f"{step}\nTo wake this session anyway, start your prompt with '!wake '.", file=sys.stderr)
    return 2

sys.exit(main())
