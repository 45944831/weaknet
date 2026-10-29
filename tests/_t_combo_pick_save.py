# -*- coding: utf-8 -*-
"""组合编辑器「换场景 → 保存落盘」短流程回归（抗干扰重试版）。

设备上常有别的自动化在抢前台/乱点（会让弹层莫名关闭、点错卡片），
所以把流程压到最短，并在失败时整段重试；每次重试都重新打开编辑器。

判定口径：UI 里第一段换成目标场景后保存，/api/combos 读回的第一段
key 必须等于目标场景的 key。
"""
import sys
import time

sys.path.insert(0, __file__.rsplit("\\", 1)[0])
import _t_combo_editor as T
import _t_dev_lib as L

NAME = "面板测试组合"
TARGET_LABEL = "5G 良好"
TARGET_KEY = "5g"


def current_first():
    combos = L.api_try("/api/combos") or {}
    for it in combos.get("items") or []:
        if it.get("name") == NAME:
            steps = it.get("steps") or []
            return steps[0].get("key") if steps else None
    return None


def rows_of(nodes):
    return [(x["text"], x["b"]) for x in T.by_desc(nodes, "选择这一段用的场景")]


def attempt(idx):
    print("\n--- 第 %d 次尝试 ---" % idx, flush=True)
    if not T.open_editor():
        return False, "打开编辑器失败"
    nodes = T.dump_tree()
    rows = rows_of(nodes)
    print("  打开后段：", [r[0] for r in rows], flush=True)
    if len(rows) != 3:
        return False, "段数 %d != 3" % len(rows)
    if rows[0][0] == TARGET_LABEL:
        return True, "已是目标场景（此前已保存）"

    T.tap(T.by_desc(nodes, "选择这一段用的场景")[0], wait=1.2)
    nd = T.dump_tree()
    opts = [x for x in nd if x["text"] == TARGET_LABEL]
    if not opts:
        return False, "没拿到场景弹层"
    T.tap(opts[0], wait=1.2)

    n2 = T.dump_tree()
    rows2 = rows_of(n2)
    save = T.by_text(n2, "保存组合")
    print("  选完段：", [r[0] for r in rows2], "保存按钮：", bool(save), flush=True)
    if not rows2 or rows2[0][0] != TARGET_LABEL:
        return False, "行内显示没更新（弹层被外部关掉？）"
    if not save:
        return False, "保存按钮不在（弹层被外部关掉？）"

    T.tap(save[0], wait=2.0)
    got = current_first()
    print("  接口读回第一段 key：", got, flush=True)
    if got != TARGET_KEY:
        return False, "落盘 key=%s，期望 %s" % (got, TARGET_KEY)
    return True, "换场景并保存成功"


def main():
    print("目标：%s（key=%s），起始 key=%s" % (TARGET_LABEL, TARGET_KEY, current_first()))
    for i in range(1, 6):
        ok, why = attempt(i)
        print(("  PASS " if ok else "  FAIL ") + why, flush=True)
        if ok:
            break
        time.sleep(1.0)
    print("\n最终 key：", current_first())
    return 0 if current_first() == TARGET_KEY else 1


if __name__ == "__main__":
    raise SystemExit(main())
