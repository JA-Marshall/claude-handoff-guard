#!/usr/bin/env python3
"""Install claude-handoff-guard: copy the hook and register it in ~/.claude/settings.json."""
import json, os, shutil, sys, time

home = os.path.expanduser("~")
dest = os.path.join(home, ".claude", "hooks", "stale_context_guard.py")
settings = os.path.join(home, ".claude", "settings.json")
here = os.path.dirname(os.path.abspath(__file__))

os.makedirs(os.path.dirname(dest), exist_ok=True)
for name in ("stale_context_guard.py", "context_rollover.py"):
    shutil.copy(os.path.join(here, name), os.path.join(os.path.dirname(dest), name))

cfg = {}
if os.path.exists(settings):
    shutil.copy(settings, f"{settings}.bak-{int(time.time())}")
    cfg = json.load(open(settings, encoding="utf8"))

def register(event, script, timeout):
    path = os.path.join(os.path.dirname(dest), script)
    cmd = f"python3 {path}" if os.name != "nt" else f"python {path.replace(os.sep, '/')}"
    groups = cfg.setdefault("hooks", {}).setdefault(event, [])
    if any(script in h.get("command", "") for g in groups for h in g.get("hooks", [])):
        print(f"{event} hook already registered; script updated.")
        return False
    groups.append({"hooks": [{"type": "command", "command": cmd, "timeout": timeout}]})
    print(f"Registered {event} hook in {settings}")
    return True

changed = register("UserPromptSubmit", "stale_context_guard.py", 300)
changed = register("Stop", "context_rollover.py", 300) or changed
if changed:
    json.dump(cfg, open(settings, "w", encoding="utf8"), indent=2)

sample = os.path.join(home, ".claude", "handoff-guard.json")
if not os.path.exists(sample):
    shutil.copy(os.path.join(here, "config.example.json"), sample)
    print(f"Wrote default config to {sample}")
print("Done. Takes effect on your next prompt; no restart needed.")
