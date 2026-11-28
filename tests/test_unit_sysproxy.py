# -*- coding: utf-8 -*-
"""Windows 系统代理接管：所有权、记账与回滚。

这一层的核心不是「能不能写注册表」，而是**谁有权改**。注册表里只有当前代理
是谁，没有「谁改的」，所以必须自己记账。下面每个用例对应一种真实会伤到用户的
场景：

* 用户本来开着公司代理，打开 WeakNet 随即退出 → 不能关掉它；
* 接管后用户手动换了代理 → 那份配置已经不是我们的，不能还原；
* 两个实例同时接管 → 后还原的会把先还原的再改一遍；
* 进程被强杀 → 下次启动要能按记录原样还原，而不是靠端口子串猜；
* 写到一半失败 → 不能留半套配置。

全部用假 winreg + 临时目录，不碰真实注册表。
"""
import sys
import types

import pytest

import weaknet as W

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------- 假注册表

class FakeReg:
    """内存注册表：values = {name: (value, type)}"""

    def __init__(self, values=None):
        self.values = dict(values or {})
        self.fail_on = set()        # 写这些字段时抛 OSError（模拟权限/磁盘失败）
        self.fail_delete = set()    # 删这些字段时抛 PermissionError（策略锁定）
        self.writes = []            # [(name, value)]，按发生顺序

    def query(self, _k, name):
        if name not in self.values:
            raise OSError("no such value")
        return self.values[name]

    def set_value(self, _k, name, _reserved, typ, val):
        if name in self.fail_on:
            raise OSError("access denied")
        self.writes.append((name, val))
        self.values[name] = (val, typ)

    def delete_value(self, _k, name):
        if name in self.fail_delete:
            raise PermissionError("access denied")
        if name not in self.values:
            # 与 winreg 一致：值不存在抛 ERROR_FILE_NOT_FOUND，被映射成
            # FileNotFoundError。用裸 OSError 会掩盖「权限被拒」和「本来没有」
            # 的区别，而这两者该走的路径完全不同。
            raise FileNotFoundError("no such value")
        self.values.pop(name)

    def namespace(self):
        return types.SimpleNamespace(
            HKEY_CURRENT_USER=1, REG_DWORD=4, REG_SZ=1,
            KEY_READ=0x20019, KEY_WRITE=0x20006, KEY_SET_VALUE=0x0002,
            OpenKey=lambda *a, **k: object(),
            CloseKey=lambda k: None,
            QueryValueEx=self.query,
            SetValueEx=self.set_value,
            DeleteValue=self.delete_value,
        )


CORP = {"ProxyEnable": (1, 4),
        "ProxyServer": ("10.0.0.9:3128", 1),
        "ProxyOverride": ("*.corp;10.*", 1)}


@pytest.fixture
def env(monkeypatch, tmp_path):
    """装好假注册表 + 临时记账目录，返回 (reg, state_path, lock_path)。"""
    reg = FakeReg(CORP)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "winreg", reg.namespace())
    monkeypatch.setattr(W, "log", lambda *a, **k: None)
    monkeypatch.setattr(W, "log_event", lambda *a, **k: None)
    monkeypatch.setattr(W, "can_bind", lambda p, h: True)
    state = tmp_path / W.SYSPROXY_STATE_NAME
    lock = tmp_path / W.SYSPROXY_LOCK_NAME
    monkeypatch.setattr(W, "_sysproxy_sidecar",
                        lambda name: tmp_path / name)
    monkeypatch.setattr(W.G, "proxy_port", 8888, raising=False)
    W.G.sysproxy_prev = None
    W.G.sysproxy_written = None
    W.G.sysproxy_on = False
    return reg, state, lock


# ------------------------------------------------- 1. 不关别人的代理

def test_release_without_ownership_leaves_registry_untouched(env):
    """用户开着公司代理、从没接管过 → 解除必须什么都不做。

    这是原缺陷的核心：老代码无条件写 ProxyEnable=0，一进一出就把公司代理关了。
    """
    reg, _state, _lock = env
    before = dict(reg.values)
    assert W.set_sysproxy(False) is True
    assert reg.values == before
    assert reg.writes == [], "没有所有权时不该有任何写入"


