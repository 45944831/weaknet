# -*- coding: utf-8 -*-
"""参数 / 场景 / 组合的纯数据用例（不碰网络）。

这些数据是界面、引擎、脚本三处的共同输入，错了的典型表现是
「参数调不动」「组合第二段凭空消失」——都是静默的，只能靠用例钉住。
"""
import pytest

import weaknet as W

pytestmark = pytest.mark.unit


def test_default_profile_covers_every_spec_param():
    prof = W.default_profile()
    assert prof["name"] == W.DEFAULT_PROFILE_NAME
    for p in W.PARAM_SPECS:
        assert p["key"] in prof, p["key"]
        assert prof[p["key"]] == p["default"], p["key"]
        assert prof[p["key"]] <= p["max"], p["key"]


def test_has_limits_reflects_values_not_flag():
    assert W.has_limits(W.default_profile()) is False
    assert W.has_limits(W.build_preset_profile("wifi")) is True


def test_every_preset_profile_matches_its_table():
    for key, (name, _cat, extra) in W.PRESETS.items():
        prof = W.build_preset_profile(key)
        assert prof["name"] == name, key
        for k, v in extra.items():
            assert prof[k] == v, "%s.%s" % (key, k)


def test_unknown_preset_raises_keyerror():
    with pytest.raises(KeyError):
        W.build_preset_profile("no-such-preset")


def test_norm_combo_steps_drops_nested_combo_and_clamps():
    rejected = []
    raw = [
        {"key": "wifi", "minutes": 0, "seconds": 0},        # 时长为 0：丢掉
        {"key": "c:other", "minutes": 1},                   # 嵌套组合：拒收并记录
        {"key": "4g", "minutes": 9999, "seconds": 9999},    # 夹到单段上限
        {"key": "", "minutes": 1},                          # 没有场景：丢掉
    ] + [{"key": "wifi", "minutes": 1} for _ in range(10)]
    steps = W.norm_combo_steps(raw, rejected)
    assert len(steps) == W.MAX_COMBO_STEPS
    assert steps[0] == {"key": "4g", "minutes": W.STEP_MAX_MINUTES,
                        "seconds": W.STEP_MAX_SECONDS}
    assert [r["reason"] for r in rejected] == ["combo"]
    assert all(not s["key"].startswith(W.COMBO_KEY_PREFIX) for s in steps)


def test_step_duration_helpers():
    assert W.step_seconds({"minutes": 2, "seconds": 30}) == 150
    assert W.step_seconds({"minutes": 9999, "seconds": 9999}) == \
        W.STEP_MAX_MINUTES * 60 + W.STEP_MAX_SECONDS
    assert W.step_text({"minutes": 2, "seconds": 30}) == "2 分 30 秒"
    assert W.step_text({"minutes": 5}) == "5 分"
    assert W.step_text({"seconds": 30}) == "30 秒"


def test_split_param_note_always_two_parts():
    for key, note in W.PARAM_NOTES.items():
        role, tips = W.split_param_note(note)
        assert role, key
        assert "\n" not in role, key
        assert tips, key


def test_user_preset_roundtrip_clamps_values(tmp_store):
    item, items = W.upsert_user_preset(
        "很长的中文名字" * 4,
        {"down_kbps": 10 ** 9, "loss_pct": -5, "bogus": 123},
    )
    assert item in items
    assert item["profile"]["down_kbps"] == W.PROF_LIMITS["down_kbps"]
    assert item["profile"]["loss_pct"] == 0
    assert "bogus" not in item["profile"]
    assert len(item["name"]) <= 40

    prof = W.build_user_profile("u:" + item["id"])
    assert prof["name"] == item["name"]
    assert prof["down_kbps"] == W.PROF_LIMITS["down_kbps"]

    other, _ = W.upsert_user_preset(item["name"], {})
    assert other["name"] != item["name"], "同名没有自动加序号"

    ok, left = W.delete_user_preset(item["id"])
    assert ok is True
    assert all(x["id"] != item["id"] for x in left)


def test_norm_user_profile_rejects_bool_and_unknown():
    prof = W._norm_user_profile("x", {"loss_pct": True, "nope": 5})
    assert prof["loss_pct"] == 0
    assert "nope" not in prof
