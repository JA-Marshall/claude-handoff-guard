#!/usr/bin/env python3
"""How much of your Claude Code spend went on re-writing a cache that had expired?

Usage: python3 usage_report.py [--days N] [projects_dir ...]     (default: ~/.claude/projects)

Estimates API-list-price cost per assistant turn from the transcript's token usage, then
flags turns that came after the prompt cache had gone cold. For those turns the "avoidable"
part is the cache write minus what the same tokens would have cost as a warm cache read.
"""
import glob, json, os, sys, time
from collections import defaultdict
from datetime import datetime

# $ per 1M tokens: [input, cache read, output]; longest model-ID match wins.
PRICES = {
    "claude-fable-5-1": [10, 0.25, 50], "claude-mythos-5-1": [10, 0.25, 50], "claude-fable-5": [10, 1, 50],
    "claude-opus-5-5": [4, 0.20, 20], "claude-opus-5": [5, 0.50, 25], "claude-opus-4": [5, 0.50, 25],
    "claude-sonnet-5": [2, 0.20, 10], "claude-sonnet-4": [3, 0.30, 15], "claude-haiku-4-5": [1, 0.10, 5],
}

def price(model):
    for k in sorted(PRICES, key=len, reverse=True):
        if k in (model or ""):
            return PRICES[k]

def turns(path):
    """Yield (epoch, model, usage) per assistant message id, in order."""
    seen, order = {}, []
    for raw in open(path, encoding="utf8", errors="replace"):
        try:
            d = json.loads(raw)
        except ValueError:
            continue
        m = d.get("message")
        if d.get("type") != "assistant" or not isinstance(m, dict) or not m.get("usage") or not d.get("timestamp"):
            continue
        key = m.get("id") or d.get("uuid")
        if key not in seen:
            order.append(key)
        seen[key] = (datetime.fromisoformat(d["timestamp"].replace("Z", "+00:00")).timestamp(), m.get("model"), m["usage"])
    return [seen[k] for k in order]

def main():
    args = sys.argv[1:]
    since = 0.0
    if "--days" in args:
        i = args.index("--days")
        since = time.time() - float(args[i + 1]) * 86400
        del args[i:i + 2]
    dirs = args or [os.path.expanduser("~/.claude/projects")]
    total = cold_total = avoidable = 0.0
    cold_turns = n_turns = n_sessions = 0
    by_model = defaultdict(lambda: [0.0, 0.0])
    worst, done = [], set()
    for base in dirs:
        for f in glob.glob(os.path.join(base, "*", "*.jsonl")):
            if os.path.basename(f) in done:  # same session listed under two directories
                continue
            done.add(os.path.basename(f))
            t = [x for x in turns(f) if x[0] >= since]
            if not t:
                continue
            n_sessions += 1
            prev = None
            for ts, model, u in t:
                p = price(model)
                if not p:
                    prev = ts
                    continue
                inp, read, out = p
                cc = u.get("cache_creation") or {}
                w1 = cc.get("ephemeral_1h_input_tokens", 0) or 0
                w5 = cc.get("ephemeral_5m_input_tokens", 0) or 0
                if not (w1 or w5):
                    w1 = u.get("cache_creation_input_tokens", 0) or 0
                write = (w1 * 2 + w5 * 1.25) * inp / 1e6
                cost = ((u.get("input_tokens", 0) or 0) * inp + (u.get("cache_read_input_tokens", 0) or 0) * read
                        + (u.get("output_tokens", 0) or 0) * out) / 1e6 + write
                total += cost
                by_model[model][0] += cost
                n_turns += 1
                ttl = 3600 if w1 or not w5 else 300
                if prev is not None and ts - prev > ttl:
                    wtok = w1 + w5
                    extra = write - wtok * read / 1e6
                    cold_turns += 1
                    cold_total += write
                    avoidable += extra
                    by_model[model][1] += extra
                    worst.append((extra, os.path.basename(f)[:8], model, wtok, (ts - prev) / 60))
                prev = ts
    if not total:
        print("No usage found.")
        return
    print(f"Sessions: {n_sessions}   assistant turns: {n_turns}   est. list-price spend: ${total:,.2f}")
    print(f"Cold-cache restarts (first turn after the cache expired): {cold_turns}")
    print(f"  cache-write cost of those turns: ${cold_total:,.2f} ({cold_total / total:.1%} of spend)")
    print(f"  avoidable part (write minus a warm read): ${avoidable:,.2f} ({avoidable / total:.1%} of spend)")
    print("\nBy model (spend / avoidable):")
    for m, (c, a) in sorted(by_model.items(), key=lambda kv: -kv[1][0]):
        print(f"  {m or 'unknown':22} ${c:9,.2f} / ${a:8,.2f}  ({a / c:.1%})" if c else "")
    print("\nCostliest restarts:")
    for extra, sid, model, wtok, idle in sorted(worst, reverse=True)[:8]:
        print(f"  ${extra:6.2f}  {sid}  {model}  {wtok // 1000}k tokens after {idle / 60:.1f}h idle")
    print("\nNotes: list prices, not your plan's billing; subagent transcripts are not included.")

main()