def test_owned_is_false_for_foreign_proxy(env):
    reg, _state, _lock = env
    assert W.sysproxy_enabled() is True        # 系统代理确实开着
    assert W.sysproxy_owned() is False         # 但不是我们开的


# ------------------------------------------------- 2. 原样还原

def test_takeover_then_release_restores_original(env):
    reg, state, _lock = env
    assert W.set_sysproxy(True) is True
    assert reg.values["ProxyServer"] == ("127.0.0.1:8888", 1)
    assert reg.values["ProxyOverride"][0] == W.SYSPROXY_OVERRIDE
    assert W.sysproxy_owned() is True
    assert state.exists(), "接管必须落盘记账"

    assert W.set_sysproxy(False) is True
    assert reg.values == CORP, "必须逐字段还原成接管前的样子"
    assert not state.exists(), "还原后记账要删掉"
    assert W.sysproxy_owned() is False


def test_release_deletes_fields_that_did_not_exist_before(env, monkeypatch):
    """原来没有 ProxyOverride → 还原时要删掉，不能留一个我们自己造的值。"""
    reg, _state, _lock = env
    reg.values.pop("ProxyOverride")
    assert W.set_sysproxy(True) is True
    assert "ProxyOverride" in reg.values
    assert W.set_sysproxy(False) is True
    assert "ProxyOverride" not in reg.values


def test_repeated_takeover_keeps_original_prev(env, monkeypatch):
    """端口变了再接管一次，prev 仍是最初的用户配置，不能把自己写的值当原值。"""
    reg, _state, _lock = env
    assert W.set_sysproxy(True) is True
    monkeypatch.setattr(W.G, "proxy_port", 8899, raising=False)
    assert W.set_sysproxy(True) is True
    assert reg.values["ProxyServer"] == ("127.0.0.1:8899", 1)
    assert W.set_sysproxy(False) is True
    assert reg.values == CORP


# ------------------------------------------------- 3. 接管后被别人改

def test_manual_change_after_takeover_is_preserved(env):
    """接管后用户手动换成别的代理 → 那个字段不能还原，开关也不能被关掉。"""
    reg, _state, _lock = env
    assert W.set_sysproxy(True) is True
    reg.values["ProxyServer"] = ("192.168.1.5:8080", 1)   # 用户手动改
    assert W.sysproxy_owned() is False

    assert W.set_sysproxy(False) is True
    assert reg.values["ProxyServer"] == ("192.168.1.5:8080", 1)
    # 代理地址已经指向别人的活代理，此时把 ProxyEnable 还原成 0 等于断网
    assert reg.values["ProxyEnable"] == (1, 4)


def test_disabled_by_user_after_takeover(env):
    """用户自己把开关关了：开关保持关闭，地址还原成原值。"""
    reg, _state, _lock = env
    assert W.set_sysproxy(True) is True
    reg.values["ProxyEnable"] = (0, 4)
    assert W.sysproxy_owned() is False
    assert W.set_sysproxy(False) is True
    assert reg.values["ProxyEnable"] == (0, 4)
    assert reg.values["ProxyServer"] == ("10.0.0.9:3128", 1)


# ------------------------------------------------- 4. 强杀后重启

def test_stale_state_restores_from_record(env, monkeypatch):
    """模拟上次被 taskkill：记账还在、注册表仍是我们的值、端口没人听。"""
    reg, _state, lock = env
    assert W.set_sysproxy(True) is True          # 留下记账
    # 模拟进程消失：内存镜像清空，只剩磁盘上的记录
    W.G.sysproxy_prev = None
    W.G.sysproxy_written = None
    W.G.sysproxy_on = False
    assert lock.exists()

    assert W.release_stale_sysproxy(8888) is True
    assert reg.values == CORP, "应按记录原样还原，而不是只关开关"
    assert not _state.exists()


def test_stale_state_skipped_when_port_listening(env, monkeypatch):
    """端口还有人监听 = 另一个实例在跑，它的接管有效，不能动。"""
    reg, state, _lock = env
    assert W.set_sysproxy(True) is True
    monkeypatch.setattr(W, "can_bind", lambda p, h: False)
    assert W.release_stale_sysproxy(8888) is False
    assert reg.values["ProxyServer"] == ("127.0.0.1:8888", 1)
    assert state.exists()


