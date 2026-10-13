"""验证打包后的 EXE（WeakNet-CLI.exe）功能是否完整打进包里。

被测对象是 dist 出来的单文件 EXE（不是源码 weaknet.py），重点验证：
  1. 自定义栏：保存 / 列表 / 以 u:<id> 应用 / 删除 / 非法值的清洗
  2. 指定程序接管：用本机 python.exe 与 curl.exe 经同一代理各下载一份 128KB，
     scope=only[curl.exe] 时应只有 curl 被限速（决定性用例）
  3. 控制台页面里确实带着新功能（自定义栏、接管目标）所需的 DOM 节点

用法：
  python _t_exe.py                      # 默认测 ./WeakNet-CLI.exe
  python _t_exe.py /abs/path/WeakNet-CLI.exe
"""
import json
import os
import subprocess
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE = os.path.dirname(os.path.abspath(__file__))
EXE = os.path.abspath(sys.argv[1] if len(sys.argv) > 1
                      else os.path.join(BASE, "WeakNet-CLI.exe"))
# 传 .py 就跑源码版（改完代码先拿源码验一遍，再打包验一遍）
CMD = [sys.executable, EXE] if EXE.endswith(".py") else [EXE]
PROXY_PORT = 8960
CTRL_PORT = 8961
FILE_PORT = 8962
PAYLOAD = b"x" * (128 * 1024)
PRESETS_JSON = os.path.join(BASE, "weaknet_presets.json")
RESULTS = []


def ck(name, cond, extra=""):
    RESULTS.append((name, bool(cond)))
    print(("  PASS  " if cond else "  FAIL  ") + name + (("  " + extra) if extra else ""))


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Length", str(len(PAYLOAD)))
        self.end_headers()
        self.wfile.write(PAYLOAD)

    def log_message(self, *a):
        pass


def api(path, body=None, method=None):
    data = json.dumps(body).encode() if body is not None else None
    m = method or ("POST" if data else "GET")
    req = urllib.request.Request("http://127.0.0.1:%d%s" % (CTRL_PORT, path),
                                 data=data, method=m)
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.read().decode()


def japi(path, body=None):
    return json.loads(api(path, body))


OPENER = urllib.request.build_opener(
    urllib.request.ProxyHandler({"http": "127.0.0.1:%d" % PROXY_PORT}))


def fetch_python():
    """用当前进程（python.exe）经代理下载"""
    t0 = time.time()
    with OPENER.open("http://127.0.0.1:%d/data" % FILE_PORT, timeout=90) as r:
        n = len(r.read())
    return time.time() - t0, n


def fetch_curl():
    """用 curl.exe 经代理下载（注意不要传 --noproxy ''，会让 curl 实际不走代理）"""
    t0 = time.time()
    out = subprocess.run(["curl", "-s", "-x", "http://127.0.0.1:%d" % PROXY_PORT,
                          "-o", "-", "http://127.0.0.1:%d/data" % FILE_PORT],
                         capture_output=True, timeout=90)
    return time.time() - t0, len(out.stdout)


def kbs(dt, n):
    return (n / 1024 / dt) if dt > 0 else 0


