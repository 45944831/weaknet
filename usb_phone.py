# -*- coding: utf-8 -*-
"""
USB 手机弱网 —— 让电脑上的 WeakNet 代理通过 USB 线接管手机的 HTTP(S) 流量。

手机上不需要装 APK，原理是两步 adb：

    1) adb -s <serial> reverse tcp:P tcp:P
       把手机自身的 localhost:P 反向映射到电脑的 P 端口（走 USB，不经路由器/Wi-Fi）
    2) adb -s <serial> shell settings put global http_proxy 127.0.0.1:P
       让手机的系统代理指向自己的 localhost:P —— 于是流量顺着 USB 进到 WeakNet
    3) settings get global http_proxy 回读校验，写不进去就如实报错

断开就是反向操作：reverse --remove + 还原代理字段。少做一步手机就上不了网，
所以本模块里所有清理动作都是「尽力做完」，不会因为中间一步失败就跳过剩下的。
（:0 不能省：MIUI 把代理镜像在 Wi-Fi 网络配置里，只 delete 全局行清不掉它。）

接管必须记账。手机上原来可能就有代理（公司 Wi-Fi 的网关代理、用户自己填的），
只清空等于把别人的配置删了；而反向映射是「谁都能建、谁都能删」的共享资源，
不记下「这条是我建的」就没法判断断开时该不该删。所以每台手机一份接管记录，
落在 ~/.weaknet/weaknet_usb_state.json：

    {"version": 1, "devices": {"<serial>": {
        "port": 8888, "proxy": "127.0.0.1:8888",
        "prev": {"http_proxy": "...", "global_http_proxy_host": "...", ...},
        "reverse_created": true, "at": 1699999999.0}}}

工程约定（与 GUI 一起打包成 EXE，出问题时用户看不到任何控制台输出）：
    - 一律 subprocess.run(列表参数)，禁止 shell=True
    - Windows 下带 CREATE_NO_WINDOW：adb.exe 是控制台程序，从 GUI 进程里拉起来
      会闪一个黑框，加这个标志才不会
    - 所有调用带超时，全部异常在调用方可见（本模块抛 AdbError）
"""

import json
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

STATE_NAME = "weaknet_usb_state.json"
STATE_VERSION = 1

# 接管前需要快照的代理字段。前两个是旧 ROM 的兼容字段，有的机型会优先读它们。
PROXY_KEYS = ("http_proxy", "global_http_proxy_host", "global_http_proxy_port")