def test_stale_state_skipped_when_record_port_listening(env, monkeypatch):
    """上次跑在 8899、这次默认 8888：8899 上的实例还活着，不能还原它。

    探测端口必须取记录里的 port，而不是本次启动参数。用 8888 探测会得到
    「没人监听」，把另一个活实例的代理配置当成残留还原掉。
    """
    reg, state, _lock = env
    monkeypatch.setattr(W.G, "proxy_port", 8899, raising=False)
    assert W.set_sysproxy(True) is True
    W.G.sysproxy_prev = None
    W.G.sysproxy_written = None
    W.G.sysproxy_on = False
    # 只有 8899 有人监听；8888 空闲
    monkeypatch.setattr(W, "can_bind", lambda p, h: p != 8899)

    assert W.release_stale_sysproxy(8888) is False
    assert state.exists()
    assert reg.values["ProxyServer"] == ("127.0.0.1:8899", 1)


def test_stale_state_keeps_foreign_server_but_cleans_ours(env):
    """代理地址被改成第三个值：保留它，但把我们改过的非路由字段还原。

    与 `_sysproxy_release` 的语义一致：ProxyServer 易主时不动
    开关 / PAC / 自动检测（那是别人正在用的活配置），但 ProxyOverride
    是我们写进去的，应该还回原值。
    """
    reg, state, _lock = env
    assert W.set_sysproxy(True) is True
    W.G.sysproxy_prev = None
    W.G.sysproxy_written = None
    W.G.sysproxy_on = False
    reg.values["ProxyServer"] = ("127.0.0.1:7777", 1)
    assert W.release_stale_sysproxy(8888) is True
    assert not state.exists()
    assert reg.values["ProxyServer"] == ("127.0.0.1:7777", 1), "别人的代理不能动"
    assert reg.values["ProxyOverride"] == CORP["ProxyOverride"], "我们改过的要还回去"


def test_stale_state_finishes_partial_takeover(env):
    """接管写到一半被杀：已写的字段要回滚，未写的保持原值。

    这是崩溃注入探针在真实 kill 下复现的现场。老实现要求「所有字段都等于
    写入值」才还原，部分写入过不了这道门，于是删掉记录就走 —— ProxyEnable
    留在 1、ProxyServer 指向已退出的端口，整机断网。
    """
    reg, state, _lock = env
    assert W.set_sysproxy(True) is True
    # 模拟只写了前两个字段（ProxyEnable / ProxyServer）就被杀
    reg.values["ProxyOverride"] = CORP["ProxyOverride"]
    reg.values.pop("AutoConfigURL", None)
    reg.values.pop("AutoDetect", None)
    W.G.sysproxy_prev = None
    W.G.sysproxy_written = None
    W.G.sysproxy_on = False

    assert W.release_stale_sysproxy(8888) is True
    assert reg.values == CORP, "部分接管也必须回到接管前"
    assert not state.exists()


def test_stale_state_restores_when_only_proxyenable_written(env):
    """最窄的部分接管：只写了开关就被杀，代理地址还是原值。

    这时 ProxyServer「不是我们写的」但等于原值，不能按易主处理 —— 否则
    开关留在 1，用户原本关着的代理被打开。
    """
    reg, state, _lock = env
    reg.values["ProxyEnable"] = (0, 4)
    assert W.set_sysproxy(True) is True
    reg.values["ProxyServer"] = ("10.0.0.9:3128", 1)
    reg.values["ProxyOverride"] = CORP["ProxyOverride"]
    reg.values.pop("AutoConfigURL", None)
    reg.values.pop("AutoDetect", None)
    W.G.sysproxy_prev = None
    W.G.sysproxy_written = None
    W.G.sysproxy_on = False

    assert W.release_stale_sysproxy(8888) is True
    assert reg.values == {"ProxyEnable": (0, 4),
                          "ProxyServer": ("10.0.0.9:3128", 1),
                          "ProxyOverride": ("*.corp;10.*", 1)}
    assert not state.exists()


