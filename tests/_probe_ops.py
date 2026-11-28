# -*- coding: utf-8 -*-
"""操作序列矩阵探针（临时工具，不参与 pytest）。"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import weaknet as W  # noqa: E402


def reset():
    prof = W.G.profile
    prof.clear()
    prof.update(W.default_profile())
    W.G.preset_key = "direct"
    W.G.limit_on = False
    W.G.last_profile = None
    W.G.gate_open = True
    W.G.gen = 0
    W.G.flap_task = None
    W.G.combo_task = None
    W.G.combo_id = None
    W.G.combo_step = -1
    W.G.combo_deadline = 0
    W.G.test_seq = 0
    W.G.test_open = False
    W.G.test_started_at = 0.0
    W.G.test_base = {}
    W.G.test_fault_base = {}
    W.G.fault_cum.clear()
    W.G.pending_profile = None
    W.G.pending_key = ""
    W.G.combos = [
        {"id": "1", "name": "组合A", "loop": False,
         "steps": [{"key": "4g", "minutes": 0, "seconds": 1}]},
        {"id": "2", "name": "组合B", "loop": True,
         "steps": [{"key": "high_latency", "minutes": 0, "seconds": 1}]},
    ]


def prof_of(**kw):
    p = W.default_profile()
    p.update(kw)
    return p


def snap(tag):
    return "%-32s limit=%-5s key=%-10s delay=%-4s open=%s combo=%s" % (
        tag, W.G.limit_on, W.G.preset_key,
        W.G.profile.get("latency_down_ms"), W.G.test_open,
        W.G.combo_task is not None)


async def main():
    out = []

    # S1 用真实预设键：运行中调参 -> 关 -> 开
    reset()
    await W.set_limit(True)
    await W.set_profile(prof_of(latency_down_ms=800, name="调过"), "4g")
    out.append(snap("S1a 运行中改 4g 为 800"))
    await W.set_limit(False)
    await W.set_limit(True)
    out.append(snap("S1b 关→开（期望保留 800）"))

    # S6 运行中切「直连」
    reset()
    await W.set_limit(True)
    await W.select_scene(prof_of(name="直连（不限制）"), "direct")
    out.append(snap("S6 运行中切直连"))

    # S6b 切直连后再切回有内容场景
    reset()
    await W.set_limit(True)
    await W.select_scene(prof_of(name="直连（不限制）"), "direct")
    await W.select_scene(prof_of(latency_down_ms=200, name="回内容"), "4g")
    out.append(snap("S6b 直连→切回内容场景"))

    # S10 组合播放中手动切别的场景
    reset()
    await W.set_limit(True)
    await W.start_combo("1")
    out.append(snap("S10a 播放组合"))
    await W.select_scene(prof_of(latency_down_ms=120, name="手动"), "5g")
    out.append(snap("S10b 播放中手动切 5g"))

    # S11 停止态选组合 -> 开启
    reset()
    await W.select_combo("1")
    out.append(snap("S11a 停止态选组合"))
    await W.set_limit(True)
    out.append(snap("S11b 开启后"))
    await W.stop_combo()

    # S12 开启 -> 关闭 -> 组合还在吗
    reset()
    await W.set_limit(True)
    await W.start_combo("2")
    await W.set_limit(False)
    out.append(snap("S12 组合播放中关闭"))
    await W.stop_combo()

    # S13 组合被删（播放中）
    reset()
    await W.set_limit(True)
    await W.start_combo("1")
    W.G.combos = [c for c in W.G.combos if c["id"] != "1"]
    await asyncio.sleep(0.2)
    out.append(snap("S13 播放中删组合 0.2s 后"))
    await W.stop_combo()

    # S14 preset_key 指向已删组合 -> 开启
    reset()
    W.G.preset_key = W.COMBO_KEY_PREFIX + "99"
    await W.set_limit(True)
    out.append(snap("S14 开启时组合已不存在"))

    # S15 快速连点：True/False 并发
    reset()
    t1 = asyncio.create_task(W.set_limit(True))
    t2 = asyncio.create_task(W.set_limit(False))
    await asyncio.gather(t1, t2)
    out.append(snap("S15 并发 True+False（期望 False）"))

    # S16 连点 3 次 F/T/F
    reset()
    for on in (True, False, True):
        await asyncio.create_task(W.set_limit(on))
    out.append(snap("S16 顺序 T/F/T（期望 T）"))

    print("\n".join(out))


asyncio.run(main())
