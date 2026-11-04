# -*- coding: utf-8 -*-
"""Windows 端：关闭 EXE 后是否残留进程 / 端口 / 系统代理。

五个场景（都用 WM_CLOSE，等价于点右上角 ✕，不模拟鼠标）：
  W1 冷启动后空闲关闭
  W2 开启限速 + 系统代理接管后关闭
  W3 共享服务运行中关闭（8900 / UDP 18899）
  W4 刚点「开启限速」就立刻关闭（竞态）
  W5 只杀引导进程（模拟任务管理器结束 / 崩溃），看 py 子进程是否变孤儿

判定标准：
  * 关闭后 8s 内 WeakNet.exe 进程数必须为 0（onefile 有引导 + 子进程两个）
  * 控制端口 8899 / 代理 8888 / 共享 8900 必须全部释放，UDP 18899 不再绑定
  * 系统代理若由本程序开启，退出后必须恢复为关闭
  * 关闭后不能出现父进程已消失的孤儿进程
"""
import ctypes
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.request
import winreg
from ctypes import wintypes as wt

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
EXE = os.path.join(ROOT, "WeakNet.exe")
CTRL = "http://127.0.0.1:8899"
CTRL_PORT = 8899
PROXY_PORT = 8888
SHARE_PORT = 8900
UDP_PORT = 18899

u = ctypes.windll.user32
u.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
u.GetWindowTextLengthW.argtypes = [wt.HWND]
u.IsWindowVisible.argtypes = [wt.HWND]
u.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
u.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]

checks = []


def ck(name, ok, detail=""):
    checks.append({"name": name, "ok": bool(ok), "detail": str(detail)})
    print(("PASS  " if ok else "FAIL  ") + name + (("  | " + str(detail)) if detail else ""),
          flush=True)


def gap(title):
    print("\n---- %s ----" % title, flush=True)


# ---------- 进程 ----------
def procs():
    """返回 [{'pid':int,'ppid':int,'cmd':str}]，WeakNet.exe 全部实例。"""
    ps = ("Get-CimInstance Win32_Process -Filter \"Name='WeakNet.exe'\" | "
          "ForEach-Object { \"$($_.ProcessId)|$($_.ParentProcessId)|$($_.CommandLine)\" }")
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = []
    for ln in (r.stdout or "").splitlines():
        ln = ln.strip()
        if "|" not in ln:
            continue
        p = ln.split("|", 2)
        try:
            out.append({"pid": int(p[0]), "ppid": int(p[1]),
                        "cmd": (p[2] if len(p) > 2 else "")[:120]})
        except ValueError:
            continue
    return out


def all_pids():
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                        "Get-Process | ForEach-Object { $_.Id }"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    s = set()
    for ln in (r.stdout or "").splitlines():
        ln = ln.strip()
        if ln.isdigit():
            s.add(int(ln))
    return s


def kill_pid(pid):
    subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# ---------- 端口 ----------
# 本机 netstat 看不到本程序 bind 的 TCP 监听行（bind/connect 均证明端口在占用，
# 但 netstat 只吐出 TIME_WAIT），所以改成主动探测：
#   TCP：能 connect 上 = 仍在监听；连接被拒/超时 = 已释放
#   UDP：bind 得上 = 已释放；bind 失败 = 仍被占用（UDP 无 TIME_WAIT，结论可靠）
def tcp_listening(port, timeout=0.8):
    s = socket.socket()
    s.settimeout(timeout)
    try:
        s.connect(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def udp_bound(port):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.bind(("0.0.0.0", port))
        return False          # bind 成功 = 没人在用
    except OSError:
        return True
    finally:
        s.close()


# ---------- 系统代理 ----------
IEP = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"


def sysproxy():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, IEP) as k:
            en = winreg.QueryValueEx(k, "ProxyEnable")[0]
            try:
                srv = winreg.QueryValueEx(k, "ProxyServer")[0]
            except OSError:
                srv = ""
            return {"enable": int(en), "server": str(srv)}
    except OSError:
        return {"enable": 0, "server": ""}


def restore_sysproxy(snap):
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, IEP, 0, winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, "ProxyEnable", 0, winreg.REG_DWORD, int(snap["enable"]))
        if snap.get("server"):
            winreg.SetValueEx(k, "ProxyServer", 0, winreg.REG_SZ, snap["server"])


# ---------- 窗口 ----------
def windows():
    out = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def cb(h, _l):
        n = u.GetWindowTextLengthW(h)
        if n:
            b = ctypes.create_unicode_buffer(n + 1)
            u.GetWindowTextW(h, b, n + 1)
            if "WeakNet" in b.value:
                pid = wt.DWORD()
                u.GetWindowThreadProcessId(h, ctypes.byref(pid))
                out.append((int(h), b.value, pid.value, bool(u.IsWindowVisible(h))))
        return True

    u.EnumWindows(cb, 0)
    return out


