# -*- coding: utf-8 -*-
"""共享协议的数据规则：鉴权、同名进收件箱、旧版只读降级、收件箱满。

这些规则决定「别人推来的东西会不会悄悄覆盖你调好的场景」，
默认必须是保守的：同名不覆盖、旧版不强收、满了要回执。
"""
import pytest

import weaknet as W

pytestmark = pytest.mark.unit


def payload(items=None, combos=None, name="同事", ver=2, pin=""):
    return {"ver": ver, "pin": pin, "from": name, "from_id": "peer1",
            "items": items or [], "combos": combos or []}


def names():
    return [it["name"] for it in W.get_user_presets()]


# ---------------------------------------------------------------- 鉴权

def test_auth_rejects_legacy_without_optin(tmp_store):
    W.G.allow_legacy = False
    err = W.share_auth_error({"ver": 1})
    assert err is not None and err[0] == 409


def test_auth_accepts_legacy_when_opted_in(tmp_store):
    W.G.allow_legacy = True
    assert W.share_auth_error({"ver": 1}) is None


def test_auth_requires_matching_pin(tmp_store):
    W.G.allow_legacy = False
    W.G.share_pin = "1234"
    err = W.share_auth_error({"ver": 2, "pin": "0000"})
    assert err is not None and err[0] == 403
    assert W.share_auth_error({"ver": 2, "pin": "1234"}) is None


def test_auth_rejects_when_local_pin_missing(tmp_store):
    """S3：本机没设 PIN 时不能 fail-open 放行，必须 409 并提示去设置。"""
    W.G.share_pin = ""
    err = W.share_auth_error({"ver": 2})
    assert err is not None and err[0] == 409
    assert "PIN" in err[1]


# ---------------------------------------------------------------- 入库规则

def test_new_name_is_accepted_directly(tmp_store):
    res = W.receive_share(payload(items=[
        {"name": "弱网-A", "profile": {"down_kbps": 300}}]))
    assert res["ok"] is True
    assert res["accepted"] == ["弱网-A"]
    assert names() == ["弱网-A"]
    assert W.get_user_presets()[0]["profile"]["down_kbps"] == 300


def test_same_name_goes_to_inbox_not_overwrite(tmp_store):
    W.upsert_user_preset("弱网-A", {"down_kbps": 100})
    res = W.receive_share(payload(items=[
        {"name": "弱网-A", "profile": {"down_kbps": 999}}]))
    assert res["accepted"] == []
    assert [p["name"] for p in res["pending"]] == ["弱网-A"]
    assert len(W.G.inbox) == 1
    assert W.get_user_presets()[0]["profile"]["down_kbps"] != 999


def test_legacy_push_never_enters_library(tmp_store):
    res = W.receive_share(payload(ver=1, items=[
        {"name": "陌生人", "profile": {}}]), only_inbox=True)
    assert res["accepted"] == []
    assert res["pending"], "旧版推送应进收件箱等确认"
    assert names() == []
    assert len(W.G.inbox) == 1


def test_inbox_full_reports_dropped(tmp_store, monkeypatch):
    monkeypatch.setattr(W, "MAX_INBOX", 1)
    W.upsert_user_preset("甲", {})
    W.upsert_user_preset("乙", {})
    res = W.receive_share(payload(items=[{"name": "甲"}, {"name": "乙"}]))
    assert len(res["pending"]) == 1
    assert len(res["dropped"]) == 1
    assert res["dropped"][0]["reason"] == "收件箱已满"


def test_empty_push_is_rejected(tmp_store):
    res = W.receive_share(payload())
    assert res["ok"] is False
    assert res["error"]


# ---------------------------------------------------------------- 收件箱处理

def test_resolve_keep_keeps_local(tmp_store):
    W.upsert_user_preset("本地", {"down_kbps": 100})
    iid = W.receive_share(payload(items=[
        {"name": "本地", "profile": {"down_kbps": 900}}]))["pending"][0]["id"]
    res = W.resolve_inbox(iid, "keep")
    assert res["ok"] is True and res["action"] == "keep"
    assert W.get_user_presets()[0]["profile"]["down_kbps"] == 100


