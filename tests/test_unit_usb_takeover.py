# -*- coding: utf-8 -*-
"""USB 手机接管：前态保存、分阶段回滚、还原归属。

手机上原来可能就有代理（公司 Wi-Fi 的网关代理、用户自己填的），而反向映射是
「谁都能建、谁都能删」的共享资源。所以每条用例都在问同一个问题：**这一步失败
之后，手机是不是回到了动手之前的样子？**

全部用假 adb（内存里的 settings + reverse 表），不连真机。
"""
import pytest

import usb_phone as UP

pytestmark = pytest.mark.unit

SERIAL = "TESTSERIAL"
PORT = 8888
PROXY = "127.0.0.1:8888"


class FakeDevice:
    """一台假手机：settings 三个字段 + reverse 映射表 + 可注入的失败点。"""

    def __init__(self, http_proxy="null", host="null", port="null"):
        self.settings = {"http_proxy": http_proxy,
                         "global_http_proxy_host": host,
                         "global_http_proxy_port": port}
        self.reverse = []            # [(local, remote)]
        self.fail = set()            # {"reverse_add", "reverse_list", "put", "get"}
        self.calls = []

    # -- 辅助 --
    def _list_text(self):
        return "\n".join("host-1 %s %s" % (a, b) for a, b in self.reverse)

    def run(self, _adb, args, timeout=20):
        a = [str(x) for x in args]
        self.calls.append(a)
        if "reverse" in a:
            i = a.index("reverse")
            rest = a[i + 1:]
            if rest == ["--list"]:
                if "reverse_list" in self.fail:
                    return 1, "", "error: closed"
                return 0, self._list_text(), ""
            if rest and rest[0] == "--remove":
                want = rest[1]
                if "reverse_remove" in self.fail:
                    return 1, "", "error: not found"
                self.reverse = [p for p in self.reverse if p[0] != want]
                return 0, "", ""
            if len(rest) == 2:
                if "reverse_add" in self.fail:
                    return 1, "", "error: cannot bind listener: address already in use"
                self.reverse = [p for p in self.reverse if p[0] != rest[0]]
                self.reverse.append((rest[0], rest[1]))
                return 0, "", ""
            return 0, "", ""
        if "settings" in a:
            i = a.index("settings")
            rest = a[i + 1:]
            if rest[:2] == ["get", "global"]:
                if "get" in self.fail:
                    return 1, "", "error: closed"
                return 0, self.settings.get(rest[2], "null"), ""
            if rest[:2] == ["put", "global"]:
                if "put" in self.fail:
                    return 1, "", "error: not allowed"
                self.settings[rest[2]] = rest[3]
                return 0, "", ""
            if rest[:2] == ["delete", "global"]:
                self.settings.pop(rest[2], None)
                return 0, "", ""
            return 0, "", ""
        return 0, "", ""


@pytest.fixture
def dev(monkeypatch, tmp_path):
    d = FakeDevice()
    monkeypatch.setattr(UP, "run", d.run)
    monkeypatch.setattr(UP, "_state_file", lambda: str(tmp_path / UP.STATE_NAME))
    monkeypatch.setattr(UP, "start_watch", lambda *a, **k: {"ok": True, "pid": 1})
    monkeypatch.setattr(UP, "stop_watch", lambda *a, **k: {"ok": True})
    return d


# ------------------------------------------------------------ 前态保存

def test_connect_saves_prev_proxy_and_restores_it(dev):
    """接管前有公司代理 → 断开要还原它，而不是清空。"""
    dev.settings["http_proxy"] = "10.0.0.9:3128"
    r = UP.connect("adb", SERIAL, PORT)
    assert r["ok"] is True, r
    assert dev.settings["http_proxy"] == PROXY
    rec = UP.record_for(SERIAL)
    assert rec and rec["prev"]["http_proxy"] == "10.0.0.9:3128"
    assert r["restore_to"] == "10.0.0.9:3128"

    r2 = UP.disconnect("adb", SERIAL)
    assert r2["state"] == "restored", r2
    assert dev.settings["http_proxy"] == "10.0.0.9:3128"
    assert UP.record_for(SERIAL) is None