def test_stale_state_finishes_partial_release(env):
    """还原写到一半被杀：剩下的字段必须继续还完。"""
    reg, state, _lock = env
    reg.values["ProxyEnable"] = (0, 4)          # 用户原本就关着代理
    assert W.set_sysproxy(True) is True
    # 模拟还原时只写回 ProxyEnable / ProxyServer 就被杀
    reg.values["ProxyEnable"] = (0, 4)
    reg.values["ProxyServer"] = ("10.0.0.9:3128", 1)
    W.G.sysproxy_prev = None
    W.G.sysproxy_written = None
    W.G.sysproxy_on = False

    assert W.release_stale_sysproxy(8888) is True
    assert reg.values == {"ProxyEnable": (0, 4),
                          "ProxyServer": ("10.0.0.9:3128", 1),
                          "ProxyOverride": ("*.corp;10.*", 1)}
    assert not state.exists()


def test_stale_recovery_keeps_foreign_field_but_restores_ours(env):
    """用户只改了 ProxyOverride：保留他的值，但把我们写的代理/开关还原。

    不能因为一个字段易主就整份记录作废 —— 那会把 ProxyEnable=1、
    ProxyServer 指向死端口的半套配置留在系统里。
    """
    reg, state, _lock = env
    assert W.set_sysproxy(True) is True
    reg.values["ProxyOverride"] = ("*.user;10.*", 1)
    W.G.sysproxy_prev = None
    W.G.sysproxy_written = None
    W.G.sysproxy_on = False

    assert W.release_stale_sysproxy(8888) is True
    assert reg.values["ProxyEnable"] == CORP["ProxyEnable"]
    assert reg.values["ProxyServer"] == CORP["ProxyServer"]
    assert reg.values["ProxyOverride"] == ("*.user;10.*", 1)
    assert not state.exists()


def test_stale_recovery_removes_lock_from_dead_process(env, monkeypatch):
    """锁里是死进程的 pid：还原完成后要清掉，不能永久留一个假占用者。"""
    reg, _state, lock = env
    assert W.set_sysproxy(True) is True
    lock.write_text("424242", encoding="utf-8")
    monkeypatch.setattr(W, "_pid_alive", lambda pid: False)
    W.G.sysproxy_prev = None
    W.G.sysproxy_written = None
    W.G.sysproxy_on = False

    assert W.release_stale_sysproxy(8888) is True
    assert reg.values == CORP
    assert not lock.exists()


# ------------------------------------------------- 5. 写到一半失败

def test_partial_write_rolls_back(env):
    """第三个字段写失败 → 前两个必须回滚，不能留半套配置。"""
    reg, state, _lock = env
    reg.fail_on = {"ProxyOverride"}
    assert W.set_sysproxy(True) is False
    assert reg.values == CORP, "部分写入失败必须回滚到原值"
    assert not state.exists()
    assert W.G.sysproxy_written is None
    assert W.sysproxy_owned() is False


# ------------------------------------------------- 6. 多实例

def test_second_instance_refuses_when_lock_held(env, monkeypatch):
    """另一个活着的实例已经接管 → 本次不改注册表。"""
    reg, _state, lock = env
    lock.write_text("424242", encoding="utf-8")
    monkeypatch.setattr(W, "_pid_alive", lambda pid: int(pid) == 424242)
    before = dict(reg.values)
    assert W.set_sysproxy(True) is False
    assert reg.values == before
    assert reg.writes == []


def test_stale_lock_from_dead_process_is_taken_over(env, monkeypatch):
    """锁里是已退出的 pid → 视为残留，可以接管。"""
    reg, _state, lock = env
    lock.write_text("424242", encoding="utf-8")
    monkeypatch.setattr(W, "_pid_alive", lambda pid: False)
    assert W.set_sysproxy(True) is True
    assert lock.read_text(encoding="utf-8").strip() == str(__import__("os").getpid())


# ------------------------------------------------- 7. 老版本残留（无记账）

def test_legacy_residue_disables_only(env):
    """没有记账的老残留：只能关开关，且要求端口精确匹配。"""
    reg, _state, _lock = env
    reg.values = {"ProxyEnable": (1, 4), "ProxyServer": ("127.0.0.1:8888", 1)}
    assert W.release_stale_sysproxy(8888) is True
    assert reg.values["ProxyEnable"] == (0, 4)
    assert reg.values["ProxyServer"] == ("127.0.0.1:8888", 1)


