# -*- coding: utf-8 -*-
"""崩溃注入子进程（只由 tests/_probe_crash.py 调用）。

为什么必须是独立进程
--------------------
`os._exit(9)` 不跑 atexit / finally，才能真实模拟「写到一半被 taskkill」。
在 pytest 进程里做这件事会把测试进程本身杀掉，所以每个场景单开一个子进程，
用文件保存假注册表和接管记账，父进程在子进程死后检查残留。

假注册表是**写穿**的：每次 SetValueEx / DeleteValue 立刻落盘，否则子进程被
kill 后什么都看不到。
"""
import json
import os
import sys
import types
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

DIR = Path(os.environ["WN_CRASH_DIR"])
MODE = os.environ["WN_CRASH_MODE"]
KILL_AT = int(os.environ.get("WN_CRASH_KILL_AT", "0"))
FAIL_AT = int(os.environ.get("WN_CRASH_FAIL_AT", "0"))
FAIL_DELETE = os.environ.get("WN_CRASH_FAIL_DELETE", "")
PORT = int(os.environ.get("WN_CRASH_PORT", "8888"))
BUSY_PORT = int(os.environ.get("WN_CRASH_BUSY_PORT", "0"))
REG = DIR / "reg.json"


class FileReg:
    """内存 + 落盘的假 winreg：values = {name: [value, type]}"""

    def __init__(self):
        self.values = json.loads(REG.read_text(encoding="utf-8"))

    def _dump(self):
        tmp = REG.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.values, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, REG)

    def query(self, _k, name):
        if name not in self.values:
            raise OSError("no such value")
        v = self.values[name]
        return v[0], v[1]

    def set_value(self, _k, name, _reserved, typ, val):
        self.values[name] = [val, int(typ)]
        self._dump()

    def delete_value(self, _k, name):
        if name == FAIL_DELETE:
            raise PermissionError("access denied (injected)")
        if name not in self.values:
            raise FileNotFoundError("no such value")
        self.values.pop(name)
        self._dump()

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


reg = FileReg()
sys.modules["winreg"] = reg.namespace()

import weaknet as W  # noqa: E402

W._sysproxy_sidecar = lambda name: DIR / name
W.can_bind = lambda p, h="127.0.0.1": not (BUSY_PORT and p == BUSY_PORT)
W.log = lambda *a, **k: None
W.log_event = lambda *a, **k: None
W._notify_proxy_changed = lambda: None
W.G.proxy_port = PORT

_orig_write = W._sysproxy_write
count = {"n": 0}


def _hooked(k, name, value):
    """按调用序号注入失败/崩溃。

    序号包含回滚写入：写第 5 个字段失败后，回滚会继续调用本函数，
    所以「失败后回滚到一半又被杀」也能定点复现。
    """
    count["n"] += 1
    n = count["n"]
    if FAIL_AT and n == FAIL_AT:
        raise OSError("injected write failure")
    _orig_write(k, name, value)
    if KILL_AT and n == KILL_AT:
        os._exit(9)


W._sysproxy_write = _hooked

if os.environ.get("WN_CRASH_LEGACY") == "1":
    # 负对照：把恢复逻辑换回修复前的「全字段匹配才还原」。
    # 探针必须在部分接管场景变红，否则说明它根本没在检查东西。
    def _legacy_release(port):
        cur = W._reg_current()
        if cur is None:
            return False
        st = W._sysproxy_state_read()
        if not st:
            return False
        written = st.get("written") or {}
        keys = [n for n in W.SYSPROXY_REGS if n in written]
        if not keys:
            W._sysproxy_state_clear()
            return False
        if not all(W._reg_same(cur, written, n) for n in keys):
            W._sysproxy_state_clear()
            return False
        if not W.can_bind(port, "127.0.0.1"):
            return False
        return W.set_sysproxy(False)

    W.release_stale_sysproxy = _legacy_release


def main():
    if MODE == "takeover":
        ok = W.set_sysproxy(True)
    elif MODE == "release":
        ok = W.set_sysproxy(False)
    elif MODE == "recover":
        ok = W.release_stale_sysproxy(PORT)
    elif MODE == "state_fail":
        # 记账落盘失败：进程内仍应能正常接管/还原（只是异常退出后退化）
        W._sysproxy_state_write = lambda st: False
        ok = W.set_sysproxy(True)
        ok = W.set_sysproxy(False) and ok
    else:
        print("UNKNOWN_MODE %s" % MODE)
        return 2
    print("RESULT ok=%s writes=%d state=%s lock=%s" % (
        bool(ok), count["n"],
        (DIR / W.SYSPROXY_STATE_NAME).exists(),
        (DIR / W.SYSPROXY_LOCK_NAME).exists()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