# 为什么需要这个守护：global http_proxy 写在系统设置里，拔线、重启都不消失；
# 而反向映射（adb reverse）只活在 adb 传输会话上，拔线即消失。两者一错位，
# 手机就把所有 HTTP(S) 发给一个没人监听的 127.0.0.1 端口，看起来就是「还在断网」。
# 拔线后电脑侧已经没有任何 adb 通道，清不了，只能由手机自己发现监听没了再还原。
_WATCH_SH = """#!/system/bin/sh
# WeakNet USB 代理守护：反向监听消失（拔线 / 传输断开）后自动还原全局代理。
# 由电脑端推送并启动，参数：<端口> <端口十六进制> <接管前的值>；勿手工修改。
PORT="$1"
HEX="$2"
PREV="$3"
EXPECT="127.0.0.1:$PORT"
PIDF="%s"
LOG="%s"

echo "start $(date) pid=$$ expect=$EXPECT prev=[$PREV]" >"$LOG"
echo $$ >"$PIDF"

listening() {
    awk -v p=":$HEX" '$2 ~ p"$" && $4 == "0A" {f=1} END {exit !f}' /proc/net/tcp /proc/net/tcp6 2>/dev/null
}

restore() {
    case "$PREV" in
        ""|"null"|"none"|":0"|"0")
            # 原来就没有代理：写 :0 让系统把镜像进 Wi-Fi 网络配置的那份一起清掉，
            # 再收残留字段（只 delete 在 MIUI 上清不干净）。
            settings put global http_proxy :0 >/dev/null 2>&1
            settings delete global global_http_proxy_host >/dev/null 2>&1
            settings delete global global_http_proxy_port >/dev/null 2>&1
            settings delete global http_proxy >/dev/null 2>&1
            echo "反向监听消失，已清除全局代理 $(date)" >>"$LOG"
            ;;
        *)
            # 接管前本来就有代理：还原它，而不是清空
            settings put global http_proxy "$PREV" >/dev/null 2>&1
            echo "反向监听消失，已还原接管前代理 [$PREV] $(date)" >>"$LOG"
            ;;
    esac
}

i=0
while [ "$i" -lt 43200 ]; do
    i=$((i + 1))
    sleep 2
    listening && continue
    sleep 2
    listening && continue
    # 真要动之前才读设置；值已被别人改掉就不动，不跟别的工具抢
    v=$(settings get global http_proxy 2>/dev/null)
    if [ "$v" != "$EXPECT" ]; then
        echo "proxy 已是 [$v]，不接管 $(date)" >>"$LOG"
        break
    fi
    restore
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


def safe_proxy_value(value):
    """能否安全地当 shell 参数传给手机上的守护脚本。

    代理值现实里就是 host:port（IPv6 字面量带方括号），只放行这个字符集。
    带引号/分号/换行的值直接当「没有原值」处理 —— 宁可清空，也不要把一段
    来路不明的字符串拼进 adb shell 命令行。
    """
    v = (value or "").strip()
    if not v or cleared(v):
        return ""
    ok = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.:_-[]")
    return v if all(ch in ok for ch in v) else ""


# ---------------------------------------------------------------- 接管记录

def _state_file():
    """接管记录的位置（与 weaknet_presets.json 同一套候选目录约定）。"""
    try:
        base = os.path.join(os.path.expanduser("~"), ".weaknet")
    except Exception:
        base = "."
    return os.path.join(base, STATE_NAME)


def load_state():
    """读全部接管记录；不存在 / 损坏 / 版本不认 一律当空。"""
    try:
        with open(_state_file(), "r", encoding="utf-8") as f:
            st = json.load(f)
    except Exception:
        return {"version": STATE_VERSION, "devices": {}}
    if not isinstance(st, dict) or st.get("version") != STATE_VERSION:
        return {"version": STATE_VERSION, "devices": {}}
    if not isinstance(st.get("devices"), dict):
        st["devices"] = {}
    return st


def save_state(st):
    """原子写接管记录；返回是否成功。"""
    p = _state_file()
    try:
        d = os.path.dirname(p)
        if d and not os.path.isdir(d):
            os.makedirs(d, exist_ok=True)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, p)
        return True
    except Exception:
        return False


def record_for(serial):
    """某台手机的接管记录；没有则 None。"""
    if not serial:
        return None
    rec = (load_state().get("devices") or {}).get(str(serial))
    return rec if isinstance(rec, dict) else None


def _pid_alive(pid):
    """进程是否还活着。

    与 weaknet.py 里同名函数同一道理：Windows 上不能用 os.kill(pid, 0)，
    CPython 会走到 TerminateProcess，等于把「检查」变成「击杀」。
    """
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if os.name != "nt":
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    try:
        import ctypes
        k = ctypes.windll.kernel32
        h = k.OpenProcess(0x1000, False, pid)      # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        try:
            code = ctypes.c_ulong()
            if not k.GetExitCodeProcess(h, ctypes.byref(code)):
                return False
            return code.value == 259                # STILL_ACTIVE
        finally:
            k.CloseHandle(h)
    except Exception:
        return False


def stale_serials():
    """接管记录还在、但写下它的进程已经不在了的手机。

    这就是「被 taskkill / 崩溃」留下的现场：手机上的 http_proxy 指向
    ``127.0.0.1:P``，而 P 端口对应的反向映射要么已随 adb 消失，要么还挂着一个
    没人转发的空壳 —— 两种情况手机都上不了网，用户却完全看不出原因。
    系统代理有 release_stale_sysproxy 处理这种事，手机侧一直没有对应物。

    只返回**属于死进程**的记录：另一个还开着的 WeakNet 实例正在接管，动它
    等于把人家拆了。旧版本记录里没有 pid 字段，无法判活，一并算残留（那种
    情况下本来也没有第二个实例能正常接管）。
    """
    out = []
    for serial, rec in (load_state().get("devices") or {}).items():
        if not isinstance(rec, dict):
            continue
        pid = rec.get("pid")
        if pid and _pid_alive(pid):
            continue
        out.append((str(serial), rec))
    return out


def forget(serial):
    st = load_state()
    devs = st.get("devices") or {}
    if str(serial) in devs:
        devs.pop(str(serial), None)
        st["devices"] = devs
        save_state(st)


def _remember(serial, rec):
    st = load_state()
    devs = st.get("devices") or {}
    devs[str(serial)] = rec
    st["devices"] = devs
    return save_state(st)


def _restore_record(serial, old):
    """把接管记录退回上一次的样子：有旧记录就写回，没有就删掉。

    用在「已经写了 pending 记录、但还没真正接管成功」的失败路径上。
    直接 forget 会把上一次仍有效的接管记录一起丢掉 —— 那台手机就再也没人
    认领了，断开时也不会撤映射。
    """
    if old is None:
        forget(serial)
    else:
        _remember(serial, old)


# ---------------------------------------------------------------- adb 基础

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


# ---------------------------------------------------------------- 反向映射

def reverse_pairs(adb, serial, timeout=ADB_TIMEOUT):
    """`reverse --list` → [(手机侧端点, 电脑侧端点)]。

    行格式形如 `host-14 tcp:8888 tcp:8888`。只取 tcp: 开头的两个 token，
    其余（传输名、序号）与判断无关。
    """
    rc, out, err = run(adb, ["-s", serial, "reverse", "--list"], timeout)
    if rc != 0:
        raise AdbError((err or out or "reverse --list 执行失败").strip()[:300])
    pairs = []
    for line in (out or "").splitlines():
        toks = [t for t in line.split() if t.startswith("tcp:")]
        if len(toks) >= 2:
            pairs.append((toks[0], toks[1]))
    return pairs


def _reverse_of(pairs, port):
    """手机侧占用 port 的那条映射（没有则 None）。"""
    want = "tcp:%d" % port
    for local, remote in pairs:
        if local == want:
            return (local, remote)
    return None


# ---------------------------------------------------------------- 守护进程

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


def start_watch(adb, serial, port, prev="", timeout=ADB_TIMEOUT):
    """在手机上启动守护：反向监听消失后按接管前的值还原全局代理。

    prev 是接管前 `settings get global http_proxy` 的原始值（空/null 表示原来
    没有代理）。返回 {"ok": True, "pid": N} 或 {"ok": False, "msg": ...}。
    装不上不影响代理本身，只是拔线后不会自愈，所以调用方按「警告」处理即可。
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
        safe_prev = safe_proxy_value(prev)      # 只放行 host:port 字符集，别拼进奇怪的东西
        launch = ("if command -v setsid >/dev/null 2>&1; then "
                  "setsid sh %s %d %s '%s' </dev/null >/dev/null 2>&1 & "
                  "else nohup sh %s %d %s '%s' </dev/null >/dev/null 2>&1 & fi"
                  % (WATCH_PATH, port, hexp, safe_prev,
                     WATCH_PATH, port, hexp, safe_prev))
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
        return {"ok": True, "pid": int(pid), "restore_to": safe_prev}
    except AdbError as e:
        return {"ok": False, "msg": str(e)}
    finally:
        if tmp:
            try:
                os.remove(tmp)
            except OSError:
                pass