def test_legacy_residue_rejects_substring_match(env):
    """127.0.0.1:8888.example 不是我们的端口，子串匹配会误伤。"""
    reg, _state, _lock = env
    reg.values = {"ProxyEnable": (1, 4),
                  "ProxyServer": ("127.0.0.1:8888.example", 1)}
    assert W.release_stale_sysproxy(8888) is False
    assert reg.values["ProxyEnable"] == (1, 4)


def test_release_stale_ignores_zero_port(env):
    reg, _state, _lock = env
    reg.values = {"ProxyEnable": (1, 4), "ProxyServer": ("127.0.0.1:8888", 1)}
    assert W.release_stale_sysproxy(0) is False
    assert reg.values["ProxyEnable"] == (1, 4)


# ------------------------------------------------- 8. 非 Windows 短路

def test_non_windows_is_noop(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    assert W.set_sysproxy(True) is False
    assert W.sysproxy_enabled() is False
    assert W.sysproxy_owned() is False
    assert W.release_stale_sysproxy(8888) is False


# ------------------------------------------------- 9. PAC / 自动检测（企业环境）
#
# WinINET 用 INTERNET_OPEN_TYPE_PRECONFIG 打开会话（Chrome / Edge / .NET 默认都是
# 这个），它的解析顺序是「先自动配置（PAC），没有才用静态代理」。也就是说：
# AutoConfigURL 非空时，我们写进去的 ProxyServer 会被整个忽略 —— 界面显示
# 「已接管」，实际一点弱网都没加。企业网里 PAC 极常见，这里必须显式中和。

PAC = {"ProxyEnable": (1, 4),
       "ProxyServer": ("10.0.0.9:3128", 1),
       "ProxyOverride": ("*.corp;10.*", 1),
       "AutoConfigURL": ("http://pac.corp/proxy.pac", 1),
       "AutoDetect": (1, 4)}


def test_takeover_neutralizes_pac(env):
    """接管时必须清掉 AutoConfigURL 并关掉自动检测，否则弱网完全不生效。"""
    reg, _state, _lock = env
    reg.values = dict(PAC)
    assert W.set_sysproxy(True) is True
    assert "AutoConfigURL" not in reg.values, \
        "PAC 还在 → WinINET 优先走 PAC，静态代理被忽略，接管等于没做"
    assert reg.values["AutoDetect"] == (0, 4), "自动检测（WPAD）同样会绕开静态代理"
    assert reg.values["ProxyServer"][0] == "127.0.0.1:8888"


def test_release_restores_pac(env):
    """解除后用户的 PAC 必须原样回来，不能变成「直连」或丢掉脚本地址。"""
    reg, _state, _lock = env
    reg.values = dict(PAC)
    assert W.set_sysproxy(True) is True
    assert W.set_sysproxy(False) is True
    assert reg.values == PAC, "PAC 与自动检测都要逐字段还原"


def test_takeover_survives_wininet_deleting_autodetect(env, monkeypatch):
    """WinINET 刷新后删掉 AutoDetect，不能让整套接管回滚。

    本机实测：_notify_proxy_changed() 之后 AutoDetect 无论写成 REG_DWORD 0
    还是 REG_SZ "0" 都会被 Windows 删掉。旧的自检要求逐字段全等，于是
    POST /api/sysproxy 恒返回 ok=false、ProxyEnable 永远写不进去。
    """
    reg, _state, _lock = env
    reg.values = dict(PAC)

    def wininet_refresh():
        reg.values.pop("AutoDetect", None)   # 模拟 WinINET 的字段清理

    monkeypatch.setattr(W, "_notify_proxy_changed", wininet_refresh)
    assert W.set_sysproxy(True) is True, "AutoDetect 被系统删掉不等于接管失败"
    assert reg.values["ProxyEnable"] == (1, 4)
    assert reg.values["ProxyServer"] == ("127.0.0.1:8888", 1)
    assert "AutoConfigURL" not in reg.values
    assert W.sysproxy_owned() is True


def test_release_restores_autodetect_when_wininet_deleted_it(env, monkeypatch):
    """接管期间 WinINET 删了 AutoDetect，解除时仍要把用户的原值装回去。"""
    reg, _state, _lock = env
    reg.values = dict(PAC)

    def wininet_refresh():
        reg.values.pop("AutoDetect", None)

    monkeypatch.setattr(W, "_notify_proxy_changed", wininet_refresh)
    assert W.set_sysproxy(True) is True
    assert "AutoDetect" not in reg.values
    assert W.set_sysproxy(False) is True
    assert reg.values == PAC, "用户原来的 WPAD 设置不能被 WinINET 的清理吃掉"


def test_release_keeps_pac_when_proxy_taken_over_by_others(env):
    """代理地址已被别人改走 → 不能再把 PAC 装回去。

    别人的配置是在「PAC 已被清空」的前提下写的静态代理；我们把 PAC 还原，
    等于让他的代理失效（PAC 优先）。控制类字段必须跟着所有权一起放手。
    """
    reg, _state, _lock = env
    reg.values = dict(PAC)
    assert W.set_sysproxy(True) is True
    reg.values["ProxyServer"] = ("192.168.1.5:8080", 1)
    assert W.set_sysproxy(False) is True
    assert "AutoConfigURL" not in reg.values
    assert reg.values["ProxyServer"] == ("192.168.1.5:8080", 1)


def test_legacy_record_without_pac_keys_still_restores(env):
    """v1.1.20 及更早留下的记账只有 3 个键。

    加了新字段后不能因为「记录里没有 AutoConfigURL」就判定记录失效 —— 那样
    上次强杀留下的系统代理就再也清不掉了。缺的字段按「没动过」跳过。
    """
    import json
    reg, state, _lock = env
    # 直接还原 v1.1.20 的现场：注册表里只有 3 个字段被改过，记账也只有 3 个键
    old_written = {"ProxyEnable": [1, 4],
                   "ProxyServer": ["127.0.0.1:8888", 1],
                   "ProxyOverride": [W.SYSPROXY_OVERRIDE, 1]}
    old_prev = {k: [v[0], v[1]] for k, v in CORP.items()}
    reg.values = {"ProxyEnable": (1, 4),
                  "ProxyServer": ("127.0.0.1:8888", 1),
                  "ProxyOverride": (W.SYSPROXY_OVERRIDE, 1)}
    state.write_text(json.dumps({"version": 1, "pid": 1, "port": 8888,
                                 "written": old_written, "prev": old_prev,
                                 "at": 0}), encoding="utf-8")
    assert W.release_stale_sysproxy(8888) is True
    assert reg.values == CORP, "旧记录仍要按它能还原的字段原样还原"


def test_takeover_fails_and_rolls_back_when_pac_cannot_be_removed(env):
    """AutoConfigURL 删不掉（策略 / 权限）→ 必须报失败并回滚。

    报成功的后果是：界面显示「已接管」，WinINET 却继续按 PAC 走，一点弱网都
    没加。宁可让用户看到「接管失败」，也不能让他拿着无效结果去分析。
    """
    reg, state, _lock = env
    reg.values = dict(PAC)
    reg.fail_delete = {"AutoConfigURL"}
    assert W.set_sysproxy(True) is False
    assert reg.values == PAC, "接管失败必须回到动手前的样子"
    assert not state.exists()
    assert W.G.sysproxy_written is None
    assert W.sysproxy_owned() is False


def test_repeated_takeover_with_pac_keeps_original(env, monkeypatch):
    """PAC 环境里换端口再接管一次：prev 仍是最初的用户配置。"""
    reg, _state, _lock = env
    reg.values = dict(PAC)
    assert W.set_sysproxy(True) is True
    monkeypatch.setattr(W.G, "proxy_port", 8899, raising=False)
    assert W.set_sysproxy(True) is True
    assert reg.values["ProxyServer"][0] == "127.0.0.1:8899"
    assert W.set_sysproxy(False) is True
    assert reg.values == PAC


def test_takeover_after_legacy_record_keeps_user_pac(env):
    """旧记账（3 键）在场、注册表里还有用户的 PAC，再接管一次。

    补齐分支要拿当前注册表值（这时仍是用户的原始 PAC）填进 prev；不补的话，
    这次释放会把用户真实的 PAC 当成「我们自己写的」删掉。
    """
    import json
    reg, state, _lock = env
    reg.values = dict(PAC)
    old_written = {"ProxyEnable": [1, 4],
                   "ProxyServer": ["127.0.0.1:8888", 1],
                   "ProxyOverride": [W.SYSPROXY_OVERRIDE, 1]}
    old_prev = {"ProxyEnable": [1, 4],
                "ProxyServer": ["10.0.0.9:3128", 1],
                "ProxyOverride": ["*.corp;10.*", 1]}
    state.write_text(json.dumps({"version": 1, "pid": 1, "port": 8888,
                                 "written": old_written, "prev": old_prev,
                                 "at": 0}), encoding="utf-8")
    assert W.set_sysproxy(True) is True          # 走到「已拥有 + 补齐」分支
    assert W.set_sysproxy(False) is True
    assert reg.values["AutoConfigURL"] == PAC["AutoConfigURL"], \
        "用户的 PAC 必须原样回来，不能被当成我们的写入值删掉"
    assert reg.values["AutoDetect"] == PAC["AutoDetect"]


# ------------------------------------------------- 9. 已知边界（登记而非修复）

def test_takeover_never_writes_connections_blob(env):
    """`Connections\\DefaultConnectionSettings` 不在记账范围内（已知边界）。

    WinINET 读取代理时以这份二进制 blob 为准；我们只写散值，blob 由系统自己
    同步。这不是「忘了」，是明确不做：正确写法要走 InternetSetOption 写
    per-connection 配置，改动面太大（见 docs/ownership.md 四·1）。

    用例锁两件事：
      * 接管 / 还原不得写坏这份 blob —— 写坏比残留更难恢复，整机代理配置会烂；
      * 它确实没被纳入字段表 —— 若将来纳入，必须同时处理二进制格式与回滚，
        这条会红，提醒作者同步文档与跨端契约。
    """
    reg, _state, _lock = env
    blob = b"\x46\x00\x00\x00\x01\x00\x00\x00" + b"\x00" * 24
    reg.values["DefaultConnectionSettings"] = (blob, 3)   # REG_BINARY

    assert W.set_sysproxy(True) is True
    assert reg.values["DefaultConnectionSettings"] == (blob, 3)
    assert W.set_sysproxy(False) is True
    assert reg.values["DefaultConnectionSettings"] == (blob, 3), \
        "blob 必须逐字节不变：我们既不读也不写它"

    assert all(n != "DefaultConnectionSettings" for n, _v in reg.writes), \
        "blob 未纳入记账，任何写入都说明字段表被改过而格式没跟上"
    assert "DefaultConnectionSettings" not in W.SYSPROXY_REGS


def test_lost_record_falls_back_to_disable_only(env):
    """记账被删 / 磁盘损坏：只能关开关，用户原代理救不回来（KNOWN）。

    这是崩溃探针里唯一标 KNOWN 的场景：接管已经把散值覆盖成我们的，原值只
    存在于记账文件；文件没了就没有任何依据。退化路径按端口精确匹配认领，
    只写 ProxyEnable=0 —— 先让用户能上网，但 ProxyServer / ProxyOverride /
    PAC / 自动检测都维持我们写入后的样子。

    探针把同一场景标 KNOWN 而不是 PASS；这里用假注册表把降级结果钉死，防止
    「只关开关」被悄悄改成「猜一个原值写回去」。将来若真能从 Connections blob
    反查原配置，这条会红：那时应改成断言原值回来，并删掉 crash.md 的 KNOWN。
    """
    reg, state, _lock = env
    reg.values = dict(PAC)
    assert W.set_sysproxy(True) is True
    state.unlink()                       # 记账没了（手工删 / 磁盘损坏）
    W.G.sysproxy_prev = None             # 进程也被杀过：内存里的原值同样没有
    W.G.sysproxy_written = None
    W.G.sysproxy_on = False
    n = len(reg.writes)

    assert W.release_stale_sysproxy(8888) is True
    assert reg.writes[n:] == [("ProxyEnable", 0)], "退化路径只允许关开关"
    assert reg.values["ProxyEnable"] == (0, 4)
    assert reg.values["ProxyServer"] == ("127.0.0.1:8888", 1), \
        "已知降级：原 10.0.0.9:3128 已经丢了，不能假装还原"
    assert reg.values["ProxyOverride"] == (W.SYSPROXY_OVERRIDE, 1)
    assert "AutoConfigURL" not in reg.values, "PAC 也回不来（原值随记账一起丢）"
    assert reg.values["AutoDetect"] == (0, 4)
    assert not state.exists()
