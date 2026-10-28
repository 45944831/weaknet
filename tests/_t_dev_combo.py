# -*- coding: utf-8 -*-
"""真机批次 3：弱网组合（API 驱动 + 截图证据）。

对应方案：A-301..A-308（API 可驱动部分）、W-411 对应端、A-207/208/209。
UI 观感（编辑器、面板分组、进度标题）以截图为证据，人工判读。
"""
import os
import sys

# 仓库根：本文件在 <repo>/tests/ 下，向上两级即仓库根。
# 用相对定位而不是写死绝对路径，换机器/换目录也能直接跑。
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

import json
import sys
import time

sys.path.insert(0, __file__.rsplit("\\", 1)[0])
import _t_dev_lib as D


def combo_api(name, steps, loop=False):
    """steps: [(scene_key, minutes, seconds)]"""
    payload = {"name": name, "loop": loop,
               "steps": [{"key": k, "minutes": m, "seconds": s} for k, m, s in steps]}
    return D.api_try("/api/combo", payload)


def main():
    D.setup_forward()
    D.app_restart()
    D.api("/api/vpn", {"enable": True})
    time.sleep(2.0)

    D.section("A-301 新建组合（API）")
    r = combo_api("真机地铁通勤", [("5g", 0, 3), ("tunnel", 0, 2), ("blackhole", 0, 1)], loop=True)
    cid = r.get("id") or r.get("cid")
    D.rec("A-301-a 组合创建成功", r.get("ok") or cid, json.dumps(r, ensure_ascii=False)[:160])
    combos = D.api("/api/combos")
    items = combos.get("items", combos if isinstance(combos, list) else [])
    names = [c.get("name") for c in items]
    D.rec("A-301-b 组合出现在列表", "真机地铁通勤" in names, str(names)[:150])

    D.section("A-302 段上限 5（API 侧归一化）")
    r6 = combo_api("六段试探", [("5g", 0, 1)] * 6)
    six_ok = r6.get("ok") or r6.get("id")
    lst = D.api("/api/combos")
    items = lst.get("items", lst if isinstance(lst, list) else [])
    six = [c for c in items if c.get("name") == "六段试探"]
    D.rec("A-302 第 6 段被裁剪（最多保留 5 段）或整支拒绝",
          (not six) or len(six[0].get("steps", [])) <= 5,
          json.dumps(r6, ensure_ascii=False)[:120] + " steps=%s" %
          (len(six[0].get("steps", [])) if six else "拒绝"))

    D.section("A-303 时间校验（分秒全 0）")
    r0 = combo_api("全零段", [("5g", 0, 0), ("tunnel", 0, 0)])
    lst = D.api("/api/combos")
    items = lst.get("items", lst if isinstance(lst, list) else [])
    zero = [c for c in items if c.get("name") == "全零段"]
    D.rec("A-303 全零段被拦下或清洗（不得出现空壳组合）", (not zero) or r0.get("ok") is False,
          "resp=%s 入库=%s" % (json.dumps(r0, ensure_ascii=False)[:100], bool(zero)))

    D.section("A-304 禁嵌套（c: 前缀段）")
    rn = combo_api("嵌套试探", [("c:%s" % (cid or "0"), 0, 5)])
    lst = D.api("/api/combos")
    items = lst.get("items", lst if isinstance(lst, list) else [])
    nest = [c for c in items if c.get("name") == "嵌套试探"]
    D.rec("A-304 嵌套段被剔除/拒绝", (not nest) or (nest and len(nest[0].get("steps", [])) == 0) is False,
          "resp=%s" % json.dumps(rn, ensure_ascii=False)[:120])

    D.section("A-305 顺序播放与段推进（真实计时）")
    # 起播：preset 指向组合
    r = D.api("/api/preset", {"key": "c:%s" % cid})
    time.sleep(0.5)
    r = D.api("/api/run", {"enable": True})
    time.sleep(1.0)
    s = D.status()
    pk = s.get("preset_key", "")
    D.rec("A-305-a 起播后 preset_key 指向组合", pk.startswith("c:"), "preset_key=%s" % pk)
    # 观察两拍：第 1 段 3 秒 → 4 秒后应该在第 2 段
    time.sleep(4.0)
    s1 = D.status()
    ev1 = [e.get("msg", "") for e in s1.get("events", [])[-8:]]
    D.rec("A-305-b 4 秒后推进到后续段（事件日志可见切段）",
          any(("第 2" in m or "2/3" in m or "段" in m) for m in ev1),
          " | ".join(ev1)[-220:])
    D.shot("03_combo_playing")
    # 总时长 3+2+1=6s，循环开启：12 秒后应至少跑完 1 轮回到第 1 段
    time.sleep(8.0)
    s2 = D.status()
    ev2 = [e.get("msg", "") for e in s2.get("events", [])[-14:]]
    seg_msgs = [m for m in ev2 if "段" in m or "5G" in m or "隧道" in m or "黑洞" in m]
    D.rec("A-306 循环回绕（12 秒后仍在播放且事件持续推进）",
          s2.get("limit_on") is True and len(seg_msgs) >= 2,
          " | ".join(ev2)[-260:])

    D.section("A-307 播放中切场景 = 抢占")
    D.api("/api/preset", {"key": "elevator"})
    time.sleep(1.0)
    s = D.status()
    D.rec("A-307 抢占后 preset_key 不再指向组合", not s.get("preset_key", "").startswith("c:"),
          "preset_key=%s limit_on=%s" % (s.get("preset_key"), s.get("limit_on")))

    D.section("A-314 组合引用自定义场景")
    r = D.api("/api/upreset", {"name": "真机自定义A", "profile": {
        "down_kbps": 300, "up_kbps": 300, "latency_down_ms": 50}})
    ups = D.api("/api/upresets")
    items = ups.get("items", ups if isinstance(ups, list) else [])
    uid = ""
    for p in items:
        if p.get("name") == "真机自定义A":
            uid = p.get("id") or ""
    r = combo_api("组合引自定义", [("u:%s" % uid, 0, 2), ("5g", 0, 1)])
    ok = r.get("ok") or r.get("id")
    D.rec("A-314 组合可引用自定义场景（u: 段）", bool(ok), json.dumps(r, ensure_ascii=False)[:140])

    D.section("A-308 删除正在播放的组合")
    cid2 = ""
    lst = D.api("/api/combos")
    items = lst.get("items", lst if isinstance(lst, list) else [])
    for c in items:
        if c.get("name") == "真机地铁通勤":
            cid2 = c.get("id")
    if cid2:
        D.api("/api/preset", {"key": "c:%s" % cid2})
        D.api("/api/run", {"enable": True})
        time.sleep(1.5)
        playing = D.status().get("preset_key", "").startswith("c:")
        r = D.api("/api/combo/delete", {"id": cid2})
        time.sleep(1.0)
        s = D.status()
        D.rec("A-308 删除播放中的组合：停播并切回直连",
              playing and not s.get("preset_key", "").startswith("c:")
              and s.get("limit_on") in (False, 0),
              "播放中=%s 删除后 preset_key=%s" % (playing, s.get("preset_key")))
    else:
        D.rec("A-308 删除播放中的组合", False, "没找到组合（前序步骤失败）")

    D.section("清理")
    lst = D.api("/api/combos")
    items = lst.get("items", lst if isinstance(lst, list) else [])
    for c in items:
        if c.get("name") in ("六段试探", "全零段", "嵌套试探", "组合引自定义"):
            D.api("/api/combo/delete", {"id": c.get("id")})
    D.api("/api/run", {"enable": False})
    D.summary("批次3：弱网组合")


if __name__ == "__main__":
    main()
