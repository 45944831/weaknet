# -*- coding: utf-8 -*-
"""弱网组合编辑器回归测试（基于 uiautomator 控件树定位）。

覆盖：行内小 ✕ 删除 / ＋添加一段 / 场景名选择 / 保存落盘 / 左滑删除。
"""
import os
import sys

# 仓库根：本文件在 <repo>/tests/ 下，向上两级即仓库根。
# 用相对定位而不是写死绝对路径，换机器/换目录也能直接跑。
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

import json
import os
import re
import sys
import time

sys.path.insert(0, HERE)
import _t_dev_lib as L



XML = os.path.join(ROOT, "_shots", "ui_z.xml")


def dump_tree(retries=4):
    """抓 uiautomator 树并解析成节点列表（每项含 text/desc/bounds/clickable）。

    dump 抓的是「当前前台窗口」的控件树。前台若被别的 App 抢走（设备上常驻着
    悬浮时钟、系统设置弹窗这类），拿到的就是那个 App 的界面，元素定位全落空。
    所以每次抓树前先确认前台是本 App。

    另外，第三方悬浮窗（毫秒悬浮时钟之类）会盖在界面上，且 **不出现在控件树里** ——
    点它等于点空气。这类干扰要先清掉。
    """
    for _ in range(retries):
        L.ensure_foreground()
        L.dismiss_overlays()
        L.sh("uiautomator dump /sdcard/ui_z.xml")
        time.sleep(0.6)
        out = L.sh("cat /sdcard/ui_z.xml")
        if out and "node" in out:
            nodes = []
            for m in re.finditer(r'<node[^>]*?>', out):
                tag = m.group(0)
                def attr(k):
                    mm = re.search(k + r'="([^"]*)"', tag)
                    return mm.group(1) if mm else ""
                b = re.search(r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', tag)
                if not b:
                    continue
                x1, y1, x2, y2 = map(int, b.groups())
                nodes.append({
                    "text": attr("text"), "desc": attr("content-desc"),
                    "cls": attr("class"), "click": attr("clickable") == "true",
                    "b": (x1, y1, x2, y2),
                    "c": ((x1 + x2) // 2, (y1 + y2) // 2),
                })
            if nodes:
                return nodes
    return []


def by_desc(nodes, desc):
    return [n for n in nodes if n["desc"] == desc]


def by_text(nodes, text):
    return [n for n in nodes if n["text"] == text]


def tap(node, wait=1.0):
    x, y = node["c"]
    L.sh("input tap %d %d" % (x, y))
    time.sleep(wait)


def open_editor():
    # 先重启 App（顺带把控制服务拉起来），再停弱网：
    # 限速飘窗是系统级窗口，会盖在界面上半部分，
    # 「组合」标签正好在飘窗底下，点它会被飘窗吃掉（表现为「点了没反应」）。
    L.app_restart(wait=4.0)
    L.setup_forward()
    time.sleep(0.8)
    L.api_try("/api/vpn", {"enable": False})
    time.sleep(1.0)
    for _ in range(8):
        L.sh("input swipe 540 1800 540 900 260")
        time.sleep(0.25)
    time.sleep(0.5)
    # 「组合」标签页：优先用控件树定位，dump 抽风时退回固定坐标
    nodes = dump_tree()
    tab = by_text(nodes, "组合")
    if tab:
        tap(tab[0], wait=1.2)
    else:
        L.sh("input tap 775 560")
        time.sleep(1.2)
    # 长按第一支组合打开编辑器：用控件树定位，不写死坐标
    # （卡片位置会随组合数量、名称长度变化，写死坐标十有八九点空）
    nodes = dump_tree()
    cards = [n for n in nodes if n["text"] and "面板测试组合" in n["text"]]
    if not cards:
        print("  ! 没找到组合卡片，节点文本：",
              [n["text"] for n in nodes if n["text"]][:12])
        return False
    x, y = cards[0]["c"]
    L.sh("input swipe %d %d %d %d 900" % (x, y, x, y))   # 长按
    time.sleep(1.6)
    return True


def main():
    ok = 0
    total = 0

    def check(name, cond, detail=""):
        nonlocal ok, total
        total += 1
        if cond:
            ok += 1
        print(("  PASS " if cond else "  FAIL ") + name + ((" | " + detail) if detail else ""),
              flush=True)

    open_editor()
    nodes = dump_tree()
    names = [n["text"] for n in nodes if n["desc"] == "选择这一段用的场景"]
    print("\n=== 结构 ===")
    print("  段数：", len(names))
    print("  各段场景名：", names)

    check("三段都在", len(names) == 3, "实际 %d" % len(names))
    check("场景名完整显示（没有省略号）",
          all(n and "…" not in n and "..." not in n for n in names), str(names))
    check("行里没有常驻删除按钮（统一走左滑）",
          len([n for n in nodes if n["desc"] == "删除这一段"]) == 0,
          str([n["desc"] for n in nodes if n["desc"] == "删除这一段"]))
    # 场景名框 + 时长区必须在同一行（垂直区间重叠）
    name_nodes = by_desc(nodes, "选择这一段用的场景")
    sec_nodes = [n for n in nodes if n["text"] == "秒"]
    check("每行是「场景名 + 分 + 秒」一行",
          len(name_nodes) == len(sec_nodes) and
          all(n["b"][1] < s["b"][3] and s["b"][1] < n["b"][3]
              for n, s in zip(name_nodes, sec_nodes)),
          str([(n["b"], s["b"]) for n, s in zip(name_nodes, sec_nodes)]))

    # 1) 左滑某一行 → 露出「删除」块 → 点它删掉这一段
    print("\n=== 1. 左滑删除 ===")
    tgt = by_desc(nodes, "选择这一段用的场景")[1]
    x1, y1, x2, y2 = tgt["b"]
    cy = (y1 + y2) // 2
    L.sh("input swipe %d %d %d %d 350" % (x2 - 60, cy, x2 - 320, cy))
    time.sleep(1.0)
    nodes_sw = dump_tree()
    del_btn = [n for n in nodes_sw if n["desc"] == "删除这一段"]
    check("左滑后露出删除按钮", len(del_btn) == 1, "找到 %d 个" % len(del_btn))
    if del_btn:
        tap(del_btn[0], wait=1.0)
    nodes2 = dump_tree()
    check("删掉一段", len(by_desc(nodes2, "选择这一段用的场景")) == 2,
          "剩余 %d" % len(by_desc(nodes2, "选择这一段用的场景")))

    # 2) ＋添加一段
    print("\n=== 2. 添加一段 ===")
    add = [n for n in nodes2 if n["click"] and n["b"][1] > 1300 and n["b"][1] < 1520]
    if add:
        tap(min(add, key=lambda n: n["b"][1]))
    nodes3 = dump_tree()
    if len(by_desc(nodes3, "选择这一段用的场景")) == 0:
        time.sleep(1.2)          # 新增的行可能还没布局完，或软键盘刚弹出
        nodes3 = dump_tree()
    check("添加后恢复三段", len(by_desc(nodes3, "选择这一段用的场景")) == 3,
          "实际 %d" % len(by_desc(nodes3, "选择这一段用的场景")))

    # 3) 点场景名 → 单选列表
    print("\n=== 3. 场景名选择 ===")
    name_nodes = by_desc(nodes3, "选择这一段用的场景")
    old = name_nodes[0]["text"]
    tap(name_nodes[0])
    nodes4 = dump_tree()
    opts = [n for n in nodes4 if n["cls"] == "android.widget.CheckedTextView" or
            (n["click"] and n["text"] and n["text"] not in
             [x["text"] for x in nodes3] and n["b"][1] > 500)]
    print("  弹层里候选场景数：", len(opts))
    L.shot("z10_picker")
    target = None
    for n in opts:
        if n["text"] and n["text"] != old:
            target = n
            break
    if target:
        tap(target)
        nodes5 = dump_tree()
        new = by_desc(nodes5, "选择这一段用的场景")[0]["text"]
        check("选完场景名即时更新", new != old, "%s -> %s" % (old, new))
        chosen = new
    else:
        check("选完场景名即时更新", False, "没找到候选场景")
        chosen = old

    # 4) 保存 → API 读回
    print("\n=== 4. 保存落盘 ===")
    nodes6 = dump_tree()
    save = [n for n in nodes6 if n["click"] and n["b"][1] > 1500 and n["b"][0] > 500]
    if save:
        tap(min(save, key=lambda n: n["b"][1]))
    time.sleep(1.2)
    L.setup_forward()
    combos = L.api_try("/api/combos")
    items = (combos or {}).get("items") or []
    target_item = None
    for it in items:
        if it.get("name") == "面板测试组合":
            target_item = it
    check("保存后接口能读到组合", target_item is not None, json.dumps(items, ensure_ascii=False)[:300])
    if target_item:
        print("  摘要：", target_item.get("summary"))
        print("  steps：", json.dumps(target_item.get("steps"), ensure_ascii=False))
        check("段数仍是 3", len(target_item.get("steps") or []) == 3)
        check("第一段是刚才选的场景", chosen in (target_item.get("summary") or ""),
              "%s in %s" % (chosen, target_item.get("summary")))

    print("\n" + "=" * 50)
    print("回归结果：%d/%d 通过" % (ok, total))
    print("=" * 50)
    return ok, total


if __name__ == "__main__":
    main()
