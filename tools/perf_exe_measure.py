# -*- coding: utf-8 -*-
"""EXE 冷启动与常驻内存测量（方案 §5 验收总表的前两行）。

量什么
------
1. **冷启动墙钟**：从 Popen 到 `/api/status` 可用 —— 用户点「开始」到界面有反应
   的真实等待时间。
2. **启动后进程树 RSS**：onefile 要把 ~10 MB 解包到 `%TEMP%\\_MEI*` 再起 Python，
   只看主进程会低估。
3. **MEI 解包体积**：onefile 的直接代价。

为什么需要它
------------
方案 §5 把「EXE 冷启动中位 ≤2.0 s」「EXE RSS 不增」列为**每版本**验收项，
但历史上只有一个放在仓库外的一次性脚本（`_measure_20261029/measure_win.py`），
版本一换就没法再测。这里把它固化成仓库内工具：换个 EXE 路径就能重测，
结果按版本归档。

用法
----
    py -3.10-64 tools/perf_exe_measure.py --exe WeakNet.exe --runs 5
    py -3.10-64 tools/perf_exe_measure.py --exe _tmp/exe_build/WeakNet.exe \\
        --runs 5 --out _tmp/exe_measure_new.json

注意：启动的是真实 GUI 程序（会接管系统代理吗？不会 —— 只有点了「开始」才接管），
测完会用 taskkill 结束进程并等端口释放。
"""
import argparse
import ctypes
import glob
import json
import os
import statistics
import subprocess
import sys
import time
import urllib.request
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATUS = "http://127.0.0.1:8899/api/status"


# --------------------------------------------------------------- RSS 采集
# 不依赖 psutil：psutil 没装就直接走 Win32 psapi，取不到就记 None，
# 绝不把「没测到」写成 0（历史脚本踩过这个坑，11-06 修过一次）。

class _PMC(ctypes.Structure):
    """PROCESS_MEMORY_COUNTERS。

    **必须是完整的 10 个成员**（2×DWORD + 8×SIZE_T，x64 下 72 字节）：
    `GetProcessMemoryInfo` 会校验 `cb == sizeof(PROCESS_MEMORY_COUNTERS)`，
    少定义两个 SIZE_T（QuotaPagedPoolUsage / QuotaNonPagedPoolUsage）会让
    cb 变成 56，调用直接返回 FALSE 且不给原因 —— 表现就是「RSS 永远读不到」。
    这个坑在 `_measure_20261029/measure_win.py` 里没有（那边是完整的 10 字段）。
    """
    _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t)]


class _PE32(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.c_void_p),
                ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", ctypes.c_long),
                ("dwFlags", wintypes.DWORD),
                ("szExeFile", wintypes.WCHAR * 260)]


def _rss_tree_mb(root_pid):
    """整棵进程树的工作集总和（MB）。取不到抛异常，由调用方记 None。"""
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    k32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    k32.Process32FirstW.argtypes = [ctypes.c_void_p, ctypes.POINTER(_PE32)]
    k32.Process32NextW.argtypes = [ctypes.c_void_p, ctypes.POINTER(_PE32)]
    k32.CloseHandle.argtypes = [ctypes.c_void_p]
    k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k32.OpenProcess.restype = ctypes.c_void_p
    psapi.GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.POINTER(_PMC),
                                           wintypes.DWORD]

    parent = {}
    snap = k32.CreateToolhelp32Snapshot(0x00000002, 0)   # TH32CS_SNAPPROCESS
    if not snap or snap == ctypes.c_void_p(-1).value:
        raise OSError("CreateToolhelp32Snapshot 失败")
    try:
        e = _PE32()
        e.dwSize = ctypes.sizeof(_PE32)
        ok = k32.Process32FirstW(snap, ctypes.byref(e))
        while ok:
            parent[e.th32ProcessID] = e.th32ParentProcessID
            ok = k32.Process32NextW(snap, ctypes.byref(e))
    finally:
        k32.CloseHandle(snap)

    tree, frontier = {root_pid}, [root_pid]
    while frontier:
        par = frontier.pop()
        for pid, ppid in parent.items():
            if ppid == par and pid not in tree:
                tree.add(pid)
                frontier.append(pid)

    total = 0
    for pid in tree:
        h = k32.OpenProcess(0x0400 | 0x0010, False, pid)   # QUERY_INFO | VM_READ
        if not h:
            continue
        try:
            c = _PMC()
            c.cb = ctypes.sizeof(_PMC)
            if psapi.GetProcessMemoryInfo(h, ctypes.byref(c), c.cb):
                total += c.WorkingSetSize
        finally:
            k32.CloseHandle(h)
    if total <= 0:
        raise OSError("进程树内存读取失败")
    return total / 1048576.0


