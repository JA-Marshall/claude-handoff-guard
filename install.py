#!/usr/bin/env python3
"""Install claude-handoff-guard: copy the hook and register it in ~/.claude/settings.json."""
import json, os, shutil, sys, time

home = os.path.expanduser("~")
dest = os.path.join(home, ".claude", "hooks", "stale_context_guard.py")
settings = os.path.join(home, ".claude", "settings.json")
here = os.path.dirname(os.path.abspath(__file__))

os.makedirs(os.path.dirname(dest), exist_ok=True)
shutil.copy(os.path.join(here, "stale_context_guard.py"), dest)

cfg = {}
if os.path.exists(settings):
    shutil.copy(settings, f"{settings}.bak-{int(time.time())}")
    cfg = json.load(open(settings, encoding="utf8"))

cmd = f"python3 {dest}" if os.name != "nt" else f"python {dest.replace(os.sep, '/')}"
groups = cfg.setdefault("hooks", {}).setdefault("UserPromptSubmit", [])
if any("stale_context_guard.py" in h.get("command", "") for g in groups for h in g.get("hooks", [])):
    print("Hook already registered; script updated.")
else:
    groups.append({"hooks": [{"type": "command", "command": cmd, "timeout": 300}]})
    json.dump(cfg, open(settings, "w", encoding="utf8"), indent=2)
    print(f"Registered hook in {settings}")

sample = os.path.join(home, ".claude", "handoff-guard.json")
if not os.path.exists(sample):
    shutil.copy(os.path.join(here, "config.example.json"), sample)
    print(f"Wrote default config to {sample}")
print("Done. Takes effect on your next prompt; no restart needed.")
