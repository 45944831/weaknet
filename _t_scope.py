"""验证「指定程序接管」：同一代理下，名单内的进程被限速、名单外的直通。

对照设计（场景固定为 2G EDGE，下行 250kbps ≈ 31KB/s）：
  - curl.exe  和 python.exe 都通过同一个代理下载同一个 128KB 文件
  - scope = only [python.exe]  -> curl 应全速（未被接管），python 应被限速
  - scope = exclude [python.exe] -> 反过来：python 全速，curl 被限速
  - scope = all              -> 两者都被限速
"""
import json
import os
import subprocess
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PY = sys.executable
BASE = os.path.dirname(os.path.abspath(__file__))
PROXY_PORT = 8950
CTRL_PORT = 8951
FILE_PORT = 8952
PAYLOAD = b"x" * (128 * 1024)      # 128KB


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Length", str(len(PAYLOAD)))
        self.end_headers()
        self.wfile.write(PAYLOAD)

    def log_message(self, *a):
        pass


def api(path, body=None, method=None):
    import urllib.request as u
    data = json.dumps(body).encode() if body is not None else None
    m = method or ("POST" if data else "GET")
    req = u.Request("http://127.0.0.1:%d%s" % (CTRL_PORT, path), data=data, method=m)
    with u.urlopen(req, timeout=10) as r:
        return r.read().decode()


PROXY_OPENER = urllib.request.build_opener(
    urllib.request.ProxyHandler({"http": "127.0.0.1:%d" % PROXY_PORT}))


def fetch_python():
    """用本进程（python.exe）经代理下载，返回 (耗时, 字节数)"""
    t0 = time.time()
    with PROXY_OPENER.open("http://127.0.0.1:%d/data" % FILE_PORT, timeout=60) as r:
        n = len(r.read())
    return time.time() - t0, n


def fetch_curl():
    """用 curl.exe（另一个进程）经代理下载，返回 (耗时, 字节数)"""
    t0 = time.time()
    # 注意：不要传 --noproxy "" —— 它会干扰 -x 指定的代理，导致 curl 实际没走代理，
    # 测试结果会变成「假通过」（看起来快其实是因为根本没经过引擎）。
    out = subprocess.run(
        ["curl", "-s", "-x", "http://127.0.0.1:%d" % PROXY_PORT,
         "-o", "-", "http://127.0.0.1:%d/data" % FILE_PORT],
        capture_output=True, timeout=60)
    dt = time.time() - t0
    return dt, len(out.stdout)


def kbps(dt, n):
    return (n / 1024 / dt) if dt > 0 else 0


def set_scene(key="edge"):
    api("/api/preset", {"key": key})


def main():
    srv = ThreadingHTTPServer(("127.0.0.1", FILE_PORT), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    eng = subprocess.Popen(
        [PY, os.path.join(BASE, "weaknet.py"),
         "--proxy-port", str(PROXY_PORT), "--ctrl-port", str(CTRL_PORT)],
        cwd=BASE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        time.sleep(3)
        set_scene("edge")
        print("场景:", json.loads(api("/api/status"))["profile"]["name"])
        print("进程候选数:", len(json.loads(api("/api/targets"))["items"]))
        print("当前进程:", os.path.basename(sys.executable), "-> 测试用 python 侧\n")

        results = {}

        # ---- 1. 全部接管 ----
        api("/api/scope", {"mode": "all", "list": []})
        print("[1] scope = all（全部接管，两者都该被限速）")
        dp, np_ = fetch_python()
        dc, nc = fetch_curl()
        print("    python.exe: %.2fs  %.0f KB/s" % (dp, kbps(dp, np_)))
        print("    curl.exe  : %.2fs  %.0f KB/s" % (dc, kbps(dc, nc)))
        results["all_python_slow"] = dp > 2.0
        results["all_curl_slow"] = dc > 2.0

        # ---- 2. 仅接管 python.exe ----
        api("/api/scope", {"mode": "only", "list": ["python.exe"]})
        print("\n[2] scope = only [python.exe]（curl 应直通全速）")
        dp, np_ = fetch_python()
        dc, nc = fetch_curl()
        print("    python.exe: %.2fs  %.0f KB/s  <- 应仍慢" % (dp, kbps(dp, np_)))
        print("    curl.exe  : %.2fs  %.0f KB/s  <- 应全速" % (dc, kbps(dc, nc)))
        results["only_python_slow"] = dp > 2.0
        results["only_curl_fast"] = dc < 1.0

        # ---- 3. 排除 python.exe（应反过来）----
        api("/api/scope", {"mode": "exclude", "list": ["python.exe"]})
        print("\n[3] scope = exclude [python.exe]（python 直通，curl 被限速）")
        dp, np_ = fetch_python()
        dc, nc = fetch_curl()
        print("    python.exe: %.2fs  %.0f KB/s  <- 应全速" % (dp, kbps(dp, np_)))
        print("    curl.exe  : %.2fs  %.0f KB/s  <- 应仍慢" % (dc, kbps(dc, nc)))
        results["exclude_python_fast"] = dp < 1.0
        results["exclude_curl_slow"] = dc > 2.0

        # ---- 3b. 决定性用例：only [curl.exe] ----
        # 若 curl 的连接被正确识别，它会被接管（慢）；
        # 若识别失败（pname=None），only 模式会放行它（快）—— 这一项能区分「真生效」与「假通过」。
        api("/api/scope", {"mode": "only", "list": ["curl.exe"]})
        print("\n[3b] scope = only [curl.exe]（curl 应被接管变慢）")
        dp, np_ = fetch_python()
        dc, nc = fetch_curl()
        print("    python.exe: %.2fs  %.0f KB/s  <- 应全速（不在名单）" % (dp, kbps(dp, np_)))
        print("    curl.exe  : %.2fs  %.0f KB/s  <- 应慢（在名单内）" % (dc, kbps(dc, nc)))
        results["only_curl_python_fast"] = dp < 1.0
        results["only_curl_curl_slow"] = dc > 2.0

        # ---- 4. 观测到的进程名 ----
        st = json.loads(api("/api/status"))
        print("\n[4] 引擎观测到的来源进程:", st["scope"]["seen"][:6])
        print("    直通流量统计:", st["stats"]["bypass"], "字节（应 > 0）")
        results["seen_nonempty"] = len(st["scope"]["seen"]) > 0

        print("\n===== 结论 =====")
        ok = True
        for k, v in results.items():
            print(("  PASS  " if v else "  FAIL  ") + k)
            ok = ok and v
        print("总体:", "PASS" if ok else "FAIL")
        return 0 if ok else 1
    finally:
        eng.terminate()
        srv.shutdown()


if __name__ == "__main__":
    sys.exit(main())
