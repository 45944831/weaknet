"""环境共享端到端验证（本机起两个引擎实例模拟两台机器）

A 与 B 各自跑一套（代理/控制台/共享端口都错开），验证：
  1. 用户名可设置且落盘
  2. 局域网广播发现：A 能看到 B（也能看到 B 的名字与平台）
  3. 推送：A 把自己的自定义环境推给 B，B 直接入库
  4. 同名冲突：再推一次同名环境 → 落进 B 的收件箱，不静默覆盖
  5. 收件箱三种处理：覆盖 / 保留 / 另存
  6. 收下的环境重启后仍在（持久化）
"""
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent
PY = sys.executable
checks = []


def ck(name, cond, extra=""):
    checks.append([name, bool(cond), str(extra)])


def api(port, path, body=None, timeout=10):
    url = "http://127.0.0.1:%d%s" % (port, path)
    if body is None:
        return json.loads(urllib.request.urlopen(url, timeout=timeout).read())
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read())


def wait_up(port, secs=40):
    for _ in range(secs * 4):
        try:
            api(port, "/api/status", timeout=2)
            return True
        except Exception:
            time.sleep(0.25)
    return False


class Eng:
    """每个实例跑在独立目录里：设置文件按脚本所在目录定位，
    共用一份的话两个实例 id 相同，会把自己当成对方忽略掉。"""

    def __init__(self, tag, proxy, ctrl):
        import shutil
        self.tag, self.ctrl = tag, ctrl
        self.dir = BASE / ("_tshare_%s" % tag)
        fresh = not self.dir.exists()
        self.dir.mkdir(exist_ok=True)
        if fresh:                      # 已存在 = 复用（模拟重启），不能覆盖掉已存的数据
            for f in ("weaknet.py", "dashboard.html"):
                shutil.copyfile(str(BASE / f), str(self.dir / f))
        self.p = subprocess.Popen(
            [PY, "weaknet.py", "--proxy-port", str(proxy), "--ctrl-port", str(ctrl)],
            cwd=str(self.dir), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def cleanup_dir(self):
        import shutil
        try:
            shutil.rmtree(str(self.dir))
        except Exception:
            pass

    def stop(self):
        try:
            self.p.terminate()
            self.p.wait(timeout=10)
        except Exception:
            try:
                self.p.kill()
            except Exception:
                pass


def main():
    A = Eng("A", 8990, 8991)
    B = Eng("B", 8992, 8993)
    try:
        ck("两个实例都起来了", wait_up(A.ctrl) and wait_up(B.ctrl))

        # ---- 1. 用户名 ----
        r = api(A.ctrl, "/api/me", {"name": "  kitten的电脑  "})
        ck("设置用户名（空白被压掉）", r["name"] == "kitten的电脑", r["name"])
        ck("/api/me 返回 id 与共享端口", bool(r.get("id")) and r.get("share"),
           json.dumps({k: r.get(k) for k in ("id", "share", "discover")}))
        b = api(B.ctrl, "/api/me", {"name": "测试机B"})
        ck("B 也设了名字", b["name"] == "测试机B", b["name"])
        ck("设置已落盘（实例自己的目录）", (A.dir / "weaknet_settings.json").exists(),
           str(A.dir))

        # ---- 2. 发现 ----
        time.sleep(6)                      # 广播周期 3s，等两轮
        pa = api(A.ctrl, "/api/peers")
        pb = api(B.ctrl, "/api/peers")
        ck("A 发现了 B", any(p["name"] == "测试机B" for p in pa["items"]),
           json.dumps([p["name"] for p in pa["items"]]))
        ck("B 发现了 A", any(p["name"] == "kitten的电脑" for p in pb["items"]),
           json.dumps([p["name"] for p in pb["items"]]))
        ck("列表里没有自己", not any(p["id"] == pa["self"]["id"] for p in pa["items"]))
        peer_b = next((p for p in pa["items"] if p["name"] == "测试机B"), None)
        ck("peer 带 IP 与共享端口", peer_b and peer_b["ip"] and peer_b["share"] > 0,
           json.dumps(peer_b))

        # ---- 3. 推送（新名字，直接入库）----
        it = api(A.ctrl, "/api/upreset",
                 {"name": "地铁3G-共享", "profile": {"down_kbps": 1500, "up_kbps": 512,
                                                    "latency_down_ms": 120, "loss_pct": 3}})
        pid = it["id"]
        ck("A 侧已保存待推送的环境", it["name"] == "地铁3G-共享", it["name"])
        res = api(A.ctrl, "/api/push", {"peer": peer_b, "ids": [pid]}, timeout=20)
        ck("推送成功", res.get("ok"), json.dumps(res))
        ck("对方确认接收", res.get("accepted") == ["地铁3G-共享"], json.dumps(res.get("accepted")))
        got = api(B.ctrl, "/api/upresets")
        names = [x["name"] for x in got["items"]]
        ck("B 侧已能读到该环境", "地铁3G-共享" in names, json.dumps(names))
        recv = next(x for x in got["items"] if x["name"] == "地铁3G-共享")
        ck("参数原样送达", recv["profile"]["down_kbps"] == 1500
           and recv["profile"]["loss_pct"] == 3,
           json.dumps({k: recv["profile"][k] for k in ("down_kbps", "up_kbps", "loss_pct")}))

        # ---- 4. 同名冲突 → 进收件箱 ----
        it2 = api(A.ctrl, "/api/upreset",
                  {"name": "地铁3G-共享", "profile": {"down_kbps": 300, "loss_pct": 20}})
        ck("A 侧重名自动加序号", it2["name"] == "地铁3G-共享 (2)", it2["name"])
        # 用改名的方式拿到同名环境再推：直接改 A 侧这条的名字不方便，改用 inbox 语义验证
        res2 = api(A.ctrl, "/api/push",
                   {"peer": peer_b, "ids": [pid]}, timeout=20)
        ck("同名推送返回 pending", res2.get("ok") and len(res2.get("pending") or []) == 1,
           json.dumps(res2))
        ck("同名推送没有 accepted", not (res2.get("accepted") or []), json.dumps(res2.get("accepted")))
        box = api(B.ctrl, "/api/inbox")
        ck("B 的收件箱里有这条", len(box["items"]) == 1, json.dumps([i["name"] for i in box["items"]]))
        ck("收件箱记录了推送方", box["items"] and box["items"][0]["from"] == "kitten的电脑",
           box["items"][0]["from"] if box["items"] else "")
        before = api(B.ctrl, "/api/upresets")
        ck("未决定前本地环境未被改动",
           next(x for x in before["items"] if x["name"] == "地铁3G-共享")["profile"]["down_kbps"] == 1500)

        # ---- 5. 三种处理：先「保留」，再推一次试「另存」和「覆盖」----
        iid = box["items"][0]["id"]
        r = api(B.ctrl, "/api/inbox/resolve", {"id": iid, "action": "keep"})
        ck("保留：请求被消费", r.get("ok") and r.get("action") == "keep", json.dumps(r))
        ck("保留后收件箱已清空", api(B.ctrl, "/api/inbox")["items"] == [])
        after = api(B.ctrl, "/api/upresets")
        ck("保留后本地参数不变",
           next(x for x in after["items"] if x["name"] == "地铁3G-共享")["profile"]["down_kbps"] == 1500)

        res3 = api(A.ctrl, "/api/push", {"peer": peer_b, "ids": [pid]}, timeout=20)
        iid2 = res3["pending"][0]["id"]
        r = api(B.ctrl, "/api/inbox/resolve",
                {"id": iid2, "action": "rename", "name": "B自己改的名字"})
        ck("另存：用了新名字", r.get("ok") and r.get("name") == "B自己改的名字", json.dumps(r))
        names2 = [x["name"] for x in api(B.ctrl, "/api/upresets")["items"]]
        ck("另存后两条都在", "地铁3G-共享" in names2 and "B自己改的名字" in names2, json.dumps(names2))

        res4 = api(A.ctrl, "/api/push", {"peer": peer_b, "ids": [pid]}, timeout=20)
        iid3 = res4["pending"][0]["id"]
        r = api(B.ctrl, "/api/inbox/resolve", {"id": iid3, "action": "overwrite"})
        ck("覆盖：返回成功", r.get("ok") and r.get("action") == "overwrite", json.dumps(r))
        names3 = [x["name"] for x in api(B.ctrl, "/api/upresets")["items"]]
        ck("覆盖不产生重复条目", names3.count("地铁3G-共享") == 1, json.dumps(names3))

        # ---- 6. 持久化：重启 B 后环境还在 ----
        B.stop()
        time.sleep(1.5)
        B2 = Eng("B", 8994, 8995)       # 同名目录 = 复用数据，模拟重启
        try:
            ok2 = wait_up(B2.ctrl)
            ck("B 重启成功", ok2)
            if ok2:
                names4 = [x["name"] for x in api(B2.ctrl, "/api/upresets")["items"]]
                ck("重启后收到的环境仍在", "地铁3G-共享" in names4, json.dumps(names4))
                ck("重启后用户名仍在", api(B2.ctrl, "/api/me")["name"] == "测试机B",
                   api(B2.ctrl, "/api/me")["name"])
        finally:
            B2.stop()
    except Exception as e:
        ck("测试过程无异常", False, "%s: %s" % (type(e).__name__, e))
    finally:
        A.stop()
        B.stop()
        A.cleanup_dir()
        B.cleanup_dir()


main()
print("=== 环境共享（Windows 引擎）验证 ===")
ok = True
for n, v, e in checks:
    print(("  PASS  " if v else "  FAIL  ") + n + (("  <- " + e) if (e and not v) else ""))
    ok = ok and v
print("总体:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