# ---------------------------------------------------------------- 代理字段

def read_proxy(adb, serial, timeout=ADB_TIMEOUT):
    """读三个代理字段的原始值（读不到记空串）。"""
    out = {}
    for key in PROXY_KEYS:
        try:
            rc, o, _e = run(adb, ["-s", serial, "shell", "settings", "get", "global", key],
                            timeout)
            out[key] = (o or "").strip() if rc == 0 else ""
        except AdbError:
            out[key] = ""
    return out


def _put_proxy(adb, serial, value, timeout=ADB_TIMEOUT):
    rc, out, err = run(adb, ["-s", serial, "shell", "settings", "put", "global",
                             "http_proxy", value], timeout)
    return rc == 0, (err or out or "")


def _clear_proxy(adb, serial, timeout=ADB_TIMEOUT):
    """写 :0 再删字段。

    delete 只清全局行，MIUI 会把代理镜像进 Wi-Fi 网络配置且不跟着删，于是
    「设置里已经没有代理」的手机照样把所有流量发给死端口（实测网络 114）。
    写 :0 会触发一次同步，把网络里的代理一起清掉，顺带修好旧版本留下的残留。
    """
    errs = []
    try:
        _put_proxy(adb, serial, ":0", timeout)
    except AdbError as e:
        errs.append(str(e))
    for key in PROXY_KEYS:
        try:
            run(adb, ["-s", serial, "shell", "settings", "delete", "global", key], timeout)
        except AdbError as e:
            errs.append(str(e))
    return errs


