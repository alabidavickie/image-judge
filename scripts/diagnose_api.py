"""Where does the time go? Times a tiny text call, then ONE real judging check, and shows any error in full.

    ANTHROPIC_BASE_URL=https://api.mwapi.dev IMAGE_JUDGE_API_TIMEOUT=300 python scripts/diagnose_api.py --set-name "My tasks - test (seed 7)"
"""
import argparse
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anthropic  # noqa: E402

from app.benchmark import load_task_images  # noqa: E402
from app.config import settings  # noqa: E402
from app.db import open_db  # noqa: E402
from app.judge import AnthropicJudge  # noqa: E402
from app.learn import set_tasks  # noqa: E402


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--set-name", required=True)
    ap.add_argument("--rubric", default=settings.judge.rubric_version)
    args = ap.parse_args()
    judge = AnthropicJudge()
    cfg = settings.judge.with_overrides(rubric_version=args.rubric)
    print(f"model {cfg.model}, rubric {cfg.rubric_version}, base {judge.client.base_url}, "
          f"sdk retries {settings.api_max_retries}, timeout {settings.api_timeout_s:.0f}s\n", flush=True)

    t0 = time.time()
    try:
        r = await judge.client.messages.create(model=cfg.model, max_tokens=16,
                                               messages=[{"role": "user", "content": "Reply with the word OK."}])
        print(f"1) tiny text call: {time.time() - t0:.1f}s -> {r.content[0].text!r}", flush=True)
    except Exception as exc:  # show everything
        print(f"1) tiny text call FAILED after {time.time() - t0:.1f}s: {type(exc).__name__}: {exc}", flush=True)
        if hasattr(exc, "response"):
            print("   headers:", {k: v for k, v in exc.response.headers.items()
                                  if k.lower().startswith(("retry", "x-ratelimit", "ratelimit"))}, flush=True)

    db = open_db(settings)
    sid = next(s["id"] for s in db.list_sets() if s["name"] == args.set_name)
    _, tasks = set_tasks(db, sid, labeled_only=True)
    t = next(t for t in tasks if all(p.is_file() and p.stat().st_size > 0 for p in [*t.originals, t.a, t.b]))
    task = load_task_images(t)
    t0 = time.time()
    try:
        out = await judge.judge_once(task, cfg, swapped=False, notes=[])
        u = out.get("usage") or {}
        print(f"2) one full check: {time.time() - t0:.1f}s, tokens in {u.get('input_tokens')} out {u.get('output_tokens')}, "
              f"verdict {out['judgment']['verdict']} (answer {t.label})", flush=True)
    except Exception as exc:
        print(f"2) one full check FAILED after {time.time() - t0:.1f}s: {type(exc).__name__}: {exc}", flush=True)


asyncio.run(main())
