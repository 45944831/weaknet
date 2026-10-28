#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""WeakNet.exe 冒烟测试：无控制台（PE subsystem=2）+ 能起来 + 控制接口可用 + GUI 窗口存在"""
import json
import os
import struct
import subprocess
import sys
import time
import urllib.request

EXE = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "WeakNet.exe")
CTRL = "http://127.0.0.1:8899"
ok, fail = 0, 0


def check(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"PASS  {name} {extra}")
    else:
        fail += 1
        print(f"FAIL  {name} {extra}")


# 1. 静态：PE 子系统 2=GUI(无控制台) 3=CUI(有控制台)
with open(EXE, "rb") as f:
    head = f.read(0x400)
e_lfanew = struct.unpack("<I", head[0x3C:0x40])[0]
opt = e_lfanew + 4 + 20
subsystem = struct.unpack("<H", head[opt + 68:opt + 70])[0]
check("EXE 为 GUI 子系统（启动不弹 DOS 窗口）", subsystem == 2, f"subsystem={subsystem} size={os.path.getsize(EXE)}B")

# 2. 启动
proc = subprocess.Popen([EXE], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        stdin=subprocess.DEVNULL, cwd=os.path.dirname(EXE))
check("进程已启动", proc.poll() is None, f"pid={proc.pid}")

# 3. 控制接口
status = None
for _ in range(40):
    try:
        with urllib.request.urlopen(CTRL + "/api/status", timeout=2) as r:
            status = json.loads(r.read())
        break
    except Exception:
        time.sleep(0.5)
check("控制接口 /api/status 有响应", status is not None, "" if status is None else f"keys={len(status)}")
if status is not None:
    check("未接管时 vpn_active 为 False", status.get("vpn_active") in (False, 0, None), str(status.get("vpn_active")))

# 4. 开关限速（引擎真的在跑）
res = None
try:
    req = urllib.request.Request(CTRL + "/api/run", data=json.dumps({"enable": True}).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        res = json.loads(r.read())
except Exception as e:
    res = {"err": str(e)}
check("POST /api/run 开启限速成功", isinstance(res, dict) and res.get("ok") in (True, 1) or "err" not in res, str(res)[:120])

try:
    req = urllib.request.Request(CTRL + "/api/run", data=json.dumps({"enable": False}).encode(),
                                 headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=10).read()
except Exception:
    pass

# 5/6 用纯 ctypes 检查（不依赖 PowerShell）
import ctypes
from ctypes import wintypes

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

time.sleep(1.5)

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
u32 = ctypes.WinDLL("user32", use_last_error=True)


class PROCESSENTRY32(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
                ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", ctypes.c_long),
                ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_char * 260)]


snap = k32.CreateToolhelp32Snapshot(0x00000002, 0)
entry = PROCESSENTRY32()
entry.dwSize = ctypes.sizeof(PROCESSENTRY32)
conhost_parents, procs, has = [], {}, k32.Process32First(snap, ctypes.byref(entry))
while has:
    name = entry.szExeFile.decode(errors="ignore")
    procs[entry.th32ProcessID] = (name, entry.th32ParentProcessID)
    if name.lower() == "conhost.exe":
        conhost_parents.append(entry.th32ParentProcessID)
    has = k32.Process32Next(snap, ctypes.byref(entry))
k32.CloseHandle(snap)

# PyInstaller --onefile 会 fork 出子进程跑真正的程序，窗口/控制台都在进程树里
tree, stack = set(), [proc.pid]
while stack:
    pid = stack.pop()
    if pid in tree:
        continue
    tree.add(pid)
    stack += [p for p, (_, ppid) in procs.items() if ppid == pid]
check("没有为进程树产生 conhost 控制台宿主", not (tree & set(conhost_parents)),
      f"tree={sorted(tree)} conhost_parents={conhost_parents[:6]}")

WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)


def find_window(pids):
    found = {"title": ""}

    def _enum(hwnd, _):
        if not u32.IsWindowVisible(hwnd):
            return True
        p = wintypes.DWORD()
        u32.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
        if p.value in pids:
            n = u32.GetWindowTextLengthW(hwnd)
            if n:
                buf = ctypes.create_unicode_buffer(n + 1)
                u32.GetWindowTextW(hwnd, buf, n + 1)
                if buf.value and not found["title"]:
                    found["title"] = buf.value
        return True

    u32.EnumWindows(WNDENUMPROC(_enum), 0)
    return found["title"]


title = ""
for _ in range(40):                       # onefile 解压 + tkinter 起来要几秒，轮询等待
    title = find_window(tree)
    if title:
        break
    time.sleep(0.5)
check("GUI 主窗口存在且有标题", bool(title), f"title={title!r}")

proc.terminate()
try:
    proc.wait(10)
except Exception:
    proc.kill()

print(f"\n结果: {ok} PASS / {fail} FAIL")
sys.exit(1 if fail else 0)