def test_connect_clears_when_no_prev_proxy(dev):
    r = UP.connect("adb", SERIAL, PORT)
    assert r["ok"] is True, r
    r2 = UP.disconnect("adb", SERIAL)
    assert r2["state"] == "restored", r2
    assert UP.cleared(dev.settings.get("http_proxy", ""))


def test_connect_removes_compat_fields(dev):
    dev.settings["global_http_proxy_host"] = "10.0.0.9"
    dev.settings["global_http_proxy_port"] = "3128"
    assert UP.connect("adb", SERIAL, PORT)["ok"] is True
    assert "global_http_proxy_host" not in dev.settings
    assert "global_http_proxy_port" not in dev.settings


def test_disconnect_restores_compat_fields(dev):
    dev.settings["global_http_proxy_host"] = "10.0.0.9"
    dev.settings["global_http_proxy_port"] = "3128"
    dev.settings["http_proxy"] = "10.0.0.9:3128"
    assert UP.connect("adb", SERIAL, PORT)["ok"] is True
    assert UP.disconnect("adb", SERIAL)["state"] == "restored"
    assert dev.settings["global_http_proxy_host"] == "10.0.0.9"
    assert dev.settings["global_http_proxy_port"] == "3128"


# ------------------------------------------------------------ 不覆盖别人的映射

def test_connect_refuses_conflicting_reverse(dev):
    """手机 localhost:P 已经指向别处 → 拒绝，且不动代理设置。"""
    dev.reverse = [("tcp:%d" % PORT, "tcp:9999")]
    before = dict(dev.settings)
    r = UP.connect("adb", SERIAL, PORT)
    assert r["ok"] is False and r["stage"] == "reverse"
    assert dev.settings == before
    assert UP.record_for(SERIAL) is None


def test_connect_adopts_identical_reverse_without_owning_it(dev):
    """已经存在 tcp:P → tcp:P：沿用，但记下「不是我建的」，断开时不要删。"""
    dev.reverse = [("tcp:%d" % PORT, "tcp:%d" % PORT)]
    r = UP.connect("adb", SERIAL, PORT)
    assert r["ok"] is True, r
    assert UP.record_for(SERIAL)["reverse_created"] is False
    UP.disconnect("adb", SERIAL)
    assert ("tcp:%d" % PORT, "tcp:%d" % PORT) in dev.reverse


def test_disconnect_removes_only_own_reverse(dev):
    assert UP.connect("adb", SERIAL, PORT)["ok"] is True
    assert ("tcp:%d" % PORT, "tcp:%d" % PORT) in dev.reverse
    UP.disconnect("adb", SERIAL)
    assert dev.reverse == []


# ------------------------------------------------------------ 分阶段回滚

def test_reverse_list_failure_after_create_rolls_back(dev):
    """映射建好了但回读失败 → 撤掉刚建的映射，别留残骸。"""
    dev.fail.add("reverse_list")
    r = UP.connect("adb", SERIAL, PORT)
    assert r["ok"] is False and r["stage"] == "reverse"
    assert dev.reverse == [], "建了又读不回来，必须撤掉"
    assert UP.record_for(SERIAL) is None


def test_settings_failure_rolls_back_proxy_and_reverse(dev):
    """代理写不进去 → 代理字段还原、映射撤掉、记录删掉。"""
    dev.settings["http_proxy"] = "10.0.0.9:3128"
    dev.fail.add("put")
    r = UP.connect("adb", SERIAL, PORT)
    assert r["ok"] is False and r["stage"] == "settings"
    assert dev.settings["http_proxy"] == "10.0.0.9:3128"
    assert dev.reverse == []
    assert UP.record_for(SERIAL) is None


def test_verify_failure_rolls_back(dev):
    """写入成功但回读不是它（有的 ROM 会改写值）→ 一样回滚。"""
    dev.settings["http_proxy"] = "10.0.0.9:3128"
    real_run = dev.run
    state = {"written": False}

    def run(adb, args, timeout=20):
        rc, out, err = real_run(adb, args, timeout)
        a = [str(x) for x in args]
        if "put" in a and "http_proxy" in a:
            state["written"] = True          # 只有真正写完之后才谎报回读值
        if state["written"] and a[-1] == "http_proxy" and "get" in a:
            return 0, "127.0.0.1:9999", ""
        return rc, out, err

    UP.run = run
    try:
        r = UP.connect("adb", SERIAL, PORT)
    finally:
        UP.run = real_run
    assert r["ok"] is False and r["stage"] == "verify"
    assert dev.settings["http_proxy"] == "10.0.0.9:3128"
    assert dev.reverse == []