def _restore_proxy(adb, serial, prev, timeout=ADB_TIMEOUT):
    """把代理字段还原成 prev（prev 里的空值 = 原来没有）。"""
    want = (prev or {}).get("http_proxy", "")
    if cleared(want):
        return _clear_proxy(adb, serial, timeout)
    errs = []
    try:
        ok, txt = _put_proxy(adb, serial, want, timeout)
        if not ok:
            errs.append("还原原代理被拒绝：" + txt[:200])
    except AdbError as e:
        errs.append(str(e))
    # 兼容字段：原来有就写回，原来没有就删掉
    for key in ("global_http_proxy_host", "global_http_proxy_port"):
        old = (prev or {}).get(key, "")
        try:
            if cleared(old):
                run(adb, ["-s", serial, "shell", "settings", "delete", "global", key], timeout)
            else:
                run(adb, ["-s", serial, "shell", "settings", "put", "global", key, old],
                    timeout)
        except AdbError as e:
            errs.append(str(e))
    return errs


# ---------------------------------------------------------------- 连接 / 断开

def _rollback(adb, serial, rec, timeout=ADB_TIMEOUT):
    """把一次没走完的接管退回原样：先还原代理字段，再撤自己建的映射。

    顺序不能反：映射先撤掉的话，手机上还指着它的代理会立刻变成死端口，
    中间这段时间手机是断网的。
    """
    notes = []
    try:
        notes.extend(_restore_proxy(adb, serial, rec.get("prev") or {}, timeout))
    except Exception as e:
        notes.append("还原代理失败：%r" % (e,))
    if rec.get("reverse_created") or rec.get("pending"):
        try:
            run(adb, ["-s", serial, "reverse", "--remove", "tcp:%d" % rec["port"]], timeout)
        except Exception as e:
            notes.append("撤端口映射失败：%r" % (e,))
    forget(serial)
    return notes


