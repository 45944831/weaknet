# -*- coding: utf-8 -*-
"""
USB 手机弱网 —— 让电脑上的 WeakNet 代理通过 USB 线接管手机的 HTTP(S) 流量。

手机上不需要装 APK，原理是两步 adb：

    1) adb -s <serial> reverse tcp:P tcp:P
       把手机自身的 localhost:P 反向映射到电脑的 P 端口（走 USB，不经路由器/Wi-Fi）
    2) adb -s <serial> shell settings put global http_proxy 127.0.0.1:P
       让手机的系统代理指向自己的 localhost:P —— 于是流量顺着 USB 进到 WeakNet
    3) settings get global http_proxy 回读校验，写不进去就如实报错

断开就是反向操作：reverse --remove + settings delete。少做一步手机就上不了网，
所以本模块里所有清理动作都是「尽力做完」，不会因为中间一步失败就跳过剩下的。

工程约定（与 GUI 一起打包成 EXE，出问题时用户看不到任何控制台输出）：
    - 一律 subprocess.run(列表参数)，禁止 shell=True
    - Windows 下带 CREATE_NO_WINDOW：adb.exe 是控制台程序，从 GUI 进程里拉起来
      会闪一个黑框，加这个标志才不会
    - 所有调用带超时，全部异常在调用方可见（本模块抛 AdbError）
"""

import os
import shutil
import subprocess

ADB_TIMEOUT = 20                  # 单条 adb 命令的超时（秒）
CREATE_NO_WINDOW = 0x08000000     # Windows：不为子进程创建控制台窗口


class AdbError(Exception):
    """adb 不可用 / 执行失败 / 超时。消息直接给用户看，要能说明白是哪一步。"""


def exe_name():
    return "adb.exe" if os.name == "nt" else "adb"


def _decode(b):
    if b is None:
        return ""
    if isinstance(b, str):
        return b.strip()
    for enc in ("utf-8", "gbk"):
        try:
            return b.decode(enc).strip()
        except Exception:
            continue
    return b.decode("utf-8", "replace").strip()


def candidate_paths(saved=None):
    """adb.exe 的候选位置，按优先级从高到低。

    用户自己选过的路径排第一（他要是用默认位置能跑，根本不会去选），
    其次是环境变量和 PATH，最后才是几个常见安装目录。
    """
    cands = []

    def add(p):
        if p:
            cands.append(str(p))

    add(saved)
    for key in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        v = os.environ.get(key)
        if v:
            add(os.path.join(v, "platform-tools", exe_name()))
    w = shutil.which("adb")
    add(w)

    home = os.path.expanduser("~")
    local = os.environ.get("LOCALAPPDATA") or os.path.join(home, "AppData", "Local")
    add(os.path.join(local, "Android", "Sdk", "platform-tools", exe_name()))
    add(os.path.join(home, "AppData", "Local", "Android", "Sdk", "platform-tools", exe_name()))
    add(os.path.join(home, "Android", "Sdk", "platform-tools", exe_name()))
    for base in ("C:/Program Files (x86)/Android/android-sdk",
                 "C:/Program Files/Android/android-sdk",
                 "C:/Android/android-sdk",
                 "C:/platform-tools"):
        add(os.path.join(base, "platform-tools" if "sdk" in base else "", exe_name()))
    return cands


def find_adb(saved=None):
    """返回第一个真实存在的 adb 路径；都找不到返回 None。

    只检查文件存在，不真的跑 `adb version` —— 每次刷新设备都多起一个进程没必要，
    真跑不起来会在后面第一条命令上如实报错。
    """
    seen = set()
    for c in candidate_paths(saved):
        if not c or c in seen:
            continue
        seen.add(c)
        try:
            if os.path.isfile(c):
                return c
        except Exception:
            continue
    return None


def run(adb, args, timeout=ADB_TIMEOUT):
    """执行一条 adb 命令，返回 (returncode, stdout, stderr)。失败抛 AdbError。"""
    if not adb or not os.path.isfile(adb):
        raise AdbError("adb 不存在：%s" % (adb or "(未配置)"))
    cmd = [adb] + [str(a) for a in args]
    kw = {}
    if os.name == "nt":
        kw["creationflags"] = CREATE_NO_WINDOW
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=timeout, **kw)
    except subprocess.TimeoutExpired:
        raise AdbError("adb 命令超时（>%ss）：%s" % (timeout, " ".join(cmd)))
    except FileNotFoundError:
        raise AdbError("adb 无法执行：%s" % adb)
    except OSError as e:
        raise AdbError("adb 调用失败：%s" % e)
    return p.returncode, _decode(p.stdout), _decode(p.stderr)


def version(adb):
    """adb 版本串，取不到返回空串"""
    try:
        rc, out, _e = run(adb, ["version"], timeout=10)
        if rc == 0:
            for line in out.splitlines():
                if "Version" in line:
                    return line.strip()
            return out.splitlines()[0] if out else ""
    except Exception:
        pass
    return ""