def test_preflight_aborts_without_touching_device(dev):
    """读不到当前设置（设备掉线）→ 什么都不改。"""
    dev.fail.add("get")
    before = dict(dev.settings)
    r = UP.connect("adb", SERIAL, PORT)
    assert r["ok"] is False and r["stage"] == "preflight"
    assert dev.settings == before
    assert dev.reverse == []


# ------------------------------------------------------------ 还原归属

def test_disconnect_preserves_proxy_changed_by_others(dev):
    """接管后别的程序把代理改走了 → 不抢，保留现状。"""
    dev.settings["http_proxy"] = "10.0.0.9:3128"
    assert UP.connect("adb", SERIAL, PORT)["ok"] is True
    dev.settings["http_proxy"] = "192.168.1.5:8080"
    r = UP.disconnect("adb", SERIAL)
    assert r["state"] == "restored", r
    assert dev.settings["http_proxy"] == "192.168.1.5:8080"
    assert "保留现状" in r["msg"]


def test_disconnect_pending_when_readback_impossible(dev):
    """手机已经拔掉，读不回确认 → 三态里的「待重连确认」，记录留着。"""
    dev.settings["http_proxy"] = "10.0.0.9:3128"
    assert UP.connect("adb", SERIAL, PORT)["ok"] is True
    dev.fail.add("get")
    r = UP.disconnect("adb", SERIAL)
    assert r["state"] == "pending", r
    assert UP.record_for(SERIAL) is not None, "没能确认就不能丢掉记录"


def test_disconnect_failed_keeps_record(dev):
    """回读成功但仍残留 → failed，记录保留以便下次再清。"""
    dev.settings["http_proxy"] = "10.0.0.9:3128"
    assert UP.connect("adb", SERIAL, PORT)["ok"] is True
    dev.fail.add("put")
    r = UP.disconnect("adb", SERIAL)
    assert r["state"] == "failed", r
    assert UP.record_for(SERIAL) is not None


def test_disconnect_without_record_clears_legacy_residue(dev):
    """老版本接管留下的残留（没有记录）→ 只能按清空处理。"""
    dev.settings["http_proxy"] = PROXY
    dev.reverse = [("tcp:%d" % PORT, "tcp:%d" % PORT)]
    r = UP.disconnect("adb", SERIAL, PORT)
    assert r["state"] == "restored", r
    assert UP.cleared(dev.settings.get("http_proxy", ""))
    assert dev.reverse == []


def test_disconnect_uses_recorded_port_when_port_unknown(dev):
    """界面不知道端口时（重启后），按记录里的端口撤映射。"""
    assert UP.connect("adb", SERIAL, PORT)["ok"] is True
    r = UP.disconnect("adb", SERIAL, None)
    assert r["state"] == "restored", r
    assert dev.reverse == []


# ------------------------------------------------------------ 守护脚本参数

def test_watch_restores_prev_not_clear(dev, monkeypatch):
    """守护脚本必须拿到接管前的值，否则拔线后会把公司代理清掉。"""
    seen = {}

    def fake_start(adb, serial, port, prev="", timeout=20):
        seen["prev"] = prev
        return {"ok": True, "pid": 1}

    monkeypatch.setattr(UP, "start_watch", fake_start)
    dev.settings["http_proxy"] = "10.0.0.9:3128"
    assert UP.connect("adb", SERIAL, PORT)["ok"] is True
    assert seen["prev"] == "10.0.0.9:3128"


# ------------------------------------------------------------ 强杀后重连（跨进程）