def main():
    if os.path.exists(PRESETS_JSON):
        os.remove(PRESETS_JSON)          # 避免历史残留干扰计数

    srv = ThreadingHTTPServer(("127.0.0.1", FILE_PORT), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    eng = subprocess.Popen(CMD + ["--proxy-port", str(PROXY_PORT),
                            "--ctrl-port", str(CTRL_PORT)],
                           cwd=BASE, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL)
    try:
        up = False
        for _ in range(60):
            time.sleep(1)
            try:
                urllib.request.urlopen("http://127.0.0.1:%d/api/status" % CTRL_PORT,
                                       timeout=2)
                up = True
                break
            except Exception:
                pass
        print("被测产物:", EXE)
        ck("EXE 可启动并响应控制端口", up)
        if not up:
            return 1

        # ---------- 1. 页面里带着新功能的 UI ----------
        html = urllib.request.urlopen(
            "http://127.0.0.1:%d/" % CTRL_PORT, timeout=10).read().decode("utf-8")
        ck("控制台含自定义栏节点",
           all(k in html for k in ("myBar", "myChips", "saveMyBtn")))
        ck("控制台含接管目标节点",
           all(k in html for k in ("scopeList", "scopeApplyBtn")))

        # ---------- 2. 自定义栏 ----------
        r = japi("/api/upreset", {"name": "EXE验证", "profile": {"down_kbps": 1234,
                                                                 "up_kbps": 300}})
        ck("保存自定义场景", r.get("ok") and r.get("name") == "EXE验证", r.get("name", ""))
        pid = r["id"]

        items = japi("/api/upresets")["items"]
        ck("列表能查到刚保存的", len(items) == 1 and items[0]["id"] == pid)

        dup = japi("/api/upreset", {"name": "EXE验证", "profile": {"down_kbps": 9}})
        ck("重名自动加序号而非静默覆盖", dup["name"] != "EXE验证", dup["name"])

        bad = japi("/api/upreset", {"name": "脏数据",
                                    "profile": {"down_kbps": -5, "loss_pct": 999}})
        prof = [i for i in bad["items"] if i["id"] == bad["id"]][0]["profile"]
        ck("非法参数被清洗到合法区间",
           0 <= prof["down_kbps"] and prof["loss_pct"] <= 100,
           "down=%s loss=%s" % (prof["down_kbps"], prof["loss_pct"]))

        ck("以 u:<id> 应用生效", japi("/api/preset", {"key": "u:" + pid}).get("ok"))
        st = json.loads(api("/api/status"))
        ck("应用后当前场景切换正确",
           st["profile"]["name"] == "EXE验证" and st["profile"]["down_kbps"] == 1234,
           st["profile"]["name"])

        ck("删除已存在的返回 ok", japi("/api/upreset/del", {"id": pid}).get("ok"))
        ck("删除不存在的返回 ok=false",
           japi("/api/upreset/del", {"id": pid}).get("ok") is False)
        for i in japi("/api/upresets")["items"]:
            japi("/api/upreset/del", {"id": i["id"]})
        ck("清空后列表为空", japi("/api/upresets")["items"] == [])

        # ---------- 3. 指定程序接管（真实流量）----------
        japi("/api/preset", {"key": "edge"})
        scene = json.loads(api("/api/status"))["profile"]
        ck("弱网场景已切换为 2G EDGE", scene["name"] == "2G EDGE", scene["name"])
        targets = japi("/api/targets")["items"]
        ck("可取到接管候选进程", len(targets) > 0, "%d 个" % len(targets))

        japi("/api/scope", {"mode": "only", "list": ["curl.exe"]})
        dp, np_ = fetch_python()
        dc, nc = fetch_curl()
        print("      only[curl.exe]  python.exe %.2fs %.0fKB/s | curl.exe %.2fs %.0fKB/s"
              % (dp, kbs(dp, np_), dc, kbs(dc, nc)))
        ck("名单外的 python.exe 直通全速", dp < 1.5)
        ck("名单内的 curl.exe 被限速", dc > 2.0)

        japi("/api/scope", {"mode": "all", "list": []})
        dp2, np2 = fetch_python()
        print("      all              python.exe %.2fs %.0fKB/s" % (dp2, kbs(dp2, np2)))
        ck("全部接管时 python.exe 也被限速", dp2 > 2.0)

        st = json.loads(api("/api/status"))
        ck("引擎观测到来源进程", len(st["scope"]["seen"]) > 0, str(st["scope"]["seen"][:4]))
        ck("直通流量被单独记账", st["stats"]["bypass"] > 0,
           "%d 字节" % st["stats"]["bypass"])

        ok = all(v for _, v in RESULTS)
        print("\n===== 结论：%d/%d 项通过 =====" % (sum(1 for _, v in RESULTS if v),
                                                  len(RESULTS)))
        return 0 if ok else 1
    finally:
        eng.terminate()
        srv.shutdown()
        try:
            if os.path.exists(PRESETS_JSON):
                os.remove(PRESETS_JSON)
        except OSError:
            pass


if __name__ == "__main__":
    sys.exit(main())
