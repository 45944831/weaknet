# -*- coding: utf-8 -*-
"""出厂默认（5 支自定义场景 + 5 支弱网组合）的纯数据用例。

默认项来自 spec/spec.json 的 defaults 段：Windows 直读、Android 由
tools/gen_spec.py 生成 SpecGen.java 常量。这里把「数据本身合法」和
「两端拿到的是同一份」都钉住，避免以后只改了一端导致双端名字漂移。
"""
import json
import re
from pathlib import Path

import pytest

import weaknet as W

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parent.parent
SPEC_GEN = ROOT / "android" / "src" / "com" / "weaknet" / "app" / "SpecGen.java"


def _restart(store, monkeypatch):
    """模拟进程重启：只保留磁盘，清掉内存缓存与一次性闸门"""
    monkeypatch.setattr(W, "_seeded", False)
    monkeypatch.setattr(W.G, "user_presets", None)
    monkeypatch.setattr(W.G, "combos", None)
    return store


# ---------------------------------------------------------------- 规格合法性

def test_defaults_count_and_names():
    assert len(W.DEFAULT_SCENES) == 5
    assert len(W.DEFAULT_COMBOS) == 5
    assert W.DEFAULT_SEED
    sn = [s["name"] for s in W.DEFAULT_SCENES]
    cn = [c["name"] for c in W.DEFAULT_COMBOS]
    assert len(set(sn)) == 5 and len(set(cn)) == 5
    assert all(0 < len(n) <= W.NAME_MAX and n.strip() == n for n in sn + cn)


def test_default_scene_params_within_spec_limits():
    limits = {p["key"]: p["max"] for p in W.PARAM_SPECS}
    for s in W.DEFAULT_SCENES:
        params = s["params"]
        assert params, s["name"]
        for k, v in params.items():
            assert k in limits, "%s.%s 不是合法参数" % (s["name"], k)
            assert 0 <= v <= limits[k], "%s.%s 越界" % (s["name"], k)
        # 至少得真的施加了弱网，别混一个「直连」进来充数
        assert W.has_limits(W._norm_user_profile(s["name"], params)), s["name"]


def test_defaults_never_mention_retired_presets():
    """2026-10 已下线的 2G EDGE / 2G GPRS / 56K 拨号 / 3G 不许回到默认项。

    现网早已退网，留着（哪怕只是名字或组合说明里的示例）会让人误以为
    还有这些内置档位。要复现极慢网就直接压带宽，见 docs/guide.md。
    """
    retired = ("2G", "3G", "EDGE", "GPRS", "56K", "拨号")
    texts = []
    for s in W.DEFAULT_SCENES:
        texts.append("默认场景 " + s["name"] + " " + s.get("desc", ""))
    for c in W.DEFAULT_COMBOS:
        texts.append("默认组合 " + c["name"] + " " + c.get("desc", ""))
        for st in c["steps"]:
            assert st["key"] in W.PRESETS, "%s 引用了不在位的场景 %s" % (c["name"], st["key"])
    # 组合说明里的历史示例也算「默认文案」，一起盯住
    texts.append("组合文档 " + json.dumps(W.SPEC["combo"], ensure_ascii=False))
    for t in texts:
        hits = [w for w in retired if w in t.upper() or w in t]
        assert not hits, "出现已下线档位 %s：%s" % (hits, t[:80])


def test_default_combos_only_reference_builtin_presets():
    for c in W.DEFAULT_COMBOS:
        assert isinstance(c["loop"], bool), c["name"]
        steps = c["steps"]
        assert 0 < len(steps) <= W.MAX_COMBO_STEPS, c["name"]
        for st in steps:
            assert st["key"] in W.PRESETS, "%s 引用了不存在的场景 %s" % (c["name"], st["key"])
            assert not st["key"].startswith(W.COMBO_KEY_PREFIX)
            assert W.step_seconds(st) > 0, "%s 有时长为 0 的段" % c["name"]
        # 归一化不能丢段：丢一段的话用户看到的和 spec 写的就不是一回事
        assert len(W.norm_combo_steps(steps)) == len(steps), c["name"]


# ---------------------------------------------------------------- 双端同源

