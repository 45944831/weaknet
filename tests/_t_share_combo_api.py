# -*- coding: utf-8 -*-
"""环境共享 + 弱网组合 的接口层自检（Windows 端 /api/push、/api/inbox、/api/inbox/resolve）。

单机进程模拟两台机器：靠切换 _preset_files 指向的存储文件来隔离 A 侧 / B 侧。
发送端和接收端必须分两段跑，中间不能复用同一份存储。

覆盖：
  1. 只勾组合不勾环境时，payload 仍带 combos（不能因为 items 空就短路）
  2. 推送组合后对端按「组合」收下，不混进环境列表
  3. 收件箱里组合 / 环境各归各类
  4. 收件箱三种处理（覆盖/保留/另存）都能落地，且组合段的参数是真实值
  5. 组合不能嵌套（推过去的段里不带 c: 前缀）
  6. 边界：空 payload、没选任何东西、段引用不存在的场景
"""
import asyncio
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
import weaknet as W  # noqa: E402

OK = []
BAD = []


def check(name, cond, extra=""):
    (OK if cond else BAD).append(name)
    print("  %s %s%s" % ("OK  " if cond else "FAIL", name,
                         ("  <- " + str(extra)) if extra else ""))


def use_store(tag):
    """切到一份全新的空存储（模拟换了一台机器）"""
    p = pathlib.Path(tempfile.mkdtemp()) / ("weaknet_%s.json" % tag)
    W._preset_files = lambda: [p]
    W.G.user_presets = None
    W.G.combos = None
    W.G.inbox = {}
    return p


