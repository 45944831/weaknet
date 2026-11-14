# -*- coding: utf-8 -*-
"""低侵入性能采样入口（P0-2）。

为什么是 py-spy 而不是 cProfile：cProfile 要改启动方式、开销大，而且测出来的是
「加了观测之后」的分布；py-spy 从进程外采样（约 5% 开销），可以直接 attach 到
已经跑起来的 EXE，不改任何产品代码。

用法
----
    py -3.10-64 tools/perf_profile.py record -d 30 -o perf-flame.svg
    py -3.10-64 tools/perf_profile.py top
    py -3.10-64 tools/perf_profile.py dump            # 卡死/疑似死锁时看调用栈
    py -3.10-64 tools/perf_profile.py record --pid 1234     # attach 已有进程
    py -3.10-64 tools/perf_profile.py record --source --slow-cb 50
        # --source：跑 python weaknet.py（便于看到协程栈）
        # --slow-cb 50：事件循环里超过 50ms 的回调打印告警（PYTHONASYNCIODEBUG=1）

注意
----
* py-spy 需要管理员权限才能 attach 某些进程；Windows 上若报权限错误，
  用「以管理员身份运行」重开终端。
* 采样会改变运行时序（哪怕只有 5%），**结论必须来自采样与不采样各跑一次**的对照。
* 生产/发布构建不要开 --slow-cb：PYTHONASYNCIODEBUG 会显著拖慢事件循环。
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # weaknet/
EXE = os.path.join(ROOT, "WeakNet.exe")
SRC = os.path.join(ROOT, "weaknet.py")
STATUS_URL = "http://127.0.0.1:8899/api/status"


def find_pyspy():
    for name in ("py-spy", "py-spy.exe"):
        p = shutil.which(name)
        if p:
            return p
    cand = os.path.join(os.path.dirname(sys.executable), "Scripts", "py-spy.exe")
    if os.path.exists(cand):
        return cand
    return None


def wait_ready(timeout=40.0):
    """等控制台端口起来（与 measure_win.py 同一判据）。"""
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            with op.open(STATUS_URL, timeout=1) as r:
                return json.loads(r.read() or b"{}")
        except Exception:
            time.sleep(0.2)
    return None


def launch(source, slow_cb):
    env = dict(os.environ)
    if slow_cb:
        # 让事件循环自己报告「哪个回调堵了多久」，这是 GUI 卡顿/切场景不即时的直接证据
        env["PYTHONASYNCIODEBUG"] = "1"
        env["WN_SLOW_CB_MS"] = str(int(slow_cb))
    if source:
        cmd = [sys.executable, SRC]
    else:
        if not os.path.exists(EXE):
            sys.exit("找不到 %s；可加 --source 直接跑源码" % EXE)
        cmd = [EXE]
    print("启动：" + " ".join(cmd))
    p = subprocess.Popen(cmd, cwd=ROOT, env=env)
    st = wait_ready()
    if st is None:
        print("警告：40s 内没等到 /api/status，仍尝试采样")
        return p, None
    print("就绪：version=%s build=%s" % (st.get("version"), st.get("build")))
    return p, st


def main():
    ap = argparse.ArgumentParser(description="WeakNet 性能采样（py-spy）")
    ap.add_argument("mode", choices=("record", "top", "dump"))
    ap.add_argument("--pid", type=int, default=0, help="attach 已有进程；省略则自己启动一个")
    ap.add_argument("-d", "--duration", type=int, default=30, help="record 采样秒数")
    ap.add_argument("-o", "--out", default="perf-flame.svg", help="record 输出文件")
    ap.add_argument("--rate", type=int, default=100, help="采样频率 Hz")
    ap.add_argument("--source", action="store_true", help="跑源码而不是 EXE")
    ap.add_argument("--slow-cb", type=int, default=0,
                    help="配合 --source：事件循环慢回调告警阈值(ms)")
    ap.add_argument("--keep-run", action="store_true", help="采样结束后不关闭被启动的进程")
    args = ap.parse_args()

    spy = find_pyspy()
    if not spy:
        sys.exit("未找到 py-spy。先执行：py -3.10-64 -m pip install py-spy")

    proc = None
    pid = args.pid
    if not pid:
        proc, _st = launch(args.source, args.slow_cb)
        pid = proc.pid
        print("PID = %d" % pid)

    cmd = [spy, args.mode, "--pid", str(pid)]
    if args.mode == "record":
        cmd += ["-o", args.out, "-d", str(args.duration), "-r", str(args.rate)]
    elif args.mode == "top":
        cmd += ["-r", str(args.rate)]
    print("执行：" + " ".join(cmd))
    rc = subprocess.call(cmd)
    print("py-spy 退出码 = %d" % rc)

    if proc is not None and not args.keep_run:
        try:
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/F", "/T"],
                           capture_output=True, timeout=20)
        except Exception:
            proc.terminate()
    if args.mode == "record" and rc == 0 and os.path.exists(args.out):
        print("火焰图：%s（%d B）" % (args.out, os.path.getsize(args.out)))
    return rc


if __name__ == "__main__":
    sys.exit(main())