# ------------------------------------------------------------------ 工具

def _mei_dirs():
    return set(glob.glob(os.path.join(os.environ.get("TEMP", "."), "_MEI*")))


def _ready(timeout=40.0):
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < timeout:
        try:
            with op.open(STATUS, timeout=1) as r:
                if r.status == 200:
                    r.read()
                    return time.perf_counter() - t0
        except Exception:
            time.sleep(0.05)
    return None


def _port_free_wait(seconds=15.0):
    import socket
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < seconds:
        s = socket.socket()
        try:
            s.bind(("127.0.0.1", 8899))
            s.close()
            return True
        except OSError:
            s.close()
            time.sleep(0.2)
    return False


def _dir_mb(path):
    total = 0
    for root, _d, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total / 1048576.0


def main():
    ap = argparse.ArgumentParser(description="EXE 冷启动 / RSS 测量")
    ap.add_argument("--exe", default=str(ROOT / "WeakNet.exe"),
                    help="待测 EXE 路径")
    ap.add_argument("--runs", type=int, default=5, help="轮数")
    ap.add_argument("--out", default="", help="结果 JSON 路径")
    args = ap.parse_args()

    exe = os.path.abspath(args.exe)
    if not os.path.exists(exe):
        sys.exit("找不到 EXE：%s" % exe)
    name = os.path.basename(exe)

    out = {"schema": 1,
           "date": time.strftime("%Y-%m-%d %H:%M:%S"),
           "exe": exe,
           "exe_size_mb": round(os.path.getsize(exe) / 1048576.0, 2),
           "runs": []}

    before = _mei_dirs()
    for i in range(args.runs):
        p = subprocess.Popen([exe], cwd=os.path.dirname(exe))
        t = _ready()
        try:
            rss = round(_rss_tree_mb(p.pid), 1)
            src = "ctypes"
        except Exception:
            rss, src = None, "unavailable"
        new_mei = _mei_dirs() - before
        meis = max([_dir_mb(d) for d in new_mei], default=0.0)
        out["runs"].append({"run": i + 1,
                            "ready_s": round(t, 2) if t else None,
                            "rss_mb": rss, "rss_src": src,
                            "mei_mb": round(meis, 1)})
        print(out["runs"][-1], flush=True)
        subprocess.run(["taskkill", "/IM", name, "/F"],
                       capture_output=True, timeout=20)
        _port_free_wait()
        time.sleep(1.2)
        before = (_mei_dirs() - new_mei) if new_mei else before

    ok = [r["ready_s"] for r in out["runs"] if r["ready_s"]]
    rss_ok = [r["rss_mb"] for r in out["runs"] if r["rss_mb"] is not None]
    out["summary"] = {
        "ready_med": round(statistics.median(ok), 2) if ok else None,
        "ready_min": min(ok) if ok else None,
        "ready_max": max(ok) if ok else None,
        "rss_med_mb": round(statistics.median(rss_ok), 1) if rss_ok else None,
        "rss_n": len(rss_ok),
        "mei_mb": max([r["mei_mb"] for r in out["runs"]], default=0.0),
    }
    out["note"] = ("rss_mb 为 /api/status 就绪后立即采样的进程树工作集总和；"
                   "None 表示本次没测到（不计入统计），不是 0")
    path = args.out or str(ROOT / "tests" / "perf" / "archive" / "v1.1.16" /
                           ("exe_measure_%s.json"
                            % time.strftime("%Y-%m-%d")))
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(json.dumps(out["summary"], ensure_ascii=False))
    print("已归档：%s" % path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
