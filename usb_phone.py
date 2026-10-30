# -*- coding: utf-8 -*-
"""
USB 手机弱网 —— 让电脑上的 WeakNet 代理通过 USB 线接管手机的 HTTP(S) 流量。

手机上不需要装 APK，原理是两步 adb：

    1) adb -s <serial> reverse tcp:P tcp:P
       把手机自身的 localhost:P 反向映射到电脑的 P 端口（走 USB，不经路由器/Wi-Fi）
    2) adb -s <serial> shell settings put global http_proxy 127.0.0.1:P
       让手机的系统代理指向自己的 localhost:P —— 于是流量顺着 USB 进到 WeakNet
    3) settings get global http_proxy 回读校验，写不进去就如实报错

断开就是反向操作：reverse --remove + settings put :0 + settings delete。少做一步手机就上不了网，
所以本模块里所有清理动作都是「尽力做完」，不会因为中间一步失败就跳过剩下的。
（:0 不能省：MIUI 把代理镜像在 Wi-Fi 网络配置里，只 delete 全局行清不掉它。）

工程约定（与 GUI 一起打包成 EXE，出问题时用户看不到任何控制台输出）：
    - 一律 subprocess.run(列表参数)，禁止 shell=True
    - Windows 下带 CREATE_NO_WINDOW：adb.exe 是控制台程序，从 GUI 进程里拉起来
      会闪一个黑框，加这个标志才不会
    - 所有调用带超时，全部异常在调用方可见（本模块抛 AdbError）
"""

import os
import shutil
import subprocess
import tempfile
import time

ADB_TIMEOUT = 20                  # 单条 adb 命令的超时（秒）
CREATE_NO_WINDOW = 0x08000000     # Windows：不为子进程创建控制台窗口

# 哪些读回值等于「没有代理」。:0 是 Android 的无代理哨兵，MIUI 写完它就是直连。
CLEARED_PROXY = ("", "null", "none", ":0", "0")

WATCH_PATH = "/data/local/tmp/wn_proxy_watch.sh"
WATCH_PID = "/data/local/tmp/wn_proxy_watch.pid"
WATCH_LOG = "/data/local/tmp/wn_proxy_watch.log"

# 为什么需要这个守护：global http_proxy 写在系统设置里，拔线、重启都不消失；
# 而反向映射（adb reverse）只活在 adb 传输会话上，拔线即消失。两者一错位，
# 手机就把所有 HTTP(S) 发给一个没人监听的 127.0.0.1 端口，看起来就是「还在断网」。
# 拔线后电脑侧已经没有任何 adb 通道，清不了，只能由手机自己发现监听没了再清。
_WATCH_SH = """#!/system/bin/sh
# WeakNet USB 代理守护：反向监听消失（拔线 / 传输断开）后自动撤掉全局代理。
# 由电脑端推送并启动，参数：<端口> <端口十六进制>；勿手工修改。
PORT="$1"
HEX="$2"
EXPECT="127.0.0.1:$PORT"
PIDF="%s"
LOG="%s"

echo "start $(date) pid=$$ expect=$EXPECT" >"$LOG"
echo $$ >"$PIDF"

listening() {
    awk -v p=":$HEX" '$2 ~ p"$" && $4 == "0A" {f=1} END {exit !f}' /proc/net/tcp /proc/net/tcp6 2>/dev/null
}

i=0
while [ "$i" -lt 43200 ]; do
    i=$((i + 1))
    sleep 2
    listening && continue
    sleep 2
    listening && continue
    # 真要删之前才读设置；值已被别人改掉就不动，不跟别的工具抢
    v=$(settings get global http_proxy 2>/dev/null)
    if [ "$v" != "$EXPECT" ]; then
        echo "proxy 已是 [$v]，不接管 $(date)" >>"$LOG"
        break
    fi
    # 只 delete 设置不够：MIUI 会把全局代理镜像进当前 Wi-Fi 的网络配置，删设置不动
    # 那一份，网络校验探测还是打向旧代理，手机一直显示「无互联网」（真机实测网络
    # 114 删完仍连 8888）。先写 :0 让系统把网络里的代理同步清掉，再收残留字段。
    settings put global http_proxy :0 >/dev/null 2>&1
    settings delete global global_http_proxy_host >/dev/null 2>&1
    settings delete global global_http_proxy_port >/dev/null 2>&1
    settings delete global http_proxy >/dev/null 2>&1
    echo "反向监听消失，已清除全局代理 $(date)" >>"$LOG"
    break
done
rm -f "$PIDF"
""" % (WATCH_PID, WATCH_LOG)


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


def cleared(value):
    """settings get global http_proxy 的返回值是否代表「没有代理」。"""
    return (value or "").strip().lower() in CLEARED_PROXY


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


def stop_watch(adb, serial, timeout=ADB_TIMEOUT):
    """收掉手机上的守护进程。失败只返回原因，不抛异常 —— 调用方都在清理路径上。"""
    if not serial:
        return {"ok": False, "msg": "没有设备"}
    cmd = ("p=$(cat %s 2>/dev/null); "
           "if [ -n \"$p\" ] && grep -q wn_proxy_watch /proc/$p/cmdline 2>/dev/null; "
           "then kill $p 2>/dev/null; fi; rm -f %s %s"
           % (WATCH_PID, WATCH_PID, WATCH_PATH))
    try:
        run(adb, ["-s", serial, "shell", "sh", "-c", cmd], timeout)
    except AdbError as e:
        return {"ok": False, "msg": str(e)}
    return {"ok": True}


