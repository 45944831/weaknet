# -*- coding: utf-8 -*-
"""操作序列矩阵：跨操作时序的回归守护（第 1 轮）。

矩阵（见 docs/op-matrix.md）把「有副作用的操作」两两组合，逐格问「先后执行
会怎样」。这里的每一条都对应一个真实用户动作序列，而不是单函数分支：

  * 运行中调参 → 停止 → 再开启：参数必须还在（否则前后两次测的不是同一套）；
  * 运行中切「直连」：开关必须真的关掉（否则界面显示弱网、实际零限制）；
  * 开启时目标组合已失效且没有历史场景：不能谎报「已开启」；
  * 停止态选场景：只记草稿、不碰流量（防回归）；
  * 组合播放中停止：不把「当前段参数」存成某个场景的草稿（会污染下次开启）。

两条红线不变：弱网开启不加访问码 / 鉴权，不增加任何测试门槛。
"""
import pytest

import weaknet as W
from conftest import reset_engine_state
from support import run_async

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _fresh():
    reset_engine_state()
    try:
        yield
    finally:
        reset_engine_state()


def _prof(**kw):
    p = W.default_profile()
    p.update(kw)
    return p


def test_runtime_tuned_params_survive_stop_then_start():
    """选 4G → 把延迟调到 800 → 停止 → 再开启，必须还是 800。

    旧行为：关闭时按 preset_key 重建，回到预设 60ms，用户刚调的值被静默覆盖。
    """
    async def go():
        await W.set_limit(True)
        await W.set_profile(_prof(latency_down_ms=800, name="调过"), "4g")
        assert W.G.profile["latency_down_ms"] == 800
        await W.set_limit(False)
        await W.set_limit(True)
        return W.G.profile["latency_down_ms"]

    assert run_async(go()) == 800


def test_switching_to_direct_scene_turns_switch_off():
    """运行中点「直连」这一档：开关必须关掉，而不是显示开启 + 零限制。"""
    async def go():
        await W.set_limit(True)
        await W.select_scene(_prof(name="直连（不限制）"), "direct")
        return W.G.limit_on, W.G.profile["latency_down_ms"]

    on, delay = run_async(go())
    assert on is False
    assert delay == 0


def test_enable_with_missing_combo_and_no_history_stays_off():
    """选中的组合被删、又没有历史场景可回落：开关不能停在「已开启」。"""
    W.G.preset_key = W.COMBO_KEY_PREFIX + "99"
    W.G.last_profile = None

    async def go():
        await W.set_limit(True)
        return W.G.limit_on

    assert run_async(go()) is False


def test_select_while_stopped_only_keeps_draft():
    """停止态选场景：只记草稿，不碰流量（这是「不偷偷开始测试」的底线）。"""
    async def go():
        await W.select_scene(_prof(latency_down_ms=650, name="草稿"), "4g")
        return (W.G.limit_on, W.G.profile["latency_down_ms"],
                dict(W.G.pending_profile or {}), W.G.pending_key)

    on, live_delay, draft, key = run_async(go())
    assert on is False
    assert live_delay == 0          # 引擎参数没动
    assert draft["latency_down_ms"] == 650
    assert key == "4g"


def test_stopped_draft_is_memory_only_across_restart(tmp_store):
    """W12 已知限制：停止态草稿只在内存，退出即丢，下次启动不恢复。

    用户停止后把延迟调到 800、**没点开启**就关程序，下次启动是直连默认值，
    不是 800。这是设计选择（进程内状态不跨进程），但必须在测试里钉死：

    * 草稿不能偷偷混进 settings —— 否则「只在内存」这句话就是假的；
    * 冷启动后 `pending_profile` 必须为空，避免上一条真被持久化了却没人发现。

    若将来真做持久化（要进 settings 并加版本迁移），这条会红：那时应改成
    断言「重启后草稿还在」，同时更新 docs/op-matrix.md 的 W12。
    """
    async def go():
        await W.select_scene(_prof(latency_down_ms=800, name="未开启"), "custom")
        return dict(W.G.pending_profile or {})

    assert run_async(go())["latency_down_ms"] == 800

    # 同一次运行里别的设置照常落盘，但不能顺手把草稿一起写进去
    d = W.load_settings()
    d["user_name"] = "tester"
    assert W.save_settings(d) is True
    saved = W.load_settings()
    assert saved["user_name"] == "tester"
    assert "pending_profile" not in saved and "pending_key" not in saved, \
        "草稿进了 settings，W12 的「只在内存」就不再成立"

    # 冷启动：G 回到默认（真实进程重启就是这个效果）
    reset_engine_state()
    assert W.G.pending_profile is None and W.G.pending_key == ""
    assert W.G.profile["latency_down_ms"] == 0, "草稿不该跨进程活下来"
    assert W.load_settings()["user_name"] == "tester", "设置本身仍应保留"


def test_stop_while_combo_does_not_cache_step_profile():
    """组合播放中关闭：当前段参数不能存成草稿，否则下次开启会盖掉播放器。"""
    ckey = W.COMBO_KEY_PREFIX + "1"

    async def go():
        await W.set_limit(True)
        await W.set_profile(_prof(latency_down_ms=300, name="组合段"), ckey)
        await W.set_limit(False)
        return W.G.pending_profile

    assert run_async(go()) is None
