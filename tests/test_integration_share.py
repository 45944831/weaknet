# -*- coding: utf-8 -*-
"""推送链路的端到端用例：发起方 -> 真实共享端口 -> 接收方入库。

重点覆盖 P0 的根因类型：Content-Length 是字节数，中文名字一字 3 字节。
接收方按字节读满才解析，任何一端把「字符数」当长度都会卡住或截断 ——
本用例用长中文名推一次，长度不匹配会直接失败而不是静默丢内容。

接收服务与推送必须跑在同一个事件循环里：run_async 结束时会把循环关掉，
先起服务再单独跑推送的话，第二次连接会因为循环已关闭而失败。
"""
import asyncio

import pytest

import weaknet as W
from support import run_async

pytestmark = pytest.mark.integration


async def push_to_local(peer, item_ids, combo_ids=None):
    """在本进程起一个真实共享端口，然后把内容推给它。"""
    srv = await asyncio.start_server(W.handle_share, "127.0.0.1", 0)
    try:
        target = {"ip": "127.0.0.1", "share": srv.sockets[0].getsockname()[1]}
        target.update(peer)
        return await W.push_to_peer(target, item_ids, combo_ids)
    finally:
        srv.close()
        await srv.wait_closed()


def test_push_roundtrip_with_long_chinese_name(tmp_store):
    W.G.user_name = "本机"
    W.G.share_pin = ""
    name = "地铁通勤弱网环境" * 3          # 27 个汉字 = 81 字节
    item, _ = W.upsert_user_preset(name, {"down_kbps": 500, "loss_pct": 3})

    res = run_async(push_to_local({"pin": ""}, [item["id"]]), timeout=20)

    assert res["ok"] is True, res
    # 本机与接收方是同一个临时库：同名一定进收件箱，内容必须完整到达
    assert [p["name"] for p in res["pending"]] == [item["name"]]
    rec = next(iter(W.G.inbox.values()))
    assert rec["name"] == item["name"]
    assert rec["profile"]["down_kbps"] == 500
    assert rec["profile"]["loss_pct"] == 3


def test_push_combo_roundtrip(tmp_store):
    W.G.user_name = "本机"
    W.G.share_pin = ""
    combo, _ = W.upsert_combo("通勤组合", [
        {"key": "wifi", "minutes": 1, "seconds": 0},
        {"key": "4g", "minutes": 0, "seconds": 30},
    ], loop=True)

    res = run_async(push_to_local({"pin": ""}, [], [combo["id"]]), timeout=20)

    assert res["ok"] is True, res
    assert [p["name"] for p in res["pending"]] == ["通勤组合"]
    rec = next(iter(W.G.inbox.values()))
    assert rec["kind"] == "combo"
    assert len(rec["combo"]["steps"]) == 2


def test_push_rejected_when_pin_mismatch(tmp_store):
    W.G.user_name = "本机"
    W.G.share_pin = "1234"
    item, _ = W.upsert_user_preset("带PIN环境", {"down_kbps": 300})

    res = run_async(push_to_local({"pin": "0000"}, [item["id"]]), timeout=20)

    assert res["ok"] is False
    assert "PIN" in res["error"], res
    assert not W.G.inbox, "被拒的推送不应进入收件箱"


def test_push_with_correct_pin_reaches_receiver(tmp_store):
    W.G.user_name = "本机"
    W.G.share_pin = "1234"
    item, _ = W.upsert_user_preset("带PIN环境", {"down_kbps": 300})

    res = run_async(push_to_local({"pin": "1234"}, [item["id"]]), timeout=20)

    assert res["ok"] is True, res
    assert W.G.inbox


def test_push_unknown_id_is_rejected_locally(tmp_store):
    res = run_async(W.push_to_peer({"ip": "127.0.0.1", "share": 9}, ["nope"]),
                    timeout=5)
    assert res["ok"] is False
    assert "没找到" in res["error"]