def start_watch(adb, serial, port, timeout=ADB_TIMEOUT):
    """在手机上启动守护：反向监听消失后自动撤掉全局代理。

    返回 {"ok": True, "pid": N} 或 {"ok": False, "msg": ...}。装不上不影响代理本身，
    只是拔线后不会自愈，所以调用方按「警告」处理即可。
    """
    if not serial:
        return {"ok": False, "msg": "没有设备"}
    stop_watch(adb, serial, timeout)          # 先收掉上一份，避免多个守护打架

    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(suffix=".sh")
        with os.fdopen(fd, "wb") as f:
            f.write(_WATCH_SH.encode("utf-8"))      # 二进制写：不让 Windows 把换行改成 CRLF
        rc, out, err = run(adb, ["-s", serial, "push", tmp, WATCH_PATH], timeout)
        if rc != 0:
            return {"ok": False, "msg": "推送守护脚本失败：" + (err or out or "未知错误")[:200]}
        rc, out, err = run(adb, ["-s", serial, "shell", "chmod", "755", WATCH_PATH], timeout)
        if rc != 0:
            return {"ok": False, "msg": "守护脚本不可执行：" + (err or out or "未知错误")[:200]}

        # setsid 另起会话：拔线时 adb 传输断开会把命令会话里的进程带走，nohup 只挡
        # SIGHUP，真机实测照样会死；setsid 起的新会话没有控制终端，能活到发现监听消失。
        hexp = "%04X" % port
        launch = ("if command -v setsid >/dev/null 2>&1; then "
                  "setsid sh %s %d %s </dev/null >/dev/null 2>&1 & "
                  "else nohup sh %s %d %s </dev/null >/dev/null 2>&1 & fi"
                  % (WATCH_PATH, port, hexp, WATCH_PATH, port, hexp))
        run(adb, ["-s", serial, "shell", launch], timeout)

        # 回读 pid 文件并确认进程还活着：adb 返回 0 不代表后台进程没被立刻回收
        pid = ""
        for _ in range(6):
            time.sleep(0.2)
            rc, out, _e = run(adb, ["-s", serial, "shell", "cat", WATCH_PID], timeout)
            pid = (out or "").strip()
            if rc == 0 and pid.isdigit():
                break
        if not pid.isdigit():
            return {"ok": False, "msg": "守护进程未启动"}
        rc, _o, _e = run(adb, ["-s", serial, "shell", "kill", "-0", pid], timeout)
        if rc != 0:
            return {"ok": False, "msg": "守护进程启动后立即退出（日志：%s）" % WATCH_LOG}
        return {"ok": True, "pid": int(pid)}
    except AdbError as e:
        return {"ok": False, "msg": str(e)}
    finally:
        if tmp:
            try:
                os.remove(tmp)
            except OSError:
                pass


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
        txt = (err or out or "未知错误")
        # 手机侧该端口已被占用：最常见的是 WeakNet.apk（自带代理服务）正开着 8888
        if "address already in use" in txt.lower():
            txt += ("\n\n手机上的 %d 端口已被占用，多半是 WeakNet.apk 正在运行"
                    "（它自己会监听 8888）。先在手机上停掉它，或在设置里换个代理端口再连。"
                    % port)
        return {"ok": False, "stage": "reverse", "msg": "端口映射失败：" + txt[:400]}

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

    watch = start_watch(adb, serial, port, timeout)
    return {"ok": True, "stage": "done", "serial": serial, "port": port, "proxy": proxy,
            "watch": bool(watch.get("ok")), "watch_msg": watch.get("msg", "")}


def disconnect(adb, serial, port, timeout=ADB_TIMEOUT):
    """断开手机代理，恢复直连。每一步都尽力做完，失败也继续往下清。"""
    if not serial:
        return {"ok": False, "stage": "serial", "msg": "没有设备可断开"}

    errs = []
    # 先收守护：它盯着反向监听，正常断开时映射一撤它也会动作，别让两边同时删
    stop_watch(adb, serial, timeout)
    try:
        rc, out, err = run(adb, ["-s", serial, "reverse", "--remove", "tcp:%d" % port], timeout)
        if rc != 0:
            txt = (err or out or "").lower()
            # 映射本来就不存在（比如手机重插过）不算错误，只要代理字段清掉就行
            if "not found" not in txt and "no reverse" not in txt:
                errs.append("移除端口映射失败：" + (err or out)[:200])
    except AdbError as e:
        errs.append(str(e))

    # 先写 :0 再删。delete 只清全局行，MIUI 会把代理镜像进 Wi-Fi 网络配置且不跟着删，
    # 于是「设置里已经没有代理」的手机照样把所有流量发给死端口（实测网络 114）。
    # 写 :0 会触发一次同步，把网络里的代理一起清掉，顺带修好旧版本留下的残留。
    try:
        run(adb, ["-s", serial, "shell", "settings", "put", "global", "http_proxy", ":0"],
            timeout)
    except AdbError as e:
        errs.append(str(e))
    for key in ("global_http_proxy_host", "global_http_proxy_port", "http_proxy"):
        try:
            run(adb, ["-s", serial, "shell", "settings", "delete", "global", key], timeout)
        except AdbError as e:
            errs.append(str(e))

    # 校验：读回来是 null / 空 / :0 才算真清干净
    got, note = "", ""
    try:
        rc, out, _e = run(adb, ["-s", serial, "shell", "settings", "get", "global",
                                "http_proxy"], timeout)
        got = (out or "").strip()
        if rc != 0:
            note = "（无法回读确认，手机可能已断开连接）"
        elif cleared(got):
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
        if rc == 0 and out and not cleared(out):
            r["proxy"] = out
    except Exception:
        pass
    return r
