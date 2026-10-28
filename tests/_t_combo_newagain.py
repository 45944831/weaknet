# -*- coding: utf-8 -*-
"""「弱网组合保存后继续新建」的边界自检（Windows GUI）。

已实现的行为：
  - 保存后不关窗，直接切成「新建」模式（名字清空、循环关掉、段重置）
  - 保存后再点「＋ 新建组合」不会卡死、不会弹出第二扇窗

本脚本专门盯两条容易出问题的路径：
  A. 保存后**已经填了半成品**，再点「＋ 新建组合」→ 用户期望的是「来一支全新的」，
     不该把上一支没填完的内容悄悄留在界面上。
  B. 保存后点「＋ 新建组合」→ 不该叠出第二扇编辑器窗口。
  C. 保存后想回去编辑刚存的那支 → 得能打开那支（而不是卡在新建模式）。
"""
import os
import sys

# 仓库根：本文件在 <repo>/tests/ 下，向上两级即仓库根。
# 用相对定位而不是写死绝对路径，换机器/换目录也能直接跑。
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

import asyncio
import pathlib
import sys
import tempfile
import threading
import time
import tkinter as tk

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
import weaknet as W  # noqa: E402
import weaknet_gui as GUI  # noqa: E402

OK = []
BAD = []


def check(name, cond, extra=""):
    (OK if cond else BAD).append(name)
    print("  %s %s%s" % ("OK  " if cond else "FAIL", name,
                         ("  <- " + str(extra)) if extra else ""))


def pump(root, n=3):
    for _ in range(n):
        try:
            root.update()
        except Exception:
            pass
        time.sleep(0.02)


def main():
    p = pathlib.Path(tempfile.mkdtemp()) / "weaknet_presets.json"
    W._preset_files = lambda: [p]
    W.G.user_presets = None
    W.G.combos = None

    loop = asyncio.new_event_loop()
    threading.Thread(target=loop.run_forever, daemon=True).start()

    root = tk.Tk()
    root.withdraw()
    app = GUI.App(root, loop, 18999)

    print("== 1. 新建一支组合并保存 ==")
    app.open_combo_editor()
    pump(root)
    check("编辑器打开了", app.combo_win is not None)
    app.combo_name.set("第一支")
    app._combo_rows[0]["mv"].set("0")
    app._combo_rows[0]["sv"].set("30")
    app.save_combo()
    pump(root)
    names = [c["name"] for c in W.get_combos()]
    check("组合已保存", "第一支" in names, names)
    check("保存后窗口还开着", app.combo_win is not None)
    check("保存后标题回到「新建」",
          app.combo_win.title() == "新建弱网组合", app.combo_win.title())
    check("保存后名字清空了", app.combo_name.get() == "", app.combo_name.get())
    check("保存后切回新建模式（editing 为空）", not app._combo_editing,
          app._combo_editing)
    check("保存后段数重置为 1", len(app._combo_rows) == 1, len(app._combo_rows))

    print("\n== 2. 保存后点「＋ 新建组合」==")
    win_before = app.combo_win
    app.open_combo_editor()          # 等价于点「＋ 新建组合」
    pump(root)
    check("没有叠出第二扇窗", app.combo_win is win_before,
          (app.combo_win, win_before))
    check("仍是新建模式", not app._combo_editing, app._combo_editing)
    check("标题还是「新建弱网组合」", app.combo_win.title() == "新建弱网组合",
          app.combo_win.title())

    print("\n== 3. 关键缺口：保存后填了半成品，再点「＋ 新建组合」 ==")
    # 用户接着排第二支，写了一半（填了名字，还没填时间）
    app.combo_name.set("第二支")
    app._combo_rows[0]["mv"].set("1")
    app._combo_rows[0]["sv"].set("0")
    pump(root)
    check("半成品已填进界面", app.combo_name.get() == "第二支",
          app.combo_name.get())

    app.open_combo_editor()          # 再点一次「＋ 新建组合」
    pump(root)
    check("没有叠出第二扇窗", app.combo_win is win_before)
    check("名字已清空（来一支全新的）", app.combo_name.get() == "",
          app.combo_name.get())
    check("时间也回到初始（0 分 30 秒）",
          app._combo_rows[0]["mv"].get() == "0"
          and app._combo_rows[0]["sv"].get() == "30",
          (app._combo_rows[0]["mv"].get(), app._combo_rows[0]["sv"].get()))

    print("\n== 4. 保存后还能回去编辑刚存的那支 ==")
    # 场景：保存完第一支后，再点这支组合的编辑入口，应该打开的是「第一支」
    first = [c for c in W.get_combos() if c["name"] == "第一支"][0]
    app.open_combo_editor(first["id"])
    pump(root)
    check("打开的是刚存的那支", app._combo_editing == first["id"],
          app._combo_editing)
    check("标题是「编辑弱网组合」", app.combo_win.title() == "编辑弱网组合",
          app.combo_win.title())
    check("名字带出来了", app.combo_name.get() == "第一支", app.combo_name.get())

    print("\n== 5. 编辑保存后同样能接着新建 ==")
    app.save_combo()
    pump(root)
    check("编辑保存后切回新建模式", not app._combo_editing, app._combo_editing)
    check("编辑保存后窗口还开着", app.combo_win is not None)
    check("编辑保存后名字清空", app.combo_name.get() == "", app.combo_name.get())
    check("库里还是一支（同名覆盖，不是多一支）",
          len([c for c in W.get_combos() if c["name"] == "第一支"]) == 1,
          [c["name"] for c in W.get_combos()])

    print("\n== 6. 连着存两支，两支都在库里 ==")
    app.combo_name.set("第二支")
    app._combo_rows[0]["mv"].set("0")
    app._combo_rows[0]["sv"].set("45")
    app.save_combo()
    pump(root)
    app.combo_name.set("第三支")
    app._combo_rows[0]["sv"].set("60")
    app.save_combo()
    pump(root)
    names = sorted(c["name"] for c in W.get_combos())
    check("三支都在库里", names == ["第一支", "第三支", "第二支"], names)
    check("保存完仍停在新建模式", not app._combo_editing, app._combo_editing)
    check("保存完窗口还开着", app.combo_win is not None)

    print("\n== 7. 组合按钮列表跟着刷新 ==")
    app.reload_combo()
    pump(root)
    btn_names = [b.cget("text") for b in app.combo_btns.values()]
    check("主界面有 3 个组合按钮", len(btn_names) == 3, btn_names)
    check("按钮带 🔀 标记", all("🔀" in t for t in btn_names), btn_names)

    try:
        root.destroy()
    except Exception:
        pass

    print("\n%d 项通过 / %d 项失败" % (len(OK), len(BAD)))
    if BAD:
        print("失败项：")
        for b in BAD:
            print("  - " + b)
        return 1
    print("全部通过 ✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())
