# -*- coding: utf-8 -*-
"""P1-2 双端 API 契约测试：能力 → 路径 → 状态码 → 顶层字段。

Windows 端默认在本进程内起一个隔离的控制服务（独立设置文件 + 随机端口），
不碰真实配置、不占 8899。

Android 端需要已安装的 App 与 adb forward：
    adb forward tcp:18899 tcp:8899
    python _t_api_parity.py --android http://127.0.0.1:18899

只断言契约（状态码、顶层字段、类型），不依赖机器上的具体数据，因此可在 CI 反复跑。
"""
import argparse
import asyncio
import json
import os
import socket
import sys
import tempfile
import urllib.parse
from pathlib import Path


HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import weaknet as w  # noqa: E402

R = []

# (能力, 方法, 路径, body, 期望状态, 顶层字段 | None=list)
COMMON = [
    ("控制状态", "GET", "/api/status", None, 200,
     ("profile", "preset_key", "limit_on", "listen")),
    ("预置场景", "GET", "/api/presets", None, 200, None),
    ("本机身份", "GET", "/api/me", None, 200,
     ("name", "id", "platform", "share", "share_ver")),
    ("共享只读状态", "GET", "/api/share/status", None, 200,
     ("name", "id", "share_port", "discover", "lan_ip", "inbox_count",
      "share_ver", "allow_legacy", "inbox_max", "peers", "inbox")),
    ("共享只读状态拒绝 POST", "POST", "/api/share/status", {}, 405, None),
    ("共享对端", "GET", "/api/peers", None, 200, ("items", "self", "discover")),
    ("收件箱", "GET", "/api/inbox", None, 200, ("items",)),
    ("收件箱处理", "POST", "/api/inbox/resolve", {"id": "__nope__"}, 200, ("ok",)),
    ("推送坏 peer", "POST", "/api/push", {"peer": "", "ids": []}, 400, ("error",)),
    ("未知路径仍是 404", "GET", "/api/__nope__", None, 404, None),
]

WIN_ONLY = [
    ("VPN 在桌面端 501", "POST", "/api/vpn", {"enable": False}, 501, ("error",)),
    ("悬浮窗在桌面端 501", "GET", "/api/float", None, 501, ("error",)),
]

ANDROID_ONLY = [
    ("系统代理在 Android 501", "POST", "/api/sysproxy", {"enable": False}, 501, ("error",)),
    ("悬浮窗读取", "GET", "/api/float", None, 200, None),
    ("旧路径 GET /api/share 是只读别名", "GET", "/api/share", None, 200,
     ("share_ver", "peers")),
    ("旧路径 POST /api/share 明确 405", "POST", "/api/share", {}, 405, None),
    ("旧路径 /api/share/push", "POST", "/api/share/push", {"peer": "", "ids": []},
     400, ("error",)),
    ("旧路径 /api/share/inbox", "GET", "/api/share/inbox", None, 200, ("items",)),
]


def rec(name, ok, detail=""):
    R.append((name, ok, detail))
    print(("PASS " if ok else "FAIL ") + name + (" | " + detail if detail else ""),
          flush=True)


def http_call(host, port, method, path, body=None, timeout=8):
    data = json.dumps(body).encode("utf-8") if body is not None else b""
    head = ("%s %s HTTP/1.1\r\nHost: %s:%d\r\nContent-Type: application/json\r\n"
            "Content-Length: %d\r\nConnection: close\r\n\r\n"
            % (method, path, host, port, len(data)))
    s = socket.create_connection((host, port), timeout=timeout)
    try:
        s.sendall(head.encode("latin1") + data)
        buf = b""
        while True:
            part = s.recv(65536)
            if not part:
                break
            buf += part
    finally:
        s.close()
    raw_head, _, raw_body = buf.partition(b"\r\n\r\n")
    try:
        status = int(raw_head.split(b" ")[1])
    except Exception:
        status = 0
    try:
        obj = json.loads(raw_body.decode("utf-8")) if raw_body else None
    except Exception:
        obj = raw_body.decode("latin1")
    return status, obj


async def run_table(host, port, table, label):
    for name, method, path, body, want, keys in table:
        try:
            st, obj = await asyncio.to_thread(http_call, host, port,
                                              method, path, body)
        except Exception as e:
            rec("%s %s" % (label, name), False, repr(e))
            continue
        ok = st == want
        detail = "status=%s want=%s" % (st, want)
        if ok and keys is not None:
            if not isinstance(obj, dict):
                ok = False
                detail += " | body=%r" % (obj,)
            else:
                missing = [k for k in keys if k not in obj]
                if missing:
                    ok = False
                    detail += " | missing=%s" % missing
        elif ok and keys is None and path in ("/api/presets",):
            ok = isinstance(obj, list)
            detail += " | type=%s" % type(obj).__name__
        rec("%s %s" % (label, name), ok, detail)


async def run_windows():
    tmp = tempfile.mkdtemp(prefix="wnparity_")
    settings = Path(tmp) / "weaknet_settings.json"
    w._settings_file = lambda: settings
    w.ensure_identity()
    srv = await asyncio.start_server(w.handle_ctrl, "127.0.0.1", 0)
    port = srv.sockets[0].getsockname()[1]
    print("Windows 进程内控制服务 127.0.0.1:%d（设置：%s）" % (port, settings), flush=True)
    try:
        await run_table("127.0.0.1", port, COMMON, "Win")
        await run_table("127.0.0.1", port, WIN_ONLY, "Win")
    finally:
        srv.close()
        await srv.wait_closed()


async def run_android(url):
    u = urllib.parse.urlparse(url)
    host = u.hostname or "127.0.0.1"
    port = u.port or 8899
    print("Android 控制服务 %s:%d" % (host, port), flush=True)
    await run_table(host, port, COMMON, "APK")
    await run_table(host, port, ANDROID_ONLY, "APK")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--android", metavar="URL",
                    help="已 adb forward 的 Android 控制地址，如 http://127.0.0.1:18899")
    ap.add_argument("--win", action="store_true", default=False,
                    help="只跑 Windows（默认：未给 --android 时也跑 Windows）")
    args = ap.parse_args()

    if args.android:
        asyncio.run(run_android(args.android))
    else:
        asyncio.run(run_windows())

    bad = [x for x in R if not x[1]]
    print("\n%d 通过 / %d 失败" % (len(R) - len(bad), len(bad)), flush=True)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
