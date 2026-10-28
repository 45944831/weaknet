# -*- coding: utf-8 -*-
"""环境共享里的弱网组合：本机 → 对方全链路自检（不联网，直接调 weaknet 的函数）

两台「机器」用两份不同的 weaknet_presets.json 模拟：A 机有自定义场景 + 组合，
B 机什么都不存在。重点验三件事：
  1. 组合里的段引用了 A 机才有的 u:<id>，B 机收下后要能还原（视为自动建好同名场景）
  2. 组合与自定义环境同名判定互不相干，同名都进收件箱
  3. 收件箱三种处理（覆盖 / 保留 / 另存）对两类都一样有效
"""
import os
import sys

# 仓库根：本文件在 <repo>/tests/ 下，向上两级即仓库根。
# 用相对定位而不是写死绝对路径，换机器/换目录也能直接跑。
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

import json
import os
import shutil
import sys
import tempfile


sys.path.insert(0, ROOT)
import weaknet as W                                            # noqa: E402





# 仓库根：本文件在 <repo>/tests/ 下，向上两级即仓库根。
# 用相对定位而不是写死绝对路径，换机器/换目录也能直接跑。

BOX = None
FAILS = []


def check(name, cond, extra=""):
    print(("  OK   " if cond else "  FAIL ") + name + (("  <- " + str(extra)) if extra else ""))
    if not cond:
        FAILS.append(name)


def use_box(tag):
    """把当前「我在用哪份存档」切到某个目录，并清掉内存缓存"""
    path = os.path.join(BOX, tag, "weaknet_presets.json")
    W._PRESET_FILES = [__import__("pathlib").Path(path)]
    W.G.user_presets = None
    W.G.combos = None
    W.G.inbox = {}
    if os.path.exists(path):
        os.remove(path)


def dump():
    """当前存档全文"""
    return json.loads(open(str(W._PRESET_FILES[0]), encoding="utf-8").read())


def reset_mem():
    W.G.user_presets = None
    W.G.combos = None


print("== 0. 准备：两个互不干扰的临时存档目录 ==")
BOX = tempfile.mkdtemp(prefix="wn-share-combo-")
for t in ("A", "B"):
    os.makedirs(os.path.join(BOX, t), exist_ok=True)

# ---------- A 机：造 1 条自定义场景 + 1 支组合（引用那条自定义 + 两个内置） ----------
use_box("A")
W.ensure_identity()
W.G.user_name = "小张"
p1, _ = W.upsert_user_preset("隧道断流", {"down_kbps": 200, "up_kbps": 100, "loss_pct": 20})
combo_a, _ = W.upsert_combo(
    "地铁通勤",
    [{"key": "5g", "minutes": 0, "seconds": 30},
     {"key": W.USER_PRESET_KEY_PREFIX + p1["id"], "minutes": 1, "seconds": 0},
     {"key": "blackhole", "minutes": 0, "seconds": 5}],
    loop=True)
check("A 机 1 条自定义 + 1 支 3 段组合", len(W.get_user_presets()) == 1
      and len(W.get_combos()) == 1 and len(W.get_combos()[0]["steps"]) == 3)

print("== 1. share_steps：自定义段要连参数快照一起带过去 ==")
steps_out = W.share_steps(W.get_combos()[0])
by_key = {s["key"]: s for s in steps_out}
check("3 段都在，时长照旧", len(steps_out) == 3
      and by_key["5g"]["seconds"] == 30
      and by_key[W.USER_PRESET_KEY_PREFIX + p1["id"]]["minutes"] == 1)
u_step = by_key[W.USER_PRESET_KEY_PREFIX + p1["id"]]
check("自定义段带上了场景名", u_step.get("scene") == "隧道断流", u_step.get("scene"))
check("自定义段带上了整份参数", (u_step.get("profile") or {}).get("down_kbps") == 200,
      u_step.get("profile"))
check("内置段只带名字（不塞冗余参数）",
      by_key["5g"].get("scene") and "profile" not in by_key["5g"])