# ---------- 控制接口 ----------
def api(path, body=None, timeout=3):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(CTRL + path, data=data,
                                 method="GET" if body is None else "POST")
    if data:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def api_try(path, body=None, timeout=3):
    try:
        return api(path, body, timeout)
    except Exception:
        return None


def start_exe(extra=""):
    p = subprocess.Popen([EXE], cwd=ROOT)
    t0 = time.time()
    st = None
    while time.time() - t0 < 30:
        st = api_try("/api/status", timeout=1)
        if st:
            break
        time.sleep(0.2)
    return p, st, time.time() - t0


def wait_window(timeout=20):
    t0 = time.time()
    while time.time() - t0 < timeout:
        w = [x for x in windows() if x[3] and x[1].startswith("WeakNet")]
        if w:
            return w[0][0]
        time.sleep(0.2)
    return None


def close_and_wait(hwnd, timeout=12.0):
    """发 WM_CLOSE，返回 (是否全消失, 耗时秒)。"""
    t0 = time.time()
    u.PostMessageW(hwnd, 0x0010, 0, 0)
    while time.time() - t0 < timeout:
        if not procs():
            return True, time.time() - t0
        time.sleep(0.1)
    return False, time.time() - t0


def snapshot_ports():
    return {"tcp_8888": tcp_listening(8888), "tcp_8899": tcp_listening(8899),
            "tcp_8900": tcp_listening(8900), "udp_18899": udp_bound(18899)}


def cleanup():
    for p in procs():
        kill_pid(p["pid"])
    time.sleep(0.5)


def run_scenario(tag, prep=None):
    """启动 -> prep() 准备状态 -> 关闭 -> 校验。返回 dict 供汇总。"""
    gap(tag)
    res = {"tag": tag}
    p, st, dt = start_exe()
    ck("%s 启动并响应控制接口" % tag, st is not None,
       "" if st is None else "%s/%s 用时%.1fs" % (st.get("version"), st.get("build"), dt))
    if not st:
        cleanup()
        res["ok"] = False
        return res
    hwnd = wait_window()
    ck("%s 主窗口可见" % tag, hwnd is not None, hwnd)

    before = procs()
    ck("%s onefile 进程树为 2 个（引导 + 实体）" % tag, len(before) == 2,
       [(x["pid"], x["ppid"]) for x in before])
    res["procs_before"] = len(before)

    if prep:
        ok, detail = prep()
        ck("%s 场景准备就绪" % tag, ok, detail)

    ports_before = snapshot_ports()
    pids_before = set(x["pid"] for x in before)
    all_before = all_pids()

    if hwnd:
        gone, cost = close_and_wait(hwnd)
    else:
        # 没有窗口就地杀掉，避免脚本挂住
        for x in procs():
            kill_pid(x["pid"])
        gone, cost = (not procs()), 0.0

    ck("%s 关闭后 WeakNet.exe 全部退出（无残留）" % tag, gone,
       "%.2fs" % cost if gone else "残留 %s" % [(x["pid"], x["ppid"]) for x in procs()])
    ck("%s 关闭耗时合理（<5s）" % tag, gone and cost < 5.0, "%.2fs" % cost)
    res["close_cost"] = round(cost, 2)

    # 给系统一点时间回收句柄/端口
    time.sleep(1.2)
    ports_after = snapshot_ports()
    released = [k for k, v in ports_after.items() if v]
    ck("%s 关闭后端口全部释放" % tag, not released,
       "仍占用: %s" % ports_after if released else str(ports_before))
    res["ports_after"] = ports_after

    # 孤儿扫描：关闭后新出现、且父进程已消失的进程
    time.sleep(2.0)
    after = all_pids()
    born = after - all_before - pids_before
    orphans = []
    if born:
        ps = ("Get-CimInstance Win32_Process | Where-Object { %s } | "
              "ForEach-Object { \"$($_.ProcessId)|$($_.ParentProcessId)|$($_.Name)\" }"
              % " -or ".join("$_.ProcessId -eq %d" % i for i in list(born)[:200]))
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        live = all_pids()
        for ln in (r.stdout or "").splitlines():
            f = ln.strip().split("|")
            if len(f) < 3 or not f[0].isdigit():
                continue
            pid, ppid, name = int(f[0]), int(f[1]) if f[1].isdigit() else -1, f[2]
            if ppid not in live:      # 父进程已不存在 -> 孤儿
                orphans.append((pid, ppid, name))
    ck("%s 无孤儿进程" % tag, not orphans, orphans)
    res["orphans"] = orphans
    res["ok"] = gone and not released and not orphans
    return res