def test_reconnect_after_kill_keeps_original_prev(dev):
    """上次接管被强杀后直接重连：不能把「我们自己写的值」当成用户原值。

    这是系统代理那条 ``if sysproxy_owned(): prev = 旧 prev`` 的手机版。
    少了它，重连一次就把 127.0.0.1:P 存成 prev，断开时还原成死端口 ——
    用户真正的公司代理在这条路径上永久丢失，而且现象是「断开后手机没网」。
    """
    dev.settings["http_proxy"] = "10.0.0.9:3128"
    assert UP.connect("adb", SERIAL, PORT)["ok"] is True
    assert dev.settings["http_proxy"] == PROXY

    r2 = UP.connect("adb", SERIAL, PORT)          # 模拟重启后直接再连
    assert r2["ok"] is True, r2
    assert UP.record_for(SERIAL)["prev"]["http_proxy"] == "10.0.0.9:3128", \
        "第二次接管必须沿用最初的原始快照"

    assert UP.disconnect("adb", SERIAL)["state"] == "restored"
    assert dev.settings["http_proxy"] == "10.0.0.9:3128"


def test_reconnect_after_kill_claims_own_reverse(dev):
    """上次建的映射还在 → 要认领，否则断开时不撤，留一条没人管的映射。"""
    assert UP.connect("adb", SERIAL, PORT)["ok"] is True
    assert ("tcp:%d" % PORT, "tcp:%d" % PORT) in dev.reverse
    assert UP.connect("adb", SERIAL, PORT)["ok"] is True
    assert UP.record_for(SERIAL)["reverse_created"] is True, "自己上次建的映射要认领"
    UP.disconnect("adb", SERIAL)
    assert dev.reverse == []


def test_reconnect_keeps_adopted_reverse_unowned(dev):
    """本来就有的映射（别人建的）→ 重连后仍然不认领。"""
    dev.reverse = [("tcp:%d" % PORT, "tcp:%d" % PORT)]
    assert UP.connect("adb", SERIAL, PORT)["ok"] is True
    assert UP.record_for(SERIAL)["reverse_created"] is False
    assert UP.connect("adb", SERIAL, PORT)["ok"] is True
    assert UP.record_for(SERIAL)["reverse_created"] is False
    UP.disconnect("adb", SERIAL)
    assert ("tcp:%d" % PORT, "tcp:%d" % PORT) in dev.reverse


# ------------------------------------------------------------ 建映射时被杀（孤儿映射）

def test_pending_record_lands_before_reverse_is_created(dev, monkeypatch):
    """建映射之前必须先有记录 —— 否则进程死在两步之间，映射就成了永久孤儿。"""
    seen = {}
    real_run = dev.run

    def spy(adb, args, timeout=20):
        a = [str(x) for x in args]
        if "reverse" in a:
            rest = a[a.index("reverse") + 1:]
            if len(rest) == 2:                 # reverse tcp:P tcp:P
                seen["rec"] = UP.record_for(SERIAL)
        return real_run(adb, args, timeout)

    monkeypatch.setattr(UP, "run", spy)
    assert UP.connect("adb", SERIAL, PORT)["ok"] is True
    assert seen["rec"] is not None, "建映射时还没有记录，崩溃就没人认领"
    assert seen["rec"].get("pending") is True
    assert seen["rec"]["prev"]["http_proxy"] == "null"


def test_kill_between_mapping_and_record_leaves_cleanable_record(dev, monkeypatch):
    """在「映射已建、正式记录未写」之间被杀：pending 记录还在，下次能发现并清理。"""
    calls = {"list": 0}
    real_run = dev.run

    def spy(adb, args, timeout=20):
        a = [str(x) for x in args]
        if "reverse" in a and a[a.index("reverse") + 1:] == ["--list"]:
            calls["list"] += 1
            if calls["list"] == 2:            # 建完映射后的回读
                raise SystemExit(9)           # 模拟 os._exit：不执行任何 finally/回滚
        return real_run(adb, args, timeout)

    monkeypatch.setattr(UP, "run", spy)
    with pytest.raises(SystemExit):
        UP.connect("adb", SERIAL, PORT)

    rec = UP.record_for(SERIAL)
    assert rec is not None and rec.get("pending") is True, "崩溃窗口里必须留下可清理的记录"
    assert ("tcp:%d" % PORT, "tcp:%d" % PORT) in dev.reverse

    monkeypatch.setattr(UP, "_pid_alive", lambda pid: False)   # 进程已死
    assert [s for s, _ in UP.stale_serials()] == [SERIAL]

    r = UP.disconnect("adb", SERIAL)
    assert r["state"] == "restored", r
    assert dev.reverse == [], "pending 记录对应的映射必须撤掉"
    assert UP.record_for(SERIAL) is None