print("== 2. B 机首收：组合 + 场景都要能落地 ==")
payload = {"from": "小张", "from_id": "a1",
           "items": [{"id": "x", "name": "隧道断流", "profile": p1["profile"]}],
           "combos": [{"id": "c1", "name": "地铁通勤", "loop": True, "steps": steps_out}]}
use_box("B")
W.ensure_identity()
W.G.user_name = "小李"
res = W.receive_share(payload)
check("返回 ok", res.get("ok") is True, res)
# 场景那条 B 机没有同名的，直接入库；组合引用它时应当复用这条而不是再建一条
check("环境直接入库 1 条", len(W.get_user_presets()) == 1, W.get_user_presets())
check("组合直接入库 1 支", len(W.get_combos()) == 1, W.get_combos())
combos_b = W.get_combos()
check("段数没丢", len(combos_b[0]["steps"]) == 3, combos_b[0]["steps"])
check("loop 属性跟着过来", combos_b[0]["loop"] is True)
check("自定义段被映射成本机存在的场景 key",
      all(W.scene_name(s["key"]) != s["key"] for s in combos_b[0]["steps"]),
      [W.scene_name(s["key"]) for s in combos_b[0]["steps"]])
check("没有因为映射而多建自定义场景（复用了先入库那条）",
      len(W.get_user_presets()) == 1, [x["name"] for x in W.get_user_presets()])
check("落到 B 机的段名与 A 机一致",
      [W.scene_name(s["key"]) for s in combos_b[0]["steps"]][1] == "隧道断流")
check("accepted 里组合带 🔀 标记",
      any("🔀" in x for x in res.get("accepted", [])), res.get("accepted"))

print("== 3. 只推组合（不带 items）：对方要自己把引用的场景建出来 ==")
use_box("B2")
res2 = W.receive_share({"from": "小张", "combos": [
    {"name": "地铁通勤", "loop": False, "steps": steps_out}]})
check("只带 combos 也能收下（不再是「没有内容」）", res2.get("ok") is True, res2)
check("被引用的自定义场景自动建好了", len(W.get_user_presets()) == 1,
      [x["name"] for x in W.get_user_presets()])
check("组合存下来了", len(W.get_combos()) == 1)
check("参数被完整还原",
      (W.get_user_presets()[0]["profile"]["down_kbps"] == 200
       and W.get_user_presets()[0]["profile"]["loss_pct"] == 20))

print("== 4. 同名 → 收件箱 pending（环境与组合各判各的） ==")
res3 = W.receive_share(payload)
check("环境重名进收件箱", any(p.get("kind") == "preset" for p in res3.get("pending", [])),
      res3.get("pending"))
check("组合重名进收件箱", any(p.get("kind") == "combo" for p in res3.get("pending", [])),
      res3.get("pending"))
check("两条 pending 各带 kind", len(res3.get("pending", [])) == 2)
check("收件箱条目带上 combo 原始数据",
      any((it.get("combo") or {}).get("steps") for it in W.G.inbox.values()))

print("== 5. 收件箱三种处理 ==")
# === 覆盖 ===
use_box("C")
W.ensure_identity()
W.G.user_name = "小李"
my_combo_before, _ = W.upsert_combo("地铁通勤", [{"key": "wifi", "minutes": 9, "seconds": 0}])
W.receive_share({"from": "小张", "combos": [
    {"name": "地铁通勤", "loop": True, "steps": steps_out}]})
iid_combo = [i["id"] for i in W.G.inbox.values() if i.get("kind") == "combo"][0]
r = W.resolve_inbox(iid_combo, "overwrite")
check("覆盖返回 ok 且是 combo", r.get("ok") and r.get("kind") == "combo", r)
reset_mem()
after = W.find_combo(my_combo_before["id"])
check("覆盖的是原来那条（没多出一条）", len(W.get_combos()) == 1, len(W.get_combos()))
check("内容已被替换成对方的版本", after and len(after["steps"]) == 3
      and after["loop"] is True, after)

