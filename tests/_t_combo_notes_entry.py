# -*- coding: utf-8 -*-
"""组合说明「！」入口的位置回归（本轮改动）

改动：组合说明的「！」从「组合区标题右侧」挪进「新建 / 编辑组合」界面里。

理由：说明是给「要动手排组合的人」看的。摆在组合列表顶上，
每一支组合都像是有一份说明；挪进编辑器里，位置才对得上使用时机。

要验的：
  1. 组合区不再有「！」（挪走了，不能两边都留着）
  2. 编辑器里有「！」，点了能打开组合说明
  3. 说明窗挂在编辑器下面（transient），关掉编辑器它跟着消失
  4. 换看别的组合（编辑器重建）时，旧说明窗不会残留
  5. 安卓端源码结构对称：说明入口在编辑器里、组合区没有
"""
import os
import sys

# 仓库根：本文件在 <repo>/tests/ 下，向上两级即仓库根。
# 用相对定位而不是写死绝对路径，换机器/换目录也能直接跑。
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

import os
import sys

sys.path.insert(0, ROOT)

import tkinter as tk      # noqa: E402

import weaknet_gui as G   # noqa: E402




PASS = 0
FAIL = 0


def ck(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ok   %s" % name)
    else:
        FAIL += 1
        print("  FAIL %s  %s" % (name, extra))


def walk(wd, out):
    for c in wd.winfo_children():
        out.append(c)
        walk(c, out)
    return out


def buttons(win):
    return [x for x in walk(win, []) if isinstance(x, tk.Button)]


def has_bang(win):
    return any(b.cget("text") == "！" for b in buttons(win))


def find_combo_page(app):
    """组合区那个动作行 Frame：里面挂「＋ 新建组合」按钮

    注意别顺着祖先往上找 —— 整个卡片里还有 13 个参数自己的「！」，
    那是参数说明入口，跟组合说明没关系。
    """
    for x in walk(app.root, []):
        if isinstance(x, tk.Button) and x.cget("text") == "＋ 新建组合":
            return x.master
    return None


def test_windows():
    root = tk.Tk()
    root.withdraw()
    app = G.App(root, None, None)
    root.update_idletasks()

    # ---- 1. 组合区没有「！」了 ----
    combo_page = find_combo_page(app)
    ck("W1 找到组合区页面", combo_page is not None)
    if combo_page is not None:
        ck("W2 组合区不再有「！」入口", not has_bang(combo_page),
           "按钮=%r" % [b.cget("text") for b in buttons(combo_page)])
        ck("W3 组合区仍有「＋ 新建组合」",
           any(b.cget("text") == "＋ 新建组合" for b in buttons(combo_page)))

    # ---- 2. 编辑器里有「！」 ----
    app.open_combo_editor()
    root.update_idletasks()
    win = app.combo_win
    ck("W4 编辑器打开了", win is not None)
    ck("W5 编辑器里有「！」", has_bang(win),
       "按钮=%r" % [b.cget("text") for b in buttons(win)])
    ck("W6 编辑器有内部标题行", hasattr(app, "lab_combo_editor_title"))
    if hasattr(app, "lab_combo_editor_title"):
        ck("W7 新建态内部标题是「新建弱网组合」",
           app.lab_combo_editor_title.cget("text") == "新建弱网组合",
           app.lab_combo_editor_title.cget("text"))

    # ---- 3. 点「！」能打开说明窗，且挂在编辑器下 ----
    for b in buttons(win):
        if b.cget("text") == "！":
            b.invoke()
            break
    root.update_idletasks()
    notes = app.combo_notes_win
    ck("W8 点「！」打开了说明窗", notes is not None)
    if notes is not None:
        ck("W9 说明窗 transient 挂在编辑器下",
           str(notes.wm_transient()) == str(win),
           "transient=%r editor=%r" % (str(notes.wm_transient()), str(win)))

    # ---- 4. 关编辑器，说明窗跟着消失 ----
    app.close_combo_editor()
    root.update_idletasks()
    alive = False
    try:
        alive = bool(notes.winfo_exists())
    except Exception:
        alive = False
    ck("W10 关编辑器后说明窗一并关闭", not alive)
    ck("W11 app.combo_notes_win 已清空", app.combo_notes_win is None)

    # ---- 5. 换看别的组合（编辑器重建）时不留孤儿说明窗 ----
    app.open_combo_editor()
    root.update_idletasks()
    for b in buttons(app.combo_win):
        if b.cget("text") == "！":
            b.invoke()
            break
    root.update_idletasks()
    ck("W12 第二扇说明窗打开了", app.combo_notes_win is not None)
    app.open_combo_editor("不存在的id")     # 走重建路径
    root.update_idletasks()
    ck("W13 重建编辑器后说明窗被带走", app.combo_notes_win is None,
       "still=%r" % app.combo_notes_win)

    root.destroy()
    return PASS, FAIL


def test_android():
    """安卓端只能查源码结构：没有真机跑不了交互"""
    base = os.path.join(HERE, "weaknet", "android", "src", "com", "weaknet", "app")
    combo = open(os.path.join(base, "ComboSheet.java"), encoding="utf-8").read()
    notes = open(os.path.join(base, "ComboNotesSheet.java"), encoding="utf-8").read()

    # 组合区：只剩「＋ 新建组合」，说明入口已摘掉
    ck("A1 安卓组合区不再有「！」",
       'bHelp.setText("！")' not in combo.split("openEditor(String id)")[0],
       "组合区仍建了 bHelp")
    ck("A2 安卓组合区仍有「＋ 新建组合」", '"＋ 新建组合"' in combo)

    # 编辑器标题行右侧有「！」，点开调用 openNotes()
    editor = combo.split("openEditor(String id)")[1]
    ck("A3 编辑器里有「！」入口", 'bHelp.setText("！")' in editor)
    ck("A4 编辑器里的「！」调 openNotes()",
       "openNotes();" in editor.split("bHelp.setOnClickListener")[1][:400]
       if "bHelp.setOnClickListener" in editor else False)
    ck("A5 标题行是横向容器（标题 + 说明同一行）",
       "LinearLayout thead" in editor and "thead.setOrientation(LinearLayout.HORIZONTAL)" in editor)

    # 浮窗归属：说明页只放回自己收起的那个
    ck("A6 说明页自己管飘窗归属",
       "suspendedByMe" in notes and "releaseFloat()" in notes)
    ck("A7 说明页 dismiss 时兜底放回飘窗",
       "setOnDismissListener" in notes and "releaseFloat();" in notes)
    ck("A8 openNotes 不再无条件 restoreFloat",
       "notes.close();\n            a.restoreFloat();" not in combo,
       "openNotes 里仍有裸的 restoreFloat")
    ck("A9 编辑器关闭时顺带关说明页",
       "closeNotes();" in editor.split("setOnDismissListener")[1][:400]
       if "setOnDismissListener" in editor else False)


def main():
    print("== Windows GUI ==")
    test_windows()
    print()
    print("== APK 源码结构 ==")
    test_android()
    print()
    print("%d 项通过 / %d 项失败" % (PASS, FAIL))
    print("全部通过 ✅" if FAIL == 0 else "有失败项 ❌")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
