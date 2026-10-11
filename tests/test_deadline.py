"""The evaluation time limit: always answer in time, using whatever checks finished."""
import asyncio
import time

import pytest

from app.config import JudgeConfig
from app.images import prepare_image
from app.judge import Task, evaluate
from conftest import FakeJudge, make_judgment, png_bytes

CFG = JudgeConfig(model="fake", runs=2, rubric_version="v1", effort="high", use_fallbacks=False)


def task():
    return Task("Make the circle red", [prepare_image(png_bytes((40, 90, 200)))],
                prepare_image(png_bytes((220, 30, 30))), prepare_image(png_bytes((30, 160, 60))))


class SlowJudge(FakeJudge):
    """Answers at once for the normal image order, never in time for the swapped order."""

    def __init__(self, slow_swapped=True, slow_normal=False):
        super().__init__(lambda t, swapped: ("A", "high"))
        self.slow_swapped, self.slow_normal = slow_swapped, slow_normal

    async def judge_once(self, task, config, swapped, notes):
        if (swapped and self.slow_swapped) or (not swapped and self.slow_normal):
            await asyncio.sleep(30)
        return await super().judge_once(task, config, swapped, notes)


def test_a_finished_check_gives_a_leaning_when_time_runs_out():
    started = time.time()
    result = asyncio.run(evaluate(task(), CFG, SlowJudge(), deadline_s=0.5))
    assert time.time() - started < 5  # it did not wait for the slow check
    agg = result["aggregate"]
    assert agg["status"] == "review" and agg["verdict"] == "A"
    assert "time limit" in agg["explanation"] and "1/2" in agg["explanation"]
    assert [r["error"] for r in result["runs"] if r["error"]] == ["Did not finish within the 0s time limit."] or \
        any("time limit" in (r["error"] or "") for r in result["runs"])


def test_if_nothing_finishes_it_fails_in_time_with_a_clear_message():
    started = time.time()
    result = asyncio.run(evaluate(task(), CFG, SlowJudge(slow_normal=True), deadline_s=0.5))
    assert time.time() - started < 5
    assert result["aggregate"]["status"] == "error" and "time limit" in result["aggregate"]["explanation"]


def test_no_limit_means_it_waits_for_every_check():
    class Quick(FakeJudge):
        async def judge_once(self, task, config, swapped, notes):
            await asyncio.sleep(0.05)
            return await super().judge_once(task, config, swapped, notes)
    result = asyncio.run(evaluate(task(), CFG, Quick(lambda t, s: ("A", "high")), deadline_s=None))
    assert result["aggregate"]["status"] == "confident"


def test_a_generous_limit_changes_nothing():
    result = asyncio.run(evaluate(task(), CFG, FakeJudge(lambda t, s: ("A", "high")), deadline_s=60))
    assert result["aggregate"]["status"] == "confident" and not any(r["error"] for r in result["runs"])


def test_a_partial_answer_never_hides_a_disagreement():
    """Time running out must not turn two disagreeing checks into a verdict."""
    from app.aggregate import RunResult, aggregate
    runs = [RunResult(0, False, "A", "high", make_judgment("A")), RunResult(1, True, "B", "high", make_judgment("B"))]
    assert aggregate(runs, planned=2, allow_partial=True).status == "unclear"
    one = [RunResult(0, False, "A", "high", make_judgment("A")), RunResult(1, True, error="Did not finish")]
    assert aggregate(one, planned=2).status == "unclear"            # the old behaviour is unchanged
    assert aggregate(one, planned=2, allow_partial=True).status == "review"


def test_the_hosted_app_defaults_to_under_two_minutes(monkeypatch):
    from app.config import default_eval_deadline
    monkeypatch.setenv("IMAGE_JUDGE_AUTH", "1")
    monkeypatch.delenv("IMAGE_JUDGE_EVAL_DEADLINE", raising=False)
    assert default_eval_deadline() == 110
    monkeypatch.setenv("IMAGE_JUDGE_EVAL_DEADLINE", "90")
    assert default_eval_deadline() == 90
    monkeypatch.delenv("IMAGE_JUDGE_AUTH")
    monkeypatch.delenv("IMAGE_JUDGE_EVAL_DEADLINE")
    assert default_eval_deadline() == 0   # local use: no limit