# === 保留 ===
use_box("C2")
W.upsert_combo("地铁通勤", [{"key": "wifi", "minutes": 9, "seconds": 0}])
W.receive_share({"from": "小张", "combos": [
    {"name": "地铁通勤", "loop": True, "steps": steps_out}]})
iid_combo = [i["id"] for i in W.G.inbox.values() if i.get("kind") == "combo"][0]
r = W.resolve_inbox(iid_combo, "keep")
check("保留：返回 ok 且不改动本地", r.get("ok") and r.get("action") == "keep", r)
reset_mem()
check("保留后本地仍是 1 段 wifi", len(W.get_combos()[0]["steps"]) == 1
      and W.get_combos()[0]["steps"][0]["key"] == "wifi")

# === 另存为 ===
use_box("C3")
W.upsert_combo("地铁通勤", [{"key": "wifi", "minutes": 9, "seconds": 0}])
W.receive_share({"from": "小张", "combos": [
    {"name": "地铁通勤", "loop": True, "steps": steps_out}]})
iid_combo = [i["id"] for i in W.G.inbox.values() if i.get("kind") == "combo"][0]
r = W.resolve_inbox(iid_combo, "rename", "小张的通勤")
check("另存为新名字", r.get("ok") and r.get("name") == "小张的通勤", r)
reset_mem()
check("原来的那条还在", len(W.get_combos()) == 2, [c["name"] for c in W.get_combos()])
reset_mem()
check("新那条是对方的段", len(W.find_combo(
    [c["id"] for c in W.get_combos() if c["name"] == "小张的通勤"][0])["steps"]) == 3)

print("== 6. 环境与组合可以混在同一条 payload 里互不干扰 ==")
use_box("D")
mix = W.receive_share({"from": "小张",
                       "items": [{"name": "隧道断流", "profile": p1["profile"]}],
                       "combos": [{"name": "地铁通勤", "loop": True, "steps": steps_out}]})
check("混推：环境 1 条 + 组合 1 支",
      len(W.get_user_presets()) == 1 and len(W.get_combos()) == 1, mix)
check("accepted 两个都有", len(mix.get("accepted", [])) == 2, mix.get("accepted"))

print("== 7. 脏数据 / 边界 ==")
use_box("E")
check("空 payload 报没有内容", W.receive_share({}).get("error") == "没有内容")
check("只有空 combos 也算没有内容",
      W.receive_share({"combos": []}).get("error") == "没有内容")
r = W.receive_share({"from": "小张", "combos": [
    {"name": "空壳", "steps": [{"key": "c:xyz", "minutes": 1, "seconds": 0}]}]})
check("整支组合只有嵌套段 → 收不下也不崩", r.get("ok") is True and len(W.get_combos()) == 0, r)
use_box("E2")
r = W.receive_share({"from": "小张", "combos": [
    {"name": "半截", "steps": [
        {"key": "nosuchpreset", "minutes": 1, "seconds": 0},      # 两边都没有的档位
        {"key": "wifi", "minutes": 0, "seconds": 3}]}]})
reset_mem()
check("未知内置档位被丢掉，剩下的段照常入库",
      r.get("ok") and len(W.get_combos()) == 1 and len(W.get_combos()[0]["steps"]) == 1,
      W.get_combos())

print("== 8. 收下来的组合真的能播（地址都有意义） ==")
use_box("F")
W.receive_share({"from": "小张", "combos": [
    {"name": "地铁通勤", "loop": True, "steps": steps_out}]})
reset_mem()
c = W.get_combos()[0]
try:
    prof = W.scene_profile(c["steps"][1]["key"])
    ok = prof.get("down_kbps") == 200
except Exception as e:
    ok = False
    prof = str(e)
check("第 2 段能解析出真实参数（不是空壳）", ok, prof)
check("combo_summary 能完整打出来",
      W.combo_summary(c).count("→") == 2, W.combo_summary(c))

shutil.rmtree(BOX, ignore_errors=True)
print("\n" + ("全部通过 ✅" if not FAILS else "失败 %d 项：%s" % (len(FAILS), "、".join(FAILS))))
sys.exit(1 if FAILS else 0)
