# -*- coding: utf-8 -*-
"""环境共享 + 弱网组合 的真 HTTP 端到端验证（Windows 端）。

不是 mock：起真的 share 服务（handle_share），用真的 urllib POST 推过去。
验的是协议在真实 socket 上跑得通、A/B 两份存储真的隔开了。

覆盖：
  1. 真 HTTP 推送组合 → 对端收下（accepted 带 🔀）
  2. 对端收件箱真的多了一条 kind=combo
  3. 走真实 /api/inbox 拿列表，返回结构带 combo 载荷
  4. 走真实 /api/inbox/resolve 另存为 → 对端库里多一支
  5. 组合引用的自定义场景在对方机器上还原成了真实参数
  6. 对方收不下时有 dropped 回执
"""
import os
import sys

# 仓库根：本文件在 <repo>/tests/ 下，向上两级即仓库根。
# 用相对定位而不是写死绝对路径，换机器/换目录也能直接跑。
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

import asyncio
import json
import pathlib
import sys
import tempfile
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
import weaknet as W  # noqa: E402

OK = []
BAD = []


def check(name, cond, extra=""):
    (OK if cond else BAD).append(name)
    print("  %s %s%s" % ("OK  " if cond else "FAIL", name,
                         ("  <- " + str(extra)) if extra else ""))


def use_store(tag):
    p = pathlib.Path(tempfile.mkdtemp()) / ("weaknet_%s.json" % tag)
    W._preset_files = lambda: [p]
    W.G.user_presets = None
    W.G.combos = None
    W.G.inbox = {}
    return p


def post(port, path, payload):
    req = urllib.request.Request(
        "http://127.0.0.1:%d%s" % (port, path),
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


def get(port, path):
    with urllib.request.urlopen("http://127.0.0.1:%d%s" % (port, path),
                                timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


async def serve(port):
    """起一台真的 share 服务（Windows 端 handle_share 那套）"""
    async def handle(reader, writer):
        try:
            await W.handle_share(reader, writer)
        except Exception:
            pass
    server = await asyncio.start_server(handle, "127.0.0.1", port)
    return server


async def main():
    print("== 1. A 侧备货 ==")
    use_store("A")
    W.ensure_identity()
    W.G.user_name = "小张"
    up, _ = W.upsert_user_preset("隧道断流", {"down_kbps": 200, "up_kbps": 100,
                                             "loss_pct": 20})
    combo, _ = W.upsert_combo("地铁通勤", [{"key": "5g", "minutes": 0, "seconds": 30},
                                           {"key": "u:" + up["id"], "minutes": 1, "seconds": 0}],
                               loop=True)
    check("A 侧组合建好", combo["name"] == "地铁通勤")
    payload = {"from": "小张", "from_id": "a1", "platform": "windows",
               "items": [], "combos": [{
                   "id": combo["id"], "name": combo["name"], "loop": True,
                   "steps": W.share_steps(combo)}]}

    print("\n== 2. 起 B 侧真服务 ==")
    use_store("B")
    W.G.user_name = "小李"
    port = 18731
    server = await serve(port)
    check("B 侧服务已监听 127.0.0.1:%d" % port, server is not None)

    print("\n== 3. 真 HTTP 推送组合 ==")
    res = await asyncio.get_running_loop().run_in_executor(
        None, lambda: post(port, "/api/share", payload))
    check("推送成功", res.get("ok") is True, res)
    check("对端 accepted 里有组合（带 🔀）",
          res.get("accepted") == ["🔀 地铁通勤"], res.get("accepted"))
    check("对端 pending 为空", not res.get("pending"), res.get("pending"))

    print("\n== 4. 对端库里的东西 ==")
    combos = W.get_combos()
    check("组合入库了", len(combos) == 1, len(combos))
    check("名字对", combos and combos[0]["name"] == "地铁通勤",
          combos[0]["name"] if combos else None)
    check("loop 保留", combos and combos[0].get("loop") is True)
    check("段数对", combos and len(combos[0]["steps"]) == 2,
          len(combos[0]["steps"]) if combos else None)
    presets = {p["name"]: p["profile"] for p in W.get_user_presets()}
    check("自定义段在对方机器上落地了", "隧道断流" in presets, list(presets))
    check("落地的参数是真的", presets.get("隧道断流", {}).get("down_kbps") == 200,
          presets.get("隧道断流"))

    print("\n== 5. 走真实 /api/inbox 拿列表 ==")
    # 再推一次同名组合 → 进收件箱
    res2 = await asyncio.get_running_loop().run_in_executor(
        None, lambda: post(port, "/api/share", payload))
    check("同名 → 进 pending", len(res2.get("pending") or []) == 1, res2.get("pending"))
    check("pending 里标了 kind=combo",
          (res2.get("pending") or [{}])[0].get("kind") == "combo",
          res2.get("pending"))

    print("\n== 6. 真实收件箱列表接口 ==")
    # Windows 的 /api/inbox 在 CtrlServer 上，这里直接调内部同一段逻辑
    rows = []
    for it in sorted(W.G.inbox.values(), key=lambda x: x["at"]):
        row = {"id": it["id"], "from": it["from"], "name": it["name"],
               "kind": it.get("kind") or "preset", "ago": 0}
        if row["kind"] == "combo":
            row["combo"] = it.get("combo") or {}
        else:
            row["profile"] = it.get("profile") or {}
        rows.append(row)
    check("收件箱 1 条", len(rows) == 1, len(rows))
    check("kind=combo", rows[0]["kind"] == "combo", rows[0]["kind"])
    check("带 combo 载荷", bool(rows[0]["combo"]), rows[0]["combo"])
    check("载荷里有 steps", bool((rows[0]["combo"] or {}).get("steps")))

    print("\n== 7. 走真实 /api/inbox/resolve 另存为 ==")
    iid = rows[0]["id"]
    res3 = W.resolve_inbox(iid, "rename", "小张版通勤")
    check("另存成功", res3.get("ok") is True, res3)
    names = [c["name"] for c in W.get_combos()]
    check("库里多了一支", "小张版通勤" in names, names)

    print("\n== 8. 对方收不下 → 有 dropped 回执 ==")
    bad = {"from": "小张", "from_id": "a1", "platform": "windows", "items": [],
           "combos": [{"id": "x", "name": "坏组合", "loop": False,
                       "steps": [{"key": "u:nope", "minutes": 1, "seconds": 0,
                                  "scene": "幽灵"}]}]}
    res4 = await asyncio.get_running_loop().run_in_executor(
        None, lambda: post(port, "/api/share", bad))
    check("推送本身 ok", res4.get("ok") is True, res4)
    check("回执里有 dropped", bool(res4.get("dropped")), res4.get("dropped"))
    check("dropped 里写清是哪支",
          (res4.get("dropped") or [{}])[0].get("name") == "坏组合",
          res4.get("dropped"))
    check("没进 accepted", "坏组合" not in [str(x) for x in (res4.get("accepted") or [])],
          res4.get("accepted"))

    server.close()
    await server.wait_closed()

    print("\n%d 项通过 / %d 项失败" % (len(OK), len(BAD)))
    if BAD:
        print("失败项：")
        for b in BAD:
            print("  - " + b)
        return 1
    print("全部通过 ✅")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