def _java_string(src, name):
    """取 SpecGen.java 里 public static final String 常量的值。

    源码里的中文是 \\uXXXX（生成器 literal() 的产物，Java 编译器会解成字符），
    Python 的 json.loads 同样认这种转义，所以能直接还原成运行时字符串。
    """
    m = re.search(r"public\s+static\s+final\s+String\s+" + re.escape(name)
                  + r"\s*=\s*(\"(?:[^\"\\]|\\.)*\")\s*;", src)
    assert m, "SpecGen.java 里找不到常量 " + name
    return json.loads(m.group(1))


def test_android_generated_defaults_match_spec():
    src = SPEC_GEN.read_text(encoding="utf-8")
    assert _java_string(src, "DEFAULT_SEED") == W.DEFAULT_SEED
    scenes = json.loads(_java_string(src, "DEFAULT_SCENES_JSON"))
    combos = json.loads(_java_string(src, "DEFAULT_COMBOS_JSON"))
    assert scenes == W.DEFAULT_SCENES
    assert combos == W.DEFAULT_COMBOS


# ---------------------------------------------------------------- Windows 注入

def test_fresh_store_gets_all_defaults(tmp_store, monkeypatch):
    monkeypatch.setattr(W, "_seeded", False)
    store = tmp_store / "weaknet_presets.json"
    assert not store.exists()

    items, combos = W.get_user_presets(), W.get_combos()
    assert [x["name"] for x in items] == [s["name"] for s in W.DEFAULT_SCENES]
    assert [c["name"] for c in combos] == [c["name"] for c in W.DEFAULT_COMBOS]
    assert store.exists()

    data = json.loads(store.read_text(encoding="utf-8"))
    assert data["seed"] == W.DEFAULT_SEED
    assert len(data["items"]) == 5 and len(data["combos"]) == 5

    # 参数真的能用：按 key 反查得到的是 spec 里那组值，不是默认直连
    prof = W.build_user_profile("u:" + items[0]["id"])
    assert prof["down_kbps"] == W.DEFAULT_SCENES[0]["params"]["down_kbps"]
    assert prof["name"] == W.DEFAULT_SCENES[0]["name"]
    # 组合摘要里的场景名能解析（段 key 全是内置预设）
    first = W.DEFAULT_COMBOS[0]["steps"][0]["key"]
    assert W.PRESETS[first][0] in W.combo_summary(combos[0])


def test_deleted_defaults_do_not_come_back(tmp_store, monkeypatch):
    monkeypatch.setattr(W, "_seeded", False)
    W.get_user_presets()
    W.get_combos()
    W.delete_user_preset(W.get_user_presets()[0]["id"])
    W.delete_combo(W.get_combos()[0]["id"])

    store = _restart(tmp_store / "weaknet_presets.json", monkeypatch)
    assert len(W.get_user_presets()) == 4
    assert len(W.get_combos()) == 4
    assert json.loads(store.read_text(encoding="utf-8"))["seed"] == W.DEFAULT_SEED


def test_seed_preserves_existing_data_and_skips_duplicate_names(tmp_store, monkeypatch):
    monkeypatch.setattr(W, "_seeded", False)
    store = tmp_store / "weaknet_presets.json"
    store.write_text(json.dumps({
        "version": 1,
        "items": [{"id": "old1", "name": "我的老场景", "profile": {}},
                  {"id": "old2", "name": W.DEFAULT_SCENES[0]["name"], "profile": {}}],
        "combos": [],
    }, ensure_ascii=False), encoding="utf-8")
    _restart(store, monkeypatch)

    items, combos = W.get_user_presets(), W.get_combos()
    assert items[0]["name"] == "我的老场景"
    assert sum(x["name"] == W.DEFAULT_SCENES[0]["name"] for x in items) == 1
    assert len(items) == 6, "老场景 + 4 支新默认（同名的那支跳过）"
    assert len(combos) == 5


def test_broken_store_still_seeds(tmp_store, monkeypatch):
    monkeypatch.setattr(W, "_seeded", False)
    store = tmp_store / "weaknet_presets.json"
    store.write_text("{broken", encoding="utf-8")
    _restart(store, monkeypatch)
    assert len(W.get_user_presets()) == 5
    assert len(W.get_combos()) == 5
    assert json.loads(store.read_text(encoding="utf-8"))["seed"] == W.DEFAULT_SEED
