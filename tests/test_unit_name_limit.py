# -*- coding: utf-8 -*-
"""自定义场景 / 弱网组合「取名最多 10 个字符」的纯逻辑回归。

上限来自 spec/spec.json 的 name_max（Windows 直读、Android 生成 SpecGen.NAME_MAX）。
这里把三层都钉住：规格同源 → 引擎所有写入口 → 老存档与共享这些没有输入框的入口。
"""
import json
import re
from pathlib import Path

import pytest

import weaknet as W

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parent.parent
SPEC_GEN = ROOT / "android" / "src" / "com" / "weaknet" / "app" / "SpecGen.java"

LONG = "一二三四五六七八九十多余"


def test_spec_is_single_source():
    assert W.SPEC["name_max"] == 10
    assert W.NAME_MAX == 10
    m = re.search(r"public\s+static\s+final\s+int\s+NAME_MAX\s*=\s*(\d+)\s*;",
                  SPEC_GEN.read_text(encoding="utf-8"))
    assert m, "SpecGen.java 里没有 NAME_MAX，忘了跑 tools/gen_spec.py？"
    assert int(m.group(1)) == W.NAME_MAX


def test_limit_item_name():
    assert W.limit_item_name("  地铁 \n 早高峰 \t 卡顿 ") == "地铁 早高峰 卡顿"
    assert W.limit_item_name(LONG) == "一二三四五六七八九十"
    assert len(W.limit_item_name(LONG)) == W.NAME_MAX
    assert W.limit_item_name("   ", "兜底") == "兜底"
    assert W.limit_item_name(None, "兜底") == "兜底"
    assert W.limit_item_name("", LONG + LONG) == "一二三四五六七八九十"
    # emoji 按码点算 1 个字符，与 Android NameLimit.trim 一致
    assert W.limit_item_name("😀" * 13) == "😀" * 10
    assert len(W.limit_item_name("😀" * 13)) == W.NAME_MAX


def test_default_names_fit():
    for d in list(W.DEFAULT_SCENES) + list(W.DEFAULT_COMBOS):
        assert 0 < len(d["name"]) <= W.NAME_MAX, d["name"]


def test_upsert_truncates_and_dedupes_within_limit(tmp_store):
    it, _ = W.upsert_user_preset(LONG, {"down_kbps": 100})
    assert it["name"] == "一二三四五六七八九十"
    it2, _ = W.upsert_user_preset(LONG, {"down_kbps": 200})
    assert it2["name"] != it["name"] and it2["name"].endswith("(2)")
    assert len(it2["name"]) <= W.NAME_MAX, "重名加序号反而造出了超长名字"

    steps = [{"key": "4g_weak", "minutes": 0, "seconds": 30}]
    c, _ = W.upsert_combo("通勤通勤通勤通勤通勤通勤通勤", steps)
    assert c is not None and len(c["name"]) <= W.NAME_MAX
    assert len(c["name"]) == 10


def test_old_store_long_names_are_trimmed(tmp_store, monkeypatch):
    store = tmp_store / "weaknet_presets.json"
    store.write_text(json.dumps({
        "version": 1,
        "seed": W.DEFAULT_SEED,
        "items": [
            {"id": "a", "name": "一二三四五六七八九十A", "profile": {}},
            {"id": "b", "name": "一二三四五六七八九十B", "profile": {}},
        ],
        "combos": [
            {"id": "c1", "name": "组合名超长超长超长超长A",
             "steps": [{"key": "4g_weak", "seconds": 30}]},
            {"id": "c2", "name": "组合名超长超长超长超长B",
             "steps": [{"key": "4g_weak", "seconds": 30}]},
        ],
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(W.G, "user_presets", None)
    monkeypatch.setattr(W.G, "combos", None)

    items = W.get_user_presets()
    combos = W.get_combos()
    assert len(items) == 2 and len(combos) == 2
    assert all(len(x["name"]) <= W.NAME_MAX for x in items + combos)
    assert len({x["name"] for x in items}) == 2, "截断后撞名没加序号"
    assert len({c["name"] for c in combos}) == 2, "截断后撞名没加序号"


def test_share_inbox_rename_respects_limit(tmp_store):
    W.G.inbox["i1"] = {"from": "某人", "kind": "preset",
                       "name": "原名", "profile": {}}
    r1 = W.resolve_inbox("i1", "rename", LONG)
    assert r1["ok"] and len(r1["name"]) == W.NAME_MAX

    W.G.inbox["i2"] = {"from": "某人", "kind": "combo", "name": "组合",
                       "combo": {"loop": False,
                                 "steps": [{"key": "4g_weak", "seconds": 30}]}}
    r2 = W.resolve_inbox("i2", "rename", LONG + LONG)
    assert r2["ok"] and len(r2["name"]) <= W.NAME_MAX


def test_scene_label_is_not_a_user_name():
    """内置预设显示名最长 14 字，段里的场景标签不能按 10 字截（跨端反查会断）"""
    longest = max(len(v[0]) for v in W.PRESETS.values())
    assert longest > W.NAME_MAX
    label = W.PRESETS["wifi_weak"][0]
    assert W._share_clean(label) == label
