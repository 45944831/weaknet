# -*- coding: utf-8 -*-
"""组合播放：引用失效的段不能把事件循环占住。

删掉自定义场景后，组合里的段会变成「空段」。循环组合如果整轮都没段可播，
播放器就会在 `while True` 里跳过唯一的等待点空转 —— 不是「播不动」，
而是整个 asyncio 事件循环被占死：代理不转发、控制接口不响应，
表现是「点了开始弱网，界面和 App 一起卡死」。这里锁住三件事：
  * 整轮无有效段 → 立刻退出并清理状态；
  * 开始播放前就拦住（不留一个空播放器）；
  * 保存 / 删场景时能说清哪些段受影响。
"""
import asyncio

import pytest

import weaknet as W

pytestmark = pytest.mark.unit

DEAD = [{"key": "u:00000001", "minutes": 1, "seconds": 0},
        {"key": "u:00000002", "minutes": 1, "seconds": 0}]


def _combo(steps=None, loop=True, cid="deadbeef"):
    return {"id": cid, "name": "全失效", "loop": loop,
            "steps": list(DEAD if steps is None else steps)}


def test_runner_exits_when_every_step_is_dead(engine):
    """整轮无有效段：必须自己退出，而不是把事件循环吃满。"""
    combo = _combo()

    async def _run():
        W.G.combo_task = asyncio.current_task()
        W.G.combo_id = combo["id"]
        W.G.combo_step = 0
        W.G.combo_deadline = 12345
        await W._combo_runner(combo)

    fut = asyncio.run_coroutine_threadsafe(_run(), engine.loop)
    # 空转的话事件循环被占住，这里会超时；正常退出则毫秒级返回
    fut.result(timeout=5)
    assert W.G.combo_task is None
    assert W.G.combo_id is None
    assert W.G.combo_step == -1
    assert W.G.combo_deadline == 0


def test_loop_keeps_serving_after_dead_combo(engine):
    """组合退出后事件循环还能正常调度（代理/控制接口依赖这一点）。"""
    combo = _combo()

    async def _run():
        W.G.combo_task = asyncio.current_task()
        await W._combo_runner(combo)
        await asyncio.sleep(0)

    asyncio.run_coroutine_threadsafe(_run(), engine.loop).result(timeout=5)
    # 再丢一个立即完成的任务：循环没被占住才能这么快回来
    asyncio.run_coroutine_threadsafe(asyncio.sleep(0), engine.loop).result(timeout=3)


def test_start_combo_refuses_all_dead(engine, monkeypatch):
    combo = _combo()
    monkeypatch.setattr(W, "find_combo", lambda cid: combo if str(cid) == combo["id"] else None)
    with pytest.raises(ValueError):
        asyncio.run_coroutine_threadsafe(
            W.start_combo(combo["id"]), engine.loop).result(timeout=5)
    assert W.G.combo_task is None


def test_select_combo_does_not_leave_dangling_preset(engine, monkeypatch):
    """选中一支播不了的组合：不能把 preset_key 改成它。"""
    combo = _combo()
    monkeypatch.setattr(W, "find_combo", lambda cid: combo if str(cid) == combo["id"] else None)
    before = W.G.preset_key
    with pytest.raises(ValueError):
        asyncio.run_coroutine_threadsafe(
            W.select_combo(combo["id"]), engine.loop).result(timeout=5)
    assert W.G.preset_key == before


# ------------------------------------------------------------ 引用校验

def test_combo_missing_refs_reports_bad_steps(engine):
    assert W.combo_missing_refs(DEAD) == [1, 2]
    # 内置档位是可解析的，不该被误报
    assert W.combo_missing_refs([{"key": "direct", "minutes": 1, "seconds": 0}]) == []
    assert W.combo_missing_refs([]) == []


def test_combo_playable_steps_mixed(engine):
    combo = _combo(steps=[{"key": "direct", "minutes": 1, "seconds": 0},
                          {"key": "u:00000001", "minutes": 1, "seconds": 0}])
    assert W.combo_playable_steps(combo) == [0]


def test_combos_using_scene_lists_affected(engine, monkeypatch):
    pid = "abcd1234"
    monkeypatch.setattr(W, "get_combos", lambda: [
        {"id": "c1", "name": "通勤", "loop": False,
         "steps": [{"key": "direct", "minutes": 1, "seconds": 0},
                   {"key": "u:" + pid, "minutes": 2, "seconds": 0}]},
        {"id": "c2", "name": "无关", "loop": False,
         "steps": [{"key": "direct", "minutes": 1, "seconds": 0}]},
    ])
    hits = W.combos_using_scene(pid)
    assert len(hits) == 1
    assert hits[0]["id"] == "c1" and hits[0]["steps"] == [2]
