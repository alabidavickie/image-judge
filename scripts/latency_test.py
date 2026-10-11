"""How long does one evaluation take, and does a faster setup still agree with you?

Judges the same labelled tasks, one at a time (like a labeller would), with a stopwatch, under each setup.
Nothing is saved to the database, and answers are never taken from the cache, so the times are real.

    ANTHROPIC_BASE_URL=https://api.mwapi.dev python scripts/latency_test.py --set-name "My tasks - test (seed 7)" --tasks 8

Setups are NAME:RUBRIC:EDGE:PIXELS, e.g. v3:v3:1568:1150000  (RUBRIC v3 = full, v3c = compact answer).
"""
import argparse
import asyncio
import statistics as st
import sys
import time
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.benchmark import load_task_images  # noqa: E402
from app.config import settings  # noqa: E402
from app.db import open_db  # noqa: E402
from app.judge import evaluate  # noqa: E402
from app.learn import set_tasks  # noqa: E402
from app.providers import RoutingJudge  # noqa: E402

DEFAULT_SETUPS = ["current:v3:1568:1150000", "compact:v3c:1568:1150000", "compact+small:v3c:1024:600000"]


def pick(tasks, n):
    """Half A and half B answers, skipping any task whose images cannot be read."""
    usable = [t for t in tasks if t.label in ("A", "B") and all(p.is_file() and p.stat().st_size > 0
                                                               for p in [*t.originals, t.a, t.b])]
    out = []
    for label in ("A", "B"):
        out += [t for t in usable if t.label == label][: n // 2]
    return out


async def run_setup(name, rubric, edge, pixels, tasks, judge, deadline):
    settings.image_max_edge, settings.image_max_pixels = edge, pixels
    cfg = replace(settings.judge, rubric_version=rubric)
    rows = []
    for t in tasks:
        task = load_task_images(t)  # resized under this setup's image size
        t0 = time.time()
        result = await evaluate(task, cfg, judge, db=None, use_cache=False, deadline_s=deadline)
        dt = time.time() - t0
        agg = result["aggregate"]
        usage = [r.get("usage") or {} for r in result["runs"] if r.get("usage")]
        rows.append({"s": dt, "status": agg["status"], "verdict": agg["verdict"], "label": t.label,
                     "in": [u.get("input_tokens", 0) for u in usage], "out": [u.get("output_tokens", 0) for u in usage],
                     "errors": sum(1 for r in result["runs"] if r.get("error"))})
        print(f"  {name:14} task {t.id}  {dt:5.0f}s  {agg['status']:9} {agg['verdict'] or '-'} (answer {t.label})"
              f"{'  errors:' + str(rows[-1]['errors']) if rows[-1]['errors'] else ''}", flush=True)
    return rows


def summary(name, rows):
    secs = sorted(r["s"] for r in rows)
    right = sum(r["verdict"] == r["label"] for r in rows)
    wrong_sure = sum(r["status"] == "confident" and r["verdict"] != r["label"] for r in rows)
    outs = [o for r in rows for o in r["out"]]
    ins = [i for r in rows for i in r["in"]]
    return (f"{name:14} median {st.median(secs):4.0f}s  slowest {secs[-1]:4.0f}s  "
            f"under 3 min {sum(s < 180 for s in secs)}/{len(secs)}  | agrees with you {right}/{len(rows)}  "
            f"confident-and-wrong {wrong_sure}  no-verdict {sum(r['verdict'] is None for r in rows)}  "
            f"errors {sum(r['errors'] for r in rows)} | tokens in {st.mean(ins) if ins else 0:,.0f} "
            f"out {st.mean(outs) if outs else 0:,.0f}")


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--set-name", required=True)
    ap.add_argument("--tasks", type=int, default=8)
    ap.add_argument("--setup", action="append", help="NAME:RUBRIC:EDGE:PIXELS (repeatable)")
    ap.add_argument("--deadline", type=float, default=170, help="seconds allowed per evaluation")
    args = ap.parse_args()

    db = open_db(settings)
    sid = next((s["id"] for s in db.list_sets() if s["name"] == args.set_name), None)
    if sid is None:
        sys.exit(f"No task set called {args.set_name!r}")
    _, tasks = set_tasks(db, sid, labeled_only=True)
    tasks = pick(tasks, args.tasks)
    print(f"{len(tasks)} tasks from '{args.set_name}', model {settings.judge.model}, "
          f"{settings.judge.runs} checks each, concurrency {settings.max_concurrency}\n", flush=True)

    judge, results = RoutingJudge(), {}
    for spec in args.setup or DEFAULT_SETUPS:
        name, rubric, edge, pixels = spec.split(":")
        print(f"== {name}: rubric {rubric}, images up to {edge}px / {int(pixels):,} pixels", flush=True)
        results[name] = await run_setup(name, rubric, int(edge), int(pixels), tasks, judge, args.deadline)
    print("\nSUMMARY")
    for name, rows in results.items():
        print(" ", summary(name, rows))


asyncio.run(main())