def connect(adb, serial, port, timeout=ADB_TIMEOUT):
    """把一台手机接到 WeakNet 代理上。

    返回 {"ok", "stage", "msg", ...}。每一阶段失败都会把已经做过的动作退回去，
    不留「映射建好了但代理没写上」这类残骸。
    """
    if not serial:
        return {"ok": False, "stage": "serial", "msg": "没有选中设备"}
    proxy = "127.0.0.1:%d" % port

    # 1) 前态快照：不知道原来是什么，就没法还原
    prev = read_proxy(adb, serial, timeout)
    if not any(prev.values()):
        # 三个字段全读不到：多半是设备掉线 / adb 断了，别在盲区里改设置
        return {"ok": False, "stage": "preflight",
                "msg": "读不到手机当前的代理设置（设备可能已断开），未做任何修改。"}

    # 上次接管留下的记录：手机现在读到的「原值」很可能就是我们自己写进去的
    # 那个 127.0.0.1:P。直接把它当用户原值备份，断开时会还原成死端口 ——
    # 用户真正的公司代理在这条路径上永久丢失。所以只要记录里的 proxy 与手机
    # 当前值一致，就沿用记录里的 prev（真正的原始快照）。
    old = record_for(serial)
    if old:
        old_mine = (old.get("proxy") or "").strip()
        now = (prev.get("http_proxy") or "").strip()
        if old_mine and now == old_mine and isinstance(old.get("prev"), dict):
            prev = old["prev"]

    # 1.5) 先落一笔「准备接管」的记录，再动手机上的共享资源。
    # 反向映射一旦建好就活在 adb server 里，进程若死在「建好映射」与「写正式
    # 记录」之间，映射不会自动撤销；没有记录，下次启动既发现不了它（
    # stale_serials 只看记录），也不知道该不该删 —— 就成了永久孤儿。
    # pending 记录让残留检测看得见它，disconnect 也按 pending 撤映射。
    pending = {"port": int(port), "proxy": proxy, "prev": prev,
               "reverse_created": False, "pending": True,
               "at": time.time(), "pid": os.getpid()}
    if not _remember(serial, pending):
        # 写不进记录就不动手：断开时没有 prev 可还原，只能「清空代理」，
        # 用户原来的公司代理会永久丢失 —— 比不接管更糟。
        return {"ok": False, "stage": "record",
                "msg": "无法写接管记录（%s 不可写）。为避免留下无法清理的端口映射"
                       "和丢失原代理设置，未做任何修改。" % _state_file()}

    # 2) 反向映射：先看有没有，不盲目覆盖别人的
    try:
        pairs = reverse_pairs(adb, serial, timeout)
    except AdbError as e:
        _restore_record(serial, old)
        return {"ok": False, "stage": "reverse", "msg": str(e)}
    mine = _reverse_of(pairs, port)
    created = False
    if mine:
        if mine[1] != "tcp:%d" % port:
            # 手机 localhost:P 已经指向别处：覆盖它等于拆别人的映射
            _restore_record(serial, old)
            return {"ok": False, "stage": "reverse",
                    "msg": ("手机上的 %d 端口已映射到 %s（不是本机 %d 端口）。"
                            "请在设置里换一个代理端口再连。" % (port, mine[1], port))}
        # 已经是 tcp:P -> tcp:P。可能是别人建的，也可能是**上次接管留下的**
        # （进程被杀，映射跟着 adb server 活到了现在）。后者要认领，否则断开时
        # 不会撤，留下一条没人管的映射。
        # 判据是「记录说这条映射归我们」：正常接管记 reverse_created，死在
        # 建映射与写正式记录之间的记 pending —— 两种都要认领，且不再要求
        # 手机代理值等于我们写过的值（pending 时根本还没写到手机上）。
        if old:
            created = bool(old.get("reverse_created") or old.get("pending"))
    else:
        try:
            rc, out, err = run(adb, ["-s", serial, "reverse",
                                     "tcp:%d" % port, "tcp:%d" % port], timeout)
        except AdbError as e:
            _restore_record(serial, old)
            return {"ok": False, "stage": "reverse", "msg": str(e)}
        if rc != 0:
            txt = (err or out or "未知错误")
            # 手机侧该端口已被占用：最常见的是 WeakNet.apk（自带代理服务）正开着 8888
            if "address already in use" in txt.lower():
                txt += ("\n\n手机上的 %d 端口已被占用，多半是 WeakNet.apk 正在运行"
                        "（它自己会监听 8888）。先在手机上停掉它，或在设置里换个代理端口再连。"
                        % port)
            _restore_record(serial, old)
            return {"ok": False, "stage": "reverse", "msg": "端口映射失败：" + txt[:400]}
        created = True

    # 3) 回读确认映射真的建立（有些 ROM 上 reverse 返回 0 但没落上）。
    #    比对的是完整端点 tcp:P -> tcp:P，不是「输出里出现过 tcp:P」。
    try:
        pairs2 = reverse_pairs(adb, serial, timeout)
    except AdbError as e:
        if created:
            try:
                run(adb, ["-s", serial, "reverse", "--remove", "tcp:%d" % port], timeout)
            except Exception:
                pass
        _restore_record(serial, old)
        return {"ok": False, "stage": "reverse", "msg": "回读端口映射失败：" + str(e)}
    got = _reverse_of(pairs2, port)
    if got != ("tcp:%d" % port, "tcp:%d" % port):
        if created:
            try:
                run(adb, ["-s", serial, "reverse", "--remove", "tcp:%d" % port], timeout)
            except Exception:
                pass
        _restore_record(serial, old)
        return {"ok": False, "stage": "reverse",
                "msg": "端口映射未生效（回读为 %s）" % (("%s→%s" % got) if got else "无")}

    # 4) 把 pending 记录升级成正式记录：后面任何一步失败都能据此还原
    #    pid 是给「下次启动发现自己已经死了」用的：只有还活着的进程才拥有这条
    #    记录，否则就是残留，见 stale_serials()。
    rec = {"port": int(port), "proxy": proxy, "prev": prev,
           "reverse_created": bool(created), "at": time.time(),
           "pid": os.getpid()}
    if not _remember(serial, rec):
        # 正式记录落盘失败：撤销刚建的映射，退回上一次的记录。
        # 撤不掉就**保留 pending 记录**（save_state 失败时文件里仍是它），
        # 这样下次启动还能发现并清理这条映射。
        removed = True
        if created:
            try:
                run(adb, ["-s", serial, "reverse", "--remove", "tcp:%d" % port], timeout)
            except Exception:
                removed = False
        if removed:
            _restore_record(serial, old)
        return {"ok": False, "stage": "record",
                "msg": "接管记录写入失败，已撤销本次操作。"
                       + ("" if removed else "端口映射未能撤销，已保留待清理记录。")}

    # 5) 清掉可能冲突的旧兼容字段（留着它们有的 ROM 会优先读它们）
    for key in ("global_http_proxy_host", "global_http_proxy_port"):
        try:
            run(adb, ["-s", serial, "shell", "settings", "delete", "global", key], timeout)
        except Exception:
            pass

    # 6) 写全局代理
    try:
        ok, txt = _put_proxy(adb, serial, proxy, timeout)
    except AdbError as e:
        notes = _rollback(adb, serial, rec, timeout)
        return {"ok": False, "stage": "settings",
                "msg": "写入全局代理失败：%s%s" % (e, ("（回滚：" + "；".join(notes) + "）") if notes else "")}
    if not ok:
        notes = _rollback(adb, serial, rec, timeout)
        return {"ok": False, "stage": "settings",
                "msg": "写入全局代理被拒绝：%s%s" % (txt[:300],
                                               ("（回滚：" + "；".join(notes) + "）") if notes else "")}

    # 7) 回读校验：写进去了但读回来不是它，等于没生效
    try:
        rc, out, err = run(adb, ["-s", serial, "shell", "settings", "get", "global",
                                 "http_proxy"], timeout)
    except AdbError as e:
        notes = _rollback(adb, serial, rec, timeout)
        return {"ok": False, "stage": "verify",
                "msg": "回读校验失败：%s%s" % (e, ("（回滚：" + "；".join(notes) + "）") if notes else "")}
    got_proxy = (out or "").strip()
    if rc != 0 or got_proxy != proxy:
        notes = _rollback(adb, serial, rec, timeout)
        return {"ok": False, "stage": "verify",
                "msg": "回读校验失败（期望 %s，实际 %s）%s"
                       % (proxy, got_proxy or (err or "(空)")[:200],
                          ("（回滚：" + "；".join(notes) + "）") if notes else "")}

    watch = start_watch(adb, serial, port, prev.get("http_proxy", ""), timeout)
    return {"ok": True, "stage": "done", "serial": serial, "port": port, "proxy": proxy,
            "watch": bool(watch.get("ok")), "watch_msg": watch.get("msg", ""),
            "restore_to": (prev.get("http_proxy") or "").strip(),
            "persisted": True}