def test_resolve_overwrite_replaces_local(tmp_store):
    W.upsert_user_preset("本地", {"down_kbps": 100})
    iid = W.receive_share(payload(items=[
        {"name": "本地", "profile": {"down_kbps": 900}}]))["pending"][0]["id"]
    res = W.resolve_inbox(iid, "overwrite")
    assert res["ok"] is True and res["action"] == "overwrite"
    assert W.get_user_presets()[0]["profile"]["down_kbps"] == 900


def test_resolve_rename_saves_alongside(tmp_store):
    W.upsert_user_preset("本地", {"down_kbps": 100})
    iid = W.receive_share(payload(items=[
        {"name": "本地", "profile": {"down_kbps": 900}}]))["pending"][0]["id"]
    res = W.resolve_inbox(iid, "rename", "本地-副本")
    assert res["ok"] is True and res["name"] == "本地-副本"
    got = {it["name"]: it["profile"]["down_kbps"] for it in W.get_user_presets()}
    assert got == {"本地": 100, "本地-副本": 900}


def test_resolve_unknown_id_is_reported(tmp_store):
    res = W.resolve_inbox("nope", "keep")
    assert res["ok"] is False and res["error"]


def test_combo_with_local_steps_is_accepted(tmp_store):
    res = W.receive_share(payload(combos=[
        {"name": "通勤", "loop": False,
         "steps": [{"key": "wifi", "minutes": 1, "seconds": 0},
                   {"key": "4g", "minutes": 0, "seconds": 30}]}]))
    assert res["accepted"] == [W.COMBO_SHARE_MARK + "通勤"]
    got = [c for c in W.get_combos() if c["name"] == "通勤"]
    assert got and len(got[0]["steps"]) == 2


def test_combo_with_no_local_steps_is_dropped(tmp_store):
    res = W.receive_share(payload(combos=[
        {"name": "空组合", "steps": [{"key": "no-such-scene", "minutes": 1}]}]))
    assert res["accepted"] == []
    assert res["dropped"] and res["dropped"][0]["name"] == "空组合"


# ---------------------------------------------------------------- PIN 退避（S3）

def test_pin_failures_lock_source_after_threshold(tmp_store):
    """4 位 PIN 只有 1 万种组合，没有退避等于可以穷举。"""
    W.G.share_pin = "1234"
    W.G.share_fail.clear()
    ip = "192.168.1.50"
    for _ in range(W.SHARE_FAIL_FREE - 1):
        err = W.share_auth_error({"ver": 2, "pin": "0000"}, peer_ip=ip)
        assert err is not None and err[0] == 403

    err = W.share_auth_error({"ver": 2, "pin": "0000"}, peer_ip=ip)
    assert err is not None and err[0] == 429 and "锁定" in err[1]

    # 锁定期内即使 PIN 正确也拒绝——否则先锁再猜对就能绕过退避
    err = W.share_auth_error({"ver": 2, "pin": "1234"}, peer_ip=ip)
    assert err is not None and err[0] == 429

    # 退避只针对来源，别把正常设备一起关在门外
    assert W.share_auth_error({"ver": 2, "pin": "1234"},
                              peer_ip="192.168.1.51") is None


def test_pin_success_clears_failure_counter(tmp_store):
    W.G.share_pin = "1234"
    W.G.share_fail.clear()
    ip = "10.0.0.9"
    for _ in range(3):
        assert W.share_auth_error({"ver": 2, "pin": "0000"}, peer_ip=ip)[0] == 403
    assert W.share_auth_error({"ver": 2, "pin": "1234"}, peer_ip=ip) is None
    assert W.share_throttle_left(ip) == 0
    assert ip not in W.G.share_fail


def test_pin_backoff_does_not_break_loopback_without_ip(tmp_store):
    """没有来源 IP（单测/嵌入式调用）时不能误锁，行为保持原样。"""
    W.G.share_pin = "1234"
    W.G.share_fail.clear()
    for _ in range(10):
        assert W.share_auth_error({"ver": 2, "pin": "0000"})[0] == 403
