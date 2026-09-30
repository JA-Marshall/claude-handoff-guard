"""Roll a session over to a fresh one once its context passes a token limit.

Stop hook (JSON on stdin). When the transcript reaches `rollover.max_tokens`:
  1. Claude is kept going with an instruction to write a handoff note to
     ~/.claude/handoffs/<session>.md (mode "self"), or the guard's summariser
     writes it from the transcript without another turn (mode "summariser").
  2. On the next stop a new session is started with `claude --bg`, seeded with
     that note, and its id is shown. Close the old session yourself.

Shares ~/.claude/handoff-guard.json with stale_context_guard.py; all keys sit
under "rollover" and are optional. Manual: `python3 context_rollover.py --check
[transcript.jsonl]` prints the current token count for a transcript.
"""
import glob, json, os, shutil, subprocess, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import stale_context_guard as guard  # noqa: E402  (config, scan, summarise, handoff)

DEFAULTS = {
    "enabled": True,
    "max_tokens": 200_000,
    "mode": "self",            # "self": Claude writes the note; "summariser": guard's external model does
    "launch": "bg",            # "bg": start `claude --bg` from the note; "none": just print the paste line
    "permission_mode": "",     # for the new session; blank = whatever the stopping session reports
    "model": "",               # for the new session; blank = your default
    "claude_path": "",         # blank = ~/.local/bin/claude, else `claude` on PATH
}
CFG = dict(DEFAULTS, **(guard.CFG.get("rollover") or {}))
env = os.environ.get
if env("HANDOFF_ROLLOVER_MAX_TOKENS"):
    CFG["max_tokens"] = int(env("HANDOFF_ROLLOVER_MAX_TOKENS"))
if env("HANDOFF_ROLLOVER_MODE"):
    CFG["mode"] = env("HANDOFF_ROLLOVER_MODE")
if env("HANDOFF_ROLLOVER_LAUNCH"):
    CFG["launch"] = env("HANDOFF_ROLLOVER_LAUNCH")
if env("HANDOFF_ROLLOVER_MODEL"):
    CFG["model"] = env("HANDOFF_ROLLOVER_MODEL")
if env("HANDOFF_ROLLOVER_DISABLE"):
    CFG["enabled"] = False

NOTE_TASK = guard.TASK.split("\n", 1)[1]  # the section list, minus the "Write a handoff note..." lead-in


def paths(session_id):
    d = os.path.expanduser(guard.CFG["output_dir"])
    os.makedirs(d, exist_ok=True)
    return (os.path.join(d, f"{session_id}.md"),
            os.path.join(d, f"{session_id}.rollover"),
            os.path.join(d, f"{session_id}.launched"))


def claude_exe():
    exe = os.path.expanduser(CFG["claude_path"] or "~/.local/bin/claude")
    return exe if os.path.exists(exe) else "claude"


def launch(note, sid, cwd, permission_mode):
    """Start the successor session. Returns the text to show the user."""
    paste = f"Read {note} and continue the work from where it leaves off."
    if CFG["launch"] != "bg":
        return f"Handoff note written: {note}\nOpen a new session and paste this:\n\n{paste}\n"
    # The note goes in the first message itself: reading it from ~/.claude/handoffs would
    # need a permission answer nobody is there to give in a background session.
    body = open(note, encoding="utf8").read().strip()
    prompt = (f"Continue the work described in this handoff note from the previous session "
              f"(also saved at {note}). Pick up at its Next step.\n\n{body}")
    cmd = [claude_exe(), "--bg", "--name", f"handoff {sid[:8]}", prompt]
    if permission_mode:
        cmd[2:2] = ["--permission-mode", permission_mode]
    if CFG["model"]:
        cmd[2:2] = ["--model", CFG["model"]]
    # A hook runs inside Claude Code: drop the markers that stop `claude` nesting, and the
    # HANDOFF_ROLLOVER_* overrides so a low test threshold can't make the child roll over too.
    clean = {k: v for k, v in os.environ.items()
             if k not in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT") and not k.startswith("HANDOFF_ROLLOVER_")}
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf8",
                       env=clean, timeout=120)
    out = (r.stdout or "").strip() or (r.stderr or "").strip()
    if r.returncode:
        return (f"Handoff note written: {note}\nCould not start the new session ({out}).\n"
                f"Open one yourself and paste:\n\n{prompt}\n")
    return f"Context rolled over. New session started from {note}:\n{out}\nThis session can be closed."


def emit(message):
    print(json.dumps({"systemMessage": message}))


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--check":
        path = sys.argv[2] if len(sys.argv) > 2 else max(
            glob.glob(os.path.expanduser("~/.claude/projects/*/*.jsonl")), key=os.path.getmtime)
        tokens = guard.scan(path)[0]
        print(f"{tokens:,} tokens in {path} (limit {CFG['max_tokens']:,})")
        return 0
    if env("CLAUDE_HANDOFF_CHILD") or not CFG["enabled"] or sys.stdin.isatty():
        return 0
    ev = json.load(sys.stdin)
    if env("HANDOFF_ROLLOVER_LOG"):
        with open(os.path.expanduser(env("HANDOFF_ROLLOVER_LOG")), "a", encoding="utf8") as f:
            f.write(json.dumps({"t": time.time(), "event": ev}) + "\n")
    transcript, sid = ev.get("transcript_path"), ev.get("session_id")
    if not sid or not transcript or not os.path.exists(transcript):
        return 0
    cwd = ev.get("cwd") or os.getcwd()
    note, nudge, done = paths(sid)

    if os.path.exists(done):
        return 0

    # Claude drafts the note in the session scratchpad, which needs no permission prompt,
    # and the hook moves it to output_dir. Without a scratchpad it writes the note directly.
    draft = os.path.join(ev["scratchpad_dir"], "handoff.md") if ev.get("scratchpad_dir") else note

    if os.path.exists(nudge):
        # Second stop: Claude should have written the draft after the nudge.
        fresh = os.path.exists(draft) and os.path.getsize(draft) > 200 \
            and os.path.getmtime(draft) >= os.path.getmtime(nudge) - 1
        if fresh and draft != note:
            shutil.copy(draft, note)
        if not fresh:
            try:  # Claude didn't write it; fall back to the guard's summariser.
                guard.handoff(transcript, guard.scan(transcript)[2])
            except Exception as e:
                emit(f"Context is over the rollover limit but no handoff note could be made: {e}")
                return 0
        open(done, "w").close()
        emit(launch(note, sid, cwd, CFG["permission_mode"] or ev.get("permission_mode", "")))
        return 0

    tokens = guard.scan(transcript)[0]
    if tokens < CFG["max_tokens"]:
        return 0
    open(nudge, "w").write(str(tokens))

    if CFG["mode"] == "summariser":
        try:
            guard.handoff(transcript, guard.scan(transcript)[2])
        except Exception as e:
            emit(f"Context is at {tokens:,} tokens but the summariser failed: {e}")
            return 0
        open(done, "w").close()
        emit(launch(note, sid, cwd, CFG["permission_mode"] or ev.get("permission_mode", "")))
        return 0

    print(json.dumps({
        "decision": "block",
        "reason": (
            f"This session's context is at {tokens:,} tokens, over the rollover limit of "
            f"{CFG['max_tokens']:,}. Before anything else, write a handoff note to {draft} so a "
            "fresh session with no memory of this chat can continue the work. Use exactly these "
            f"sections, in Markdown:\n\n{NOTE_TASK}\n\nThen stop. A new session will be started "
            "from that file automatically; do not start one yourself."
        ),
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