async def main():
    print("== 1. A 侧：建自定义环境 + 引用它的组合 ==")
    use_store("A")
    W.ensure_identity()
    W.G.user_name = "小张"
    up, _ = W.upsert_user_preset("隧道断流", {"down_kbps": 200, "up_kbps": 100,
                                             "loss_pct": 20})
    combo, _ = W.upsert_combo("地铁通勤", [{"key": "5g", "minutes": 0, "seconds": 30},
                                           {"key": "u:" + up["id"], "minutes": 1, "seconds": 0}],
                               loop=True)
    check("A 侧建好自定义环境", up["name"] == "隧道断流")
    check("A 侧建好组合", combo["name"] == "地铁通勤")
    check("组合引用了自定义段", combo["steps"][1]["key"].startswith("u:"))
    combo_id = combo["id"]

    print("\n== 2. A 侧：只推组合（不勾环境）==")
    captured = {}

    def fake_post(ip, port, payload):
        captured.clear()
        captured.update(payload)
        return {"ok": True, "name": "小李", "accepted": ["地铁通勤"], "pending": []}

    W._post_share = fake_post
    peer = {"ip": "127.0.0.1", "port": 8900, "name": "小李"}
    res = await W.push_to_peer(peer, [], [combo_id])
    check("只推组合也推送成功", res.get("ok") is True, res.get("error"))
    check("payload 含 combos", "combos" in captured, list(captured.keys()))
    check("payload 含 items（空数组也在）", "items" in captured, list(captured.keys()))
    c0 = (captured.get("combos") or [{}])[0]
    check("combo 带 name", c0.get("name") == "地铁通勤", c0.get("name"))
    check("combo 带 loop", c0.get("loop") is True, c0.get("loop"))
    steps = c0.get("steps") or []
    check("combo 带 2 段", len(steps) == 2, len(steps))
    s0, s1 = (steps + [{}, {}])[:2]
    check("内置段带 key", s0.get("key") == "5g", s0.get("key"))
    check("内置段带场景名", bool(s0.get("scene")), s0.get("scene"))
    check("自定义段带 key", s1.get("key") == "u:" + up["id"], s1.get("key"))
    check("自定义段带场景名", bool(s1.get("scene")), s1.get("scene"))
    check("自定义段带参数快照",
          isinstance(s1.get("profile"), dict) and s1["profile"].get("down_kbps") == 200,
          s1.get("profile"))
    check("段里没有嵌套组合（不带 c: 前缀）",
          all(not str(s.get("key", "")).startswith("c:") for s in steps))
    shared_steps = steps

    print("\n== 3. B 侧：收下这支组合 ==")
    use_store("B")
    res_b = W.receive_share({"from": "小张", "combos": [
        {"name": "地铁通勤", "loop": True, "steps": shared_steps}]})
    check("B 侧收下成功", res_b.get("ok") is True, res_b)
    check("B 侧 accepted 里有组合名（带 🔀 标记）",
          res_b.get("accepted") == ["🔀 地铁通勤"], res_b.get("accepted"))
    check("B 侧组合入库了", len(W.get_combos()) == 1, len(W.get_combos()))
    got = W.get_combos()[0]
    check("B 侧组合名字对", got["name"] == "地铁通勤", got["name"])
    check("B 侧组合段数对", len(got["steps"]) == 2, len(got["steps"]))
    check("B 侧组合 loop 保留", got.get("loop") is True)
    # 关键：自定义段必须还原成真实参数，而不是空壳。
    # 组合段要在本机落地一条同名自定义场景，否则这一段无从播放（引擎设计如此）。
    p_by_key = {p["name"]: p["profile"] for p in W.get_user_presets()}
    check("B 侧自定义段被还原成一条环境", "隧道断流" in p_by_key, list(p_by_key))
    if "隧道断流" in p_by_key:
        check("还原后参数是真的", p_by_key["隧道断流"].get("down_kbps") == 200,
              p_by_key["隧道断流"])
    check("B 侧组合引用的段指向本机环境",
          any(s["key"].startswith("u:") for s in got["steps"]),
          [s["key"] for s in got["steps"]])

    print("\n== 4. B 侧：同名 → 进收件箱（先在本地造出同名项）==")
    use_store("B2")
    # 本地已有同名组合 + 同名环境，两者都要撞名进收件箱
    W.upsert_combo("地铁通勤", [{"key": "5g", "minutes": 0, "seconds": 10}])
    W.upsert_user_preset("隧道断流", {"down_kbps": 200})
    n_combo_before = len(W.get_combos())
    n_preset_before = len(W.get_user_presets())
    W.receive_share({"from": "小张", "combos": [
        {"name": "地铁通勤", "loop": True, "steps": shared_steps}]})
    W.receive_share({"from": "小张", "items": [
        {"name": "隧道断流", "profile": {"down_kbps": 200}}]})
    kinds = sorted((it.get("kind") or "preset") for it in W.G.inbox.values())
    check("收件箱里有 2 条", len(W.G.inbox) == 2, len(W.G.inbox))
    check("一条环境一条组合", kinds == ["combo", "preset"], kinds)
    check("同名组合没被静默覆盖", len(W.get_combos()) == n_combo_before,
          len(W.get_combos()))
    check("同名环境没被静默覆盖", len(W.get_user_presets()) == n_preset_before,
          len(W.get_user_presets()))

    print("\n== 5. /api/inbox 返回结构（与引擎同一段组装逻辑）==")
    rows = []
    for it in sorted(W.G.inbox.values(), key=lambda x: x["at"]):
        row = {"id": it["id"], "from": it["from"], "name": it["name"],
               "kind": it.get("kind") or "preset", "ago": 0}
        if row["kind"] == "combo":
            row["combo"] = it.get("combo") or {}
        else:
            row["profile"] = it.get("profile") or {}
        rows.append(row)
    crow = [r for r in rows if r["kind"] == "combo"][0]
    prow = [r for r in rows if r["kind"] == "preset"][0]
    check("组合条目带 combo 载荷", bool(crow["combo"]), crow["combo"])
    check("环境条目带 profile", bool(prow["profile"]), prow["profile"])
    check("组合载荷里含 steps", bool((crow["combo"] or {}).get("steps")),
          (crow["combo"] or {}).get("steps"))
    check("组合载荷里含 loop", "loop" in (crow["combo"] or {}))

    print("\n== 6. 收件箱三种处理 ==")
    # 保留（本地本来就有同名组合，保留后不应多出新条目）
    n_combo_0 = len(W.get_combos())
    n_preset_0 = len(W.get_user_presets())
    cid = [i["id"] for i in W.G.inbox.values() if i.get("kind") == "combo"][0]
    r = W.resolve_inbox(cid, "keep")
    check("保留本地：ok", r.get("ok") is True, r)
    check("保留本地：返回 kind=combo", r.get("kind") == "combo", r.get("kind"))
    check("保留本地：组合没多出来", len(W.get_combos()) == n_combo_0,
          (n_combo_0, len(W.get_combos())))
    check("保留本地：也没白留一堆自定义场景",
          len(W.get_user_presets()) == n_preset_0,
          (n_preset_0, [p["name"] for p in W.get_user_presets()]))

    # 另存
    W.receive_share({"from": "小张", "combos": [
        {"name": "地铁通勤", "loop": True, "steps": shared_steps}]})
    cid = [i["id"] for i in W.G.inbox.values() if i.get("kind") == "combo"][0]
    r = W.resolve_inbox(cid, "rename", "小张版通勤")
    check("另存为：ok", r.get("ok") is True, r)
    check("另存为：名字对", r.get("name") == "小张版通勤", r.get("name"))
    names = [c["name"] for c in W.get_combos()]
    check("另存为：库里多了一支", "小张版通勤" in names, names)
    got = [c for c in W.get_combos() if c["name"] == "小张版通勤"][0]
    check("另存为：段数对", len(got["steps"]) == 2, len(got["steps"]))
    check("另存为：loop 保留", got.get("loop") is True)

    print("\n== 7. 覆盖 ==")
    use_store("B3")
    W.upsert_combo("地铁通勤", [{"key": "5g", "minutes": 0, "seconds": 10}])
    before = [c for c in W.get_combos() if c["name"] == "地铁通勤"][0]
    W.receive_share({"from": "小张", "combos": [
        {"name": "地铁通勤", "loop": True, "steps": shared_steps}]})
    cid = [i["id"] for i in W.G.inbox.values() if i.get("kind") == "combo"][0]
    r = W.resolve_inbox(cid, "overwrite")
    check("覆盖：ok", r.get("ok") is True, r)
    after = [c for c in W.get_combos() if c["name"] == "地铁通勤"]
    check("覆盖：还是一支", len(after) == 1, len(after))
    check("覆盖：id 没变", after[0]["id"] == before["id"])
    check("覆盖：段数变成 2", len(after[0]["steps"]) == 2, len(after[0]["steps"]))
    check("覆盖：loop 变成 True", after[0].get("loop") is True)

    print("\n== 8. 边界 ==")
    use_store("C")
    r = W.receive_share({})
    check("空 payload 报没有内容", r.get("error") == "没有内容", r)
    r = W.receive_share({"combos": []})
    check("空 combos 也报没有内容", r.get("error") == "没有内容", r)
    r = await W.push_to_peer(peer, [], [])
    check("什么都没选 → 报错", r.get("ok") is False, r)

    print("\n== 9. 一段都落不下来的组合 → 必须有回执 ==")
    # 这台的机器上没有「幽灵」这一档，也没带参数快照 → 对方没法播这个组合。
    # 关键：不能静默丢弃 —— 推送方要收到「对方收不下」的回执，否则以为推成功了。
    use_store("D")
    res_d = W.receive_share({"from": "小张", "combos": [
        {"name": "坏组合", "loop": False,
         "steps": [{"key": "u:nope", "minutes": 1, "seconds": 0, "scene": "幽灵"}]}]})
    check("收不下的组合：返回 ok", res_d.get("ok") is True, res_d)
    check("收不下的组合：进了 dropped 回执",
          any(d.get("name") == "坏组合" for d in (res_d.get("dropped") or [])),
          res_d.get("dropped"))
    check("收不下的组合：没进 accepted",
          "坏组合" not in [str(x) for x in (res_d.get("accepted") or [])],
          res_d.get("accepted"))
    check("收不下的组合：没进 pending",
          "坏组合" not in [str(p.get("name")) for p in (res_d.get("pending") or [])],
          res_d.get("pending"))
    check("收不下的组合：本地没建出来",
          len([c for c in W.get_combos() if c["name"] == "坏组合"]) == 0,
          [c["name"] for c in W.get_combos()])
    check("回执里带了原因", bool((res_d.get("dropped") or [{}])[0].get("reason")),
          res_d.get("dropped"))

    # 推送方视角：模拟对方返回 dropped，验证 push_to_peer 会把它透出来
    use_store("E")
    W.upsert_combo("坏组合", [{"key": "5g", "minutes": 0, "seconds": 10}])
    cid2 = W.get_combos()[0]["id"]

    def fake_post3(ip, port, payload):
        return {"ok": True, "name": "小李", "accepted": [],
                "pending": [], "dropped": [{"name": "坏组合", "reason": "本机没有这一档"}]}

    W._post_share = fake_post3
    res_e = await W.push_to_peer(peer, [], [cid2])
    check("推送方收到 dropped 回执", bool(res_e.get("dropped")), res_e)
    check("推送方能看到是哪支组合",
          res_e.get("dropped") and res_e["dropped"][0].get("name") == "坏组合",
          res_e.get("dropped"))

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