def test_reconnect_claims_reverse_left_by_pending_record(dev, monkeypatch):
    """上次死在建映射之后：映射是我们的，重连要认领，否则永远撤不掉。"""
    UP._remember(SERIAL, {"port": PORT, "proxy": PROXY,
                          "prev": {"http_proxy": "10.0.0.9:3128"},
                          "reverse_created": False, "pending": True,
                          "at": 1.0, "pid": 999999})
    dev.settings["http_proxy"] = "10.0.0.9:3128"
    dev.reverse = [("tcp:%d" % PORT, "tcp:%d" % PORT)]

    r = UP.connect("adb", SERIAL, PORT)
    assert r["ok"] is True, r
    rec = UP.record_for(SERIAL)
    assert rec.get("reverse_created") is True, "pending 留下的映射没被认领"
    assert rec.get("pending") is None, "接管成功后 pending 标记要消失"
    assert rec["prev"]["http_proxy"] == "10.0.0.9:3128"

    UP.disconnect("adb", SERIAL)
    assert dev.reverse == []


def test_failed_connect_restores_previous_record(dev):
    """第二次接管失败：不能把上一次的正式记录降级成 pending。"""
    assert UP.connect("adb", SERIAL, PORT)["ok"] is True
    rec1 = UP.record_for(SERIAL)
    dev.fail.add("reverse_list")
    r = UP.connect("adb", SERIAL, PORT)
    assert r["ok"] is False and r["stage"] == "reverse"
    assert UP.record_for(SERIAL) == rec1


def test_connect_aborts_when_record_cannot_be_written(dev, monkeypatch):
    """记录写不进去就不动手：没有 prev，断开时只能清空，用户代理永久丢失。"""
    monkeypatch.setattr(UP, "save_state", lambda st: False)
    r = UP.connect("adb", SERIAL, PORT)
    assert r["ok"] is False and r["stage"] == "record", r
    assert dev.reverse == []
    assert dev.settings["http_proxy"] == "null"


# ------------------------------------------------------------ 残留检测

def test_stale_serials_skips_live_owner(dev, monkeypatch):
    """记录属于还活着的进程 → 另一个实例正在接管，不能动。"""
    assert UP.connect("adb", SERIAL, PORT)["ok"] is True
    monkeypatch.setattr(UP, "_pid_alive", lambda pid: True)
    assert UP.stale_serials() == []


def test_stale_serials_reports_dead_owner(dev, monkeypatch):
    """写下记录的进程已经不在了 → 这是需要清理的残留。"""
    assert UP.connect("adb", SERIAL, PORT)["ok"] is True
    monkeypatch.setattr(UP, "_pid_alive", lambda pid: False)
    stale = UP.stale_serials()
    assert [s for s, _r in stale] == [SERIAL]
    assert stale[0][1]["proxy"] == PROXY


def test_stale_serials_treats_legacy_record_as_stale(dev):
    """旧版本记录没有 pid 字段，无法判活 → 按残留处理（否则永远清不掉）。"""
    UP.save_state({"version": UP.STATE_VERSION, "devices": {
        SERIAL: {"port": PORT, "proxy": PROXY, "prev": {}, "reverse_created": True}}})
    assert [s for s, _r in UP.stale_serials()] == [SERIAL]


def test_connect_records_pid(dev):
    """新记录要带 pid：下次启动靠它区分「自己的残留」和「别人的接管」。"""
    assert UP.connect("adb", SERIAL, PORT)["ok"] is True
    import os
    assert UP.record_for(SERIAL)["pid"] == os.getpid()


@pytest.mark.parametrize("value,expected", [
    ("10.0.0.9:3128", "10.0.0.9:3128"),
    ("[::1]:8080", "[::1]:8080"),
    ("null", ""),
    (":0", ""),
    ("", ""),
    ("1.2.3.4:80; rm -rf /", ""),      # 不往 adb shell 里拼奇怪的东西
    ("a'b", ""),
])
def test_safe_proxy_value(value, expected):
    assert UP.safe_proxy_value(value) == expected
