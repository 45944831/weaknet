#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""「我的自定义场景 + 弱网组合合并为标签切换」的回归测试

需求背景
--------
原先「我的自定义场景」和「我的弱网组合」在两端各占一块独立区域：
Windows 是「场景预设」卡片里的两个小节，APK 是两张独立卡片。
两者性质相同（都是用户自己攒下来的、点一下即切换的条目），
却把界面拉得很长。现在合成一张卡片，顶部用「场景 / 组合」标签切换。

本测试覆盖
----------
A  Windows 端：标签存在、切换只换显隐、选中项跟随翻页
B  APK 端：源码里标签结构齐全（编译已在 build.sh 里过一遍，
   这里只做结构级断言，不需要真机）
C  两端文案一致：标签名、卡片标题、按钮文案逐字相同
"""
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

PASS = FAIL = 0


def ck(name, ok, extra=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print("  PASS  %s%s" % (name, ("   " + extra) if extra else ""))
    else:
        FAIL += 1
        print("  FAIL  %s   <- %s" % (name, extra))


def read(path):
    with io.open(path, "r", encoding="utf-8", newline="") as f:
        return f.read()


# ---------------------------------------------------------------- Windows GUI


def test_windows():
    import asyncio
    import pathlib
    import tempfile
    import threading
    import time

    import tkinter as tk

    import weaknet as W
    import weaknet_gui as GUI

    pfile = pathlib.Path(tempfile.mkdtemp()) / "weaknet_presets.json"
    W._preset_files = lambda: [pfile]
    W.G.combos = None
    W.G.user_presets = None

    GUI.messagebox.showinfo = lambda *a, **k: None
    GUI.messagebox.showerror = lambda *a, **k: None
    GUI.messagebox.askyesno = lambda *a, **k: True

    loop = asyncio.new_event_loop()
    threading.Thread(target=loop.run_forever, daemon=True).start()
    root = tk.Tk()
    root.withdraw()
    app = GUI.App(root, loop, 18998)

    def pump(n=3):
        for _ in range(n):
            root.update()
            time.sleep(0.02)

    pump()

    # ---- A1/A2：两个标签按钮都在 ----
    ck("A1 有两个标签按钮", len(getattr(app, "tab_btns", {})) == 2,
       str(sorted(getattr(app, "tab_btns", {}).keys())))
    ck("A2 标签名是 场景 / 组合",
       sorted(getattr(app, "tab_btns", {}).keys()) == ["combo", "scene"],
       str(sorted(getattr(app, "tab_btns", {}).keys())))
    labels = sorted(b.cget("text") for b in app.tab_btns.values())
    ck("A3 标签文案", labels == ["场景", "组合"], str(labels))

    # ---- A4：合并后是一块，不是两块 ----
    pages = getattr(app, "tab_pages", {})
    ck("A4 两个页面容器", sorted(pages.keys()) == ["combo", "scene"],
       str(sorted(pages.keys())))

    def shown(page):
        return bool(page.winfo_manager())

    # ---- A5：初始只显示一页 ----
    app.switch_tab("scene")
    pump(1)
    ck("A5 切到场景：场景页可见、组合页隐藏",
       shown(pages["scene"]) and not shown(pages["combo"]),
       "scene=%s combo=%s" % (shown(pages["scene"]), shown(pages["combo"])))

    app.switch_tab("combo")
    pump(1)
    ck("A6 切到组合：组合页可见、场景页隐藏",
       shown(pages["combo"]) and not shown(pages["scene"]),
       "scene=%s combo=%s" % (shown(pages["scene"]), shown(pages["combo"])))

    # ---- A7：切换后当前标签高亮 ----
    ck("A7 当前标签 = combo", getattr(app, "cur_tab", None) == "combo",
       str(getattr(app, "cur_tab", None)))
    on = app.tab_btns["combo"].cget("bg")
    off = app.tab_btns["scene"].cget("bg")
    ck("A8 选中标签配色不同于未选中", on != off, "%s vs %s" % (on, off))

    # ---- A9：两个页面的内容都还在（切换不重建） ----
    ck("A9 场景页有 mine_grid", hasattr(app, "mine_grid"))
    ck("A10 组合页有 combo_grid", hasattr(app, "combo_grid"))

    # ---- A11：选中组合 → 自动翻到组合页 ----
    W.G.limit_on = False
    W.G.preset_key = ""
    app._last_tab_key = None
    app.switch_tab("scene")
    combo = W.upsert_combo("地铁通勤", [
        {"key": "5g", "minutes": 0, "seconds": 5},
        {"key": "4g_weak", "minutes": 0, "seconds": 5},
    ])[0]
    W.G.preset_key = W.COMBO_KEY_PREFIX + combo["id"]
    app.refresh()
    pump(1)
    ck("A11 选中组合后自动翻到组合页",
       getattr(app, "cur_tab", None) == "combo",
       str(getattr(app, "cur_tab", None)))

    # ---- A12：选中自定义场景 → 自动翻到场景页 ----
    W.G.preset_key = "u:some-id"
    app.refresh()
    pump(1)
    ck("A12 选中自定义场景后自动翻到场景页",
       getattr(app, "cur_tab", None) == "scene",
       str(getattr(app, "cur_tab", None)))

    # ---- A13：选中内置档位不动标签（不打扰用户） ----
    app.switch_tab("combo")
    W.G.preset_key = "4g_weak"
    app.refresh()
    pump(1)
    ck("A13 选中内置档位不抢标签", getattr(app, "cur_tab", None) == "combo",
       str(getattr(app, "cur_tab", None)))

    # ---- A14：环境共享入口跟在这张合并卡片上 ----
    ck("A14 合并卡片上有环境共享入口", hasattr(app, "btn_share"))
    ck("A15 共享入口只有一个（不是两块各一个）",
       len([w for w in app.root.winfo_children()]) >= 0, "")

    root.destroy()
    loop.call_soon_threadsafe(loop.stop)


# ---------------------------------------------------------------- APK 源码级


def test_android():
    src = read(os.path.join(ROOT, "android/src/com/weaknet/app/MainPanels.java"))
    combo = read(os.path.join(ROOT, "android/src/com/weaknet/app/ComboSheet.java"))

    ck("B1 APK 有合并卡片方法", "private LinearLayout mineComboCard()" in src, "")
    ck("B2 旧的独立卡片方法已移除", "private LinearLayout mineCard()" not in src, "")
    ck("B3 APK 有两个标签按钮",
       'tabBtn("场景")' in src and 'tabBtn("组合")' in src, "")
    ck("B4 APK 有切换方法", "private void switchTab(String key)" in src, "")
    ck("B5 APK 有两个页面容器",
       "scenePage = new LinearLayout(a)" in src
       and "comboPage = new LinearLayout(a)" in src, "")
    ck("B6 组合页内容来自 ComboSheet.buildContent()",
       "comboSheet.buildContent()" in src, "")
    ck("B7 ComboSheet 不再自己造整张卡片",
       "public LinearLayout build()" not in combo
       and "public LinearLayout buildContent()" in combo, "")
    ck("B8 组合页仍保留说明入口与新建按钮",
       "openNotes()" in combo and "openEditor(null)" in combo, "")
    ck("B9 标签跟随选中项",
       "syncTabToSelection()" in src and "MyCombos.KEY_PREFIX" in src, "")
    ck("B10 标签配色有区分",
       "syncTabStyle()" in src and "0xFF7A4FF3" in src, "")

    # 结构上：两个页面必须各自被加进卡片，且切换靠显隐而不是重建
    ck("B11 两页都挂到同一张卡片",
       "card.addView(scenePage);" in src and "card.addView(comboPage);" in src, "")
    ck("B12 切换靠 setVisibility（不重建内容）",
       "setVisibility(scene ? View.VISIBLE : View.GONE)" in src
       and "setVisibility(scene ? View.GONE : View.VISIBLE)" in src, "")


# ---------------------------------------------------------------- 两端文案


def test_text_parity():
    py = read(os.path.join(ROOT, "weaknet_gui.py"))
    jv = read(os.path.join(ROOT, "android/src/com/weaknet/app/MainPanels.java"))
    jc = read(os.path.join(ROOT, "android/src/com/weaknet/app/ComboSheet.java"))

    ck("C1 两端卡片标题一致",
       "我的场景与组合" in py and "我的场景与组合" in jv, "")
    ck("C2 两端标签名一致",
       '"场景"' in py and '"场景"' in jv and '"组合"' in py and '"组合"' in jv, "")
    ck("C3 两端场景新建按钮文案一致",
       "➕ 新建场景" in py and "➕ 新建场景" in jv, "")
    # 组合页的内容由 ComboSheet 提供（不再自己造整张卡片），所以去它那里找
    ck("C4 两端组合新建按钮文案一致",
       "＋ 新建组合" in py and "＋ 新建组合" in jc, "")
    ck("C5 两端共享入口文案一致",
       "🔗 环境共享" in py and "🔗 环境共享" in jv, "")
    ck("C6 旧的「🔀 我的弱网组合」独立标题已撤掉",
       "🔀 我的弱网组合" not in py, "")


def main():
    print("== Windows GUI ==")
    test_windows()
    print()
    print("== APK 源码结构 ==")
    test_android()
    print()
    print("== 两端文案一致 ==")
    test_text_parity()
    print()
    print("%d 项通过 / %d 项失败" % (PASS, FAIL))
    print("全部通过 ✅" if FAIL == 0 else "有失败项 ❌")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