def disconnect(adb, serial, port=None, timeout=ADB_TIMEOUT):
    """断开手机代理，恢复接管前的状态。每一步都尽力做完，失败也继续往下清。

    返回的 state 是三态之一：
      restored —— 回读确认已恢复（或本来就没有残留）
      pending  —— 回读失败（手机多半已断开），需要重连后再确认
      failed   —— 回读成功但仍残留，或写入被拒
    """
    if not serial:
        return {"ok": False, "stage": "serial", "state": "failed", "msg": "没有设备可断开"}

    rec = record_for(serial)
    if port is None:
        port = (rec or {}).get("port") or 0
    try:
        port = int(port or 0)
    except (TypeError, ValueError):
        port = 0

    errs = []
    notes = []
    # 先收守护：它盯着反向监听，正常断开时映射一撤它也会动作，别让两边同时改
    stop_watch(adb, serial, timeout)

    # 端口映射：只有「本次建的」才撤。别人建的（或接管前就有的）留着。
    # pending 也要撤：它表示「记录写完、映射可能已经建了，但没走到写代理那步」，
    # 崩溃窗口里留下的正是这种记录 —— 不撤就成永久孤儿。
    if port > 0:
        should_remove = True if rec is None else bool(
            rec.get("reverse_created") or rec.get("pending"))
        if should_remove:
            try:
                rc, out, err = run(adb, ["-s", serial, "reverse",
                                         "--remove", "tcp:%d" % port], timeout)
                if rc != 0:
                    txt = (err or out or "").lower()
                    # 映射本来就不存在（比如手机重插过）不算错误
                    if "not found" not in txt and "no reverse" not in txt:
                        errs.append("移除端口映射失败：" + (err or out)[:200])
            except AdbError as e:
                errs.append(str(e))
        else:
            notes.append("端口映射由其它程序建立，已保留")

    # 代理字段：接管前有代理就还原，没有才清空
    preserved = False
    if rec:
        cur = read_proxy(adb, serial, timeout)
        now = (cur.get("http_proxy") or "").strip()
        mine = (rec.get("proxy") or "").strip()
        if now and not cleared(now) and now != mine:
            # 已经不是我们写的值：别人改过了，不动
            preserved = True
            notes.append("手机代理已被改为 %s，保留现状" % now)
        else:
            try:
                errs.extend(_restore_proxy(adb, serial, rec.get("prev") or {}, timeout))
            except AdbError as e:
                errs.append(str(e))
    else:
        # 没有记账（老版本接管 / 记录被删）：只能按「清空」处理
        try:
            errs.extend(_clear_proxy(adb, serial, timeout))
        except AdbError as e:
            errs.append(str(e))

    # 校验：读回来是 null / 空 / :0 / 原值 才算真干净
    got, readback_ok = "", True
    try:
        rc, out, _e = run(adb, ["-s", serial, "shell", "settings", "get", "global",
                                "http_proxy"], timeout)
        got = (out or "").strip()
        if rc != 0:
            readback_ok = False
        elif preserved:
            pass                     # 刻意保留别人的值，不做「是否等于原值」的校验
        else:
            want = (rec or {}).get("prev", {}).get("http_proxy", "") if rec else ""
            if cleared(want):
                if not cleared(got):
                    errs.append("代理字段仍残留：" + got)
            elif (got or "").strip() != (want or "").strip():
                errs.append("代理字段未还原（期望 %s，实际 %s）" % (want, got or "(空)"))
    except AdbError as e:
        readback_ok = False
        notes.append("无法回读确认：%s" % e)

    if errs:
        # 有明确失败：保留记录，用户下次插回还能再清一次
        state = "failed"
        msg = "；".join(errs)
    elif not readback_ok:
        state = "pending"
        msg = "已下发还原，但无法回读确认（手机可能已断开），插回后请再点一次「断开」"
    else:
        state = "restored"
        forget(serial)
        msg = "已恢复手机直连"
    if notes:
        msg += "（" + "；".join(notes) + "）"
    return {"ok": state == "restored", "stage": "cleanup" if errs else "done",
            "state": state, "msg": msg, "port": port,
            "restored_to": ((rec or {}).get("prev") or {}).get("http_proxy", "")}


def status(adb, serial, port, timeout=ADB_TIMEOUT):
    """查询当前手机上的映射与代理状态，用于界面显示。查询失败一律当作未生效。"""
    r = {"reverse": False, "proxy": "", "owned": bool(record_for(serial))}
    if not adb or not serial:
        return r
    try:
        pairs = reverse_pairs(adb, serial, timeout)
        r["reverse"] = _reverse_of(pairs, port) is not None
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