def list_devices(adb, timeout=ADB_TIMEOUT):
    """解析 `adb devices -l`。

    返回 {"devices": [...], "blocked": [...]}：
      devices —— state == device，可以用的
      blocked —— unauthorized（没授权 USB 调试）/ offline 等，不能用的，
                 单独返回是为了给用户提示「去手机上点允许」，而不是笼统说「没设备」
    """
    rc, out, err = run(adb, ["devices", "-l"], timeout)
    if rc != 0:
        raise AdbError((err or out or "adb devices 执行失败").strip()[:300])

    devices, blocked = [], []
    for line in out.splitlines():
        line = line.strip()
        if not line or line.startswith("*") or line.lower().startswith("list of devices"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        serial, state = parts[0], parts[1]
        info = {}
        for kv in parts[2:]:
            if ":" in kv:
                k, v = kv.split(":", 1)
                info[k] = v
        item = {
            "serial": serial,
            "state": state,
            "model": info.get("model") or info.get("device") or info.get("product") or "",
            "product": info.get("product", ""),
        }
        (devices if state == "device" else blocked).append(item)
    return {"devices": devices, "blocked": blocked}


def connect(adb, serial, port, timeout=ADB_TIMEOUT):
    """把一台手机接到 WeakNet 代理上。返回 {"ok":..., "stage":..., "msg":...}"""
    if not serial:
        return {"ok": False, "stage": "serial", "msg": "没有选中设备"}
    proxy = "127.0.0.1:%d" % port

    # 1) 端口反向映射：手机 localhost:port -> 电脑 port
    try:
        rc, out, err = run(adb, ["-s", serial, "reverse", "tcp:%d" % port, "tcp:%d" % port],
                           timeout)
    except AdbError as e:
        return {"ok": False, "stage": "reverse", "msg": str(e)}
    if rc != 0:
        return {"ok": False, "stage": "reverse",
                "msg": "端口映射失败：" + (err or out or "未知错误")[:300]}

    # 2) 确认映射真的建立（有些 ROM 上 reverse 返回 0 但没落上）
    try:
        rc2, out2, _e2 = run(adb, ["-s", serial, "reverse", "--list"], timeout)
    except AdbError as e:
        return {"ok": False, "stage": "reverse", "msg": str(e)}
    if rc2 != 0 or ("tcp:%d" % port) not in (out2 or ""):
        return {"ok": False, "stage": "reverse",
                "msg": "端口映射未生效（reverse --list 里查不到 tcp:%d）" % port}

    # 3) 写全局代理。先清掉可能冲突的旧兼容字段（留着它们有的 ROM 会优先读它们）
    for key in ("global_http_proxy_host", "global_http_proxy_port"):
        try:
            run(adb, ["-s", serial, "shell", "settings", "delete", "global", key], timeout)
        except Exception:
            pass

    try:
        rc, out, err = run(adb, ["-s", serial, "shell", "settings", "put", "global",
                                 "http_proxy", proxy], timeout)
    except AdbError as e:
        return {"ok": False, "stage": "settings", "msg": str(e)}
    if rc != 0:
        return {"ok": False, "stage": "settings",
                "msg": "写入全局代理被拒绝：" + (err or out or "未知错误")[:300]}

    # 4) 回读校验：写进去了但读回来不是它，等于没生效
    try:
        rc, out, err = run(adb, ["-s", serial, "shell", "settings", "get", "global",
                                 "http_proxy"], timeout)
    except AdbError as e:
        return {"ok": False, "stage": "verify", "msg": str(e)}
    got = (out or "").strip()
    if rc != 0 or got != proxy:
        return {"ok": False, "stage": "verify",
                "msg": "回读校验失败（期望 %s，实际 %s）" % (proxy, got or (err or "(空)")[:200])}

    return {"ok": True, "stage": "done", "serial": serial, "port": port, "proxy": proxy}


def disconnect(adb, serial, port, timeout=ADB_TIMEOUT):
    """断开手机代理，恢复直连。每一步都尽力做完，失败也继续往下清。"""
    if not serial:
        return {"ok": False, "stage": "serial", "msg": "没有设备可断开"}

    errs = []
    try:
        rc, out, err = run(adb, ["-s", serial, "reverse", "--remove", "tcp:%d" % port], timeout)
        if rc != 0:
            txt = (err or out or "").lower()
            # 映射本来就不存在（比如手机重插过）不算错误，只要代理字段清掉就行
            if "not found" not in txt and "no reverse" not in txt:
                errs.append("移除端口映射失败：" + (err or out)[:200])
    except AdbError as e:
        errs.append(str(e))

    for key in ("http_proxy", "global_http_proxy_host", "global_http_proxy_port"):
        try:
            run(adb, ["-s", serial, "shell", "settings", "delete", "global", key], timeout)
        except AdbError as e:
            errs.append(str(e))

    # 校验：读回来是 null / 空才算真清干净
    got, note = "", ""
    try:
        rc, out, _e = run(adb, ["-s", serial, "shell", "settings", "get", "global",
                                "http_proxy"], timeout)
        got = (out or "").strip()
        if rc != 0:
            note = "（无法回读确认，手机可能已断开连接）"
        elif got in ("", "null", "None"):
            got = ""
        else:
            errs.append("代理字段仍残留：" + got)
    except AdbError as e:
        note = "（无法回读确认：%s）" % e

    if errs:
        return {"ok": False, "stage": "cleanup", "msg": "；".join(errs) + note}
    return {"ok": True, "stage": "done", "msg": "已恢复手机直连" + note}


def status(adb, serial, port, timeout=ADB_TIMEOUT):
    """查询当前手机上的映射与代理状态，用于界面显示。查询失败一律当作未生效。"""
    r = {"reverse": False, "proxy": ""}
    if not adb or not serial:
        return r
    try:
        rc, out, _e = run(adb, ["-s", serial, "reverse", "--list"], timeout)
        if rc == 0:
            r["reverse"] = ("tcp:%d" % port) in (out or "")
    except Exception:
        pass
    try:
        rc, out, _e = run(adb, ["-s", serial, "shell", "settings", "get", "global",
                                "http_proxy"], timeout)
        if rc == 0 and out and out not in ("null", "None"):
            r["proxy"] = out
    except Exception:
        pass
    return r