def main():
    gap("环境基线")
    ck("基线：没有 WeakNet.exe 在跑", not procs(), [(x["pid"], x["ppid"]) for x in procs()])
    if procs():
        cleanup()
    base_proxy = sysproxy()
    print("基线系统代理：%s" % base_proxy, flush=True)
    results = []

    # W1 空闲关闭
    results.append(run_scenario("W1 空闲关闭"))

    # W2 限速 + 系统代理
    def prep_w2():
        r1 = api("/api/run", {"enable": True})
        r2 = api("/api/sysproxy", {"enable": True})
        sp = sysproxy()
        return (bool(r1 and r1.get("limit_on")) and sp["enable"] == 1,
                "limit_on=%s sysproxy=%s" % (r1, sp))

    res = run_scenario("W2 限速+接管中关闭", prep_w2)
    sp_after = sysproxy()
    ck("W2 退出后系统代理已恢复（不残留接管）", sp_after["enable"] == 0, sp_after)
    res["ok"] = res["ok"] and sp_after["enable"] == 0
    results.append(res)

    # W3 共享运行中关闭（共享默认随服务启动，检查端口确实在监听）
    def prep_w3():
        sh = api_try("/api/share/status")
        return (tcp_listening(SHARE_PORT), "share=%s listen8900=%s udp18899=%s"
                % (sh, tcp_listening(SHARE_PORT), udp_bound(UDP_PORT)))

    results.append(run_scenario("W3 共享运行中关闭", prep_w3))

    # W4 开启限速后立刻关闭（竞态）
    gap("W4 开启限速后 0.3s 内关闭（竞态）")
    p, st, dt = start_exe()
    ck("W4 启动", st is not None, dt)
    hwnd = wait_window()
    ck("W4 主窗口可见", hwnd is not None, hwnd)
    t0 = time.time()
    api_try("/api/run", {"enable": True}, timeout=1)
    time.sleep(0.3)
    if hwnd:
        gone, cost = close_and_wait(hwnd)
    else:
        gone, cost = (not procs()), 0.0
    ck("W4 竞态关闭后无残留进程", gone,
       "%.2fs" % (time.time() - t0) if gone else [(x["pid"], x["ppid"]) for x in procs()])
    time.sleep(1.2)
    ck("W4 竞态关闭后端口释放",
       not tcp_listening(CTRL_PORT) and not tcp_listening(PROXY_PORT),
       snapshot_ports())
    results.append({"tag": "W4 竞态关闭", "ok": gone})

    # W5 只杀引导进程（模拟任务管理器结束）
    gap("W5 强杀引导进程（模拟崩溃）")
    p, st, dt = start_exe()
    ck("W5 启动", st is not None, dt)
    ps = procs()
    parent = [x for x in ps if x["ppid"] not in [y["pid"] for y in ps]]
    child = [x for x in ps if x not in parent]
    ck("W5 能区分引导进程与实体进程", bool(parent) and bool(child),
       "parent=%s child=%s" % ([x["pid"] for x in parent], [x["pid"] for x in child]))
    if parent:
        kill_pid(parent[0]["pid"])
        time.sleep(1.5)
        left = procs()
        gone5 = not left
        ck("W5 强杀引导进程后，实体进程也随之退出", gone5,
           "残留 %s" % [(x["pid"], x["ppid"]) for x in left])
        # 引导进程没有窗口；它被杀后实体进程带着窗口继续活着。
        # 只要窗口还在，用户就能看见并再点 ✕ 正常退出，不会变成「看不见的幽灵进程」。
        w5 = [x for x in windows() if x[3] and x[1].startswith("WeakNet")]
        ck("W5 若残留，窗口是否仍然可见（决定严重性）", (gone5 and not w5) or bool(w5),
           "残留=%s 窗口=%s" % (len(left), w5))
        if len(left) == 1 and w5:
            ck("W5 残留进程就是窗口所属进程（点 ✕ 仍可正常退出）",
               w5[0][2] == left[0]["pid"], "窗口 pid=%s 残留 pid=%s"
               % (w5[0][2], left[0]["pid"]))
        time.sleep(3.0)
        ck("W5 3 秒后仍无进程（不会被复活）", not procs(),
           [(x["pid"], x["ppid"]) for x in procs()])
        time.sleep(1.0)
        ck("W5 端口释放", not tcp_listening(CTRL_PORT) and not tcp_listening(PROXY_PORT),
           snapshot_ports())
        results.append({"tag": "W5 强杀引导", "ok": gone5})

    cleanup()
    restore_sysproxy(base_proxy)
    gap("收尾")
    ck("收尾：无 WeakNet.exe 残留", not procs(), [(x["pid"], x["ppid"]) for x in procs()])
    ck("收尾：系统代理已还原", sysproxy()["enable"] == base_proxy["enable"], sysproxy())
    return results


if __name__ == "__main__":
    rc = 1
    results = []
    try:
        results = main()
        rc = 0
    except Exception:
        import traceback
        traceback.print_exc()
        ck("脚本异常", False)
    finally:
        cleanup()
        out = os.path.join(ROOT, "_t_exit_residue_win.json")
        with open(out, "w", encoding="utf-8") as f:
            json.dump(checks, f, ensure_ascii=False, indent=2)
        bad = [c for c in checks if not c["ok"]]
        print("\n==== %d 项检查，%d 失败 ====" % (len(checks), len(bad)), flush=True)
        for c in bad:
            print("  FAIL: %s | %s" % (c["name"], c["detail"]), flush=True)
        sys.exit(2 if bad else rc)
