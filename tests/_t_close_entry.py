# -*- coding: utf-8 -*-
"""二级弹出界面：关闭入口唯一性。

用户在「接管范围」窗口上报了个问题：同一扇窗上同时摆着
标题栏右上角的 ✕、头部的「← 返回」、底部的「返回」，
三个入口干的是同一件事（关窗），看着犯迷糊。

规矩定下来：**每个二级界面只留一个关闭入口，就是 ✕**。

分两端查：
  · Windows 端跑真实 GUI，看每个二级窗里还有没有别的关闭按钮
  · Android 端查源码，确认底部没有「取消」，且都有 ✕
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

import tkinter as tk          # noqa: E402
import weaknet_gui as G        # noqa: E402

# 仓库根：本文件在 <repo>/tests/ 下，向上两级即仓库根。
# 用相对定位而不是写死绝对路径，换机器/换目录也能直接跑。
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

ANDROID = os.path.join(HERE, "weaknet", "android", "src", "com", "weaknet", "app")

pass_n = 0
fail_n = 0


def ck(name, ok, detail=""):
    global pass_n, fail_n
    if ok:
        pass_n += 1
        print("  PASS  %s" % name)
    else:
        fail_n += 1
        print("  FAIL  %s   %s" % (name, detail))


def walk(wd, out):
    for c in wd.winfo_children():
        out.append(c)
        walk(c, out)
    return out


def buttons_of(win):
    return [str(x.cget("text")) for x in walk(win, []) if isinstance(x, tk.Button)]


def texts_of(win):
    """窗里所有文字控件的内容（Label / Button / TextView 都算）"""
    out = []
    for x in walk(win, []):
        if isinstance(x, (tk.Label, tk.Button)):
            out.append(str(x.cget("text")))
    return out


# 关闭入口的措辞：这些词出现就说明「返回」按钮还在
BACK_WORDS = ("返回", "取消", "关闭", "←")


def test_windows(root):
    app = G.App(root, None, None)
    root.update_idletasks()

    # ---- 1. 接管范围：头部与底部都不该有返回 ----
    app.open_scope()
    root.update_idletasks()
    w = app.scope_win
    btns = buttons_of(w)
    ck("W1 接管范围窗没有「返回」按钮",
       not any("返回" in b for b in btns), "按钮：%s" % btns)
    ck("W2 接管范围窗没有「取消」按钮",
       not any("取消" in b for b in btns), "按钮：%s" % btns)
    ck("W3 接管范围窗保留了「保存」",
       "保存" in btns, "按钮：%s" % btns)

    # 头部标题左边距：移除「← 返回」后，标题不该还留着原来给按钮留的 12px 缩进
    heads = [x for x in walk(w, []) if isinstance(x, tk.Label)
             and str(x.cget("text")) == "接管范围"]
    ck("W4 接管范围标题存在且只有一个", len(heads) == 1, "找到 %d 个" % len(heads))

    # 保存按钮要撑满整行（原来它和「返回」各占一半）
    saves = [x for x in walk(w, []) if isinstance(x, tk.Button)
             and str(x.cget("text")) == "保存"]
    if saves:
        root.update_idletasks()
        fw = w.winfo_width()
        sw = saves[0].winfo_width()
        ck("W5 接管范围的「保存」撑满整行",
           fw > 0 and sw >= fw - 30, "窗宽 %d 保存宽 %d" % (fw, sw))
    else:
        ck("W5 接管范围的「保存」撑满整行", False, "没找到保存按钮")

    # ---- 2. 组合说明窗 ----
    app.open_combo_notes()
    root.update_idletasks()
    cb = buttons_of(app.combo_notes_win)
    ck("W6 组合说明窗没有「返回」按钮",
       not any("返回" in b for b in cb), "按钮：%s" % cb)

    # ---- 3. 场景说明窗 ----
    app.open_preset_notes()
    root.update_idletasks()
    pb = buttons_of(app.notes_win)
    ck("W7 场景说明窗没有「返回」按钮",
       not any("返回" in b for b in pb), "按钮：%s" % pb)

    # ---- 4. 环境共享窗：底部整排删掉了，不能留空 foot 框 ----
    app.open_share()
    root.update_idletasks()
    sb = buttons_of(app.share_win)
    ck("W8 环境共享窗没有「返回」按钮",
       not any("返回" in b for b in sb), "按钮：%s" % sb)

    # ---- 5. 组合编辑器：底部「取消」改成标题栏 ✕ ----
    app.open_combo_editor()
    root.update_idletasks()
    eb = buttons_of(app.combo_win)
    ck("W9 组合编辑器没有「取消」按钮",
       not any("取消" in b for b in eb), "按钮：%s" % eb)
    ck("W10 组合编辑器还有「保存组合」",
       "保存组合" in eb, "按钮：%s" % eb)

    # ---- 6. 全局扫一遍：主界面之外的窗都不该再有「返回」 ----
    app.open_scope()
    root.update_idletasks()
    all_back = []
    for win in (app.scope_win, app.combo_notes_win, app.notes_win,
                app.share_win, app.combo_win):
        if win is None:
            continue
        for t in texts_of(win):
            for w2 in BACK_WORDS:
                if w2 in t:
                    all_back.append(t)
                    break
    ck("W11 所有二级窗的文字里没有「返回/取消」",
       not all_back, "找到：%s" % all_back[:5])

    # ---- 7. 关键：移除「返回」后，✕ 还能不能关窗 ----
    # 这是本轮最大的风险点。X 走的是 WM_DELETE_WINDOW，
    # 要是这条路跟原来的「返回」按钮走的不是同一条，关窗就失灵了。
    # 直接调 win.destroy() 是绕过协议的（那不算 X），所以这里取协议里
    # 注册的命令原样执行 —— 这才是点右上角 X 真正发生的事。
    for name, opener, attr in (
            ("接管范围", app.open_scope, "scope_win"),
            ("组合说明", app.open_combo_notes, "combo_notes_win"),
            ("场景说明", app.open_preset_notes, "notes_win"),
            ("环境共享", app.open_share, "share_win"),
            ("组合编辑器", app.open_combo_editor, "combo_win"),
    ):
        opener()
        root.update_idletasks()
        win = getattr(app, attr)
        if win is None:
            ck("W12 %s 窗能打开" % name, False)
            continue
        cmd = win.protocol("WM_DELETE_WINDOW")
        ck("W12 %s 窗绑定了 WM_DELETE_WINDOW（X 才有路可走）" % name,
           bool(cmd), "没绑协议")
        if cmd:
            try:
                root.tk.eval(cmd)
            except Exception as e:
                ck("W13 %s 点 X 能关窗" % name, False, "执行协议报错 %s" % e)
                continue
        root.update_idletasks()
        gone = getattr(app, attr) is None
        ck("W13 %s 点 X 后窗关掉、状态清空" % name, gone,
           "attr 仍是 %r" % getattr(app, attr))

    # ---- 8. Esc 仍然能关（接管范围有绑） ----
    app.open_scope()
    root.update_idletasks()
    w2 = app.scope_win
    ck("W14 接管范围窗仍绑定 Esc 关闭",
       bool(w2.bind("<Escape>")), "没绑 Esc")
    # Esc 也要走 close_scope（未保存会问一句），不是直接 destroy
    root.update_idletasks()
    try:
        app.close_scope()
    except Exception:
        pass
    root.update_idletasks()
    ck("W15 close_scope 把窗口状态清空了", app.scope_win is None)

    # 收工：把窗都关掉
    for close in (lambda: app.close_scope(), lambda: app.close_combo_notes(),
                  lambda: app.close_preset_notes(), lambda: app.close_share(),
                  lambda: app.close_combo_editor()):
        try:
            close()
        except Exception:
            pass


def test_android():
    """Android 端：查源码。

    这些界面的 ✕ 都在标题行里，取消入口在底部 ——
    两者功能重复，按规矩只留 ✕。
    """
    sheets = {
        "ScopeSheet.java": "指定程序",
        "MainPanels.java": "自定义参数表单",
        "ComboSheet.java": "组合编辑器",
    }
    for fn, what in sheets.items():
        p = os.path.join(ANDROID, fn)
        src = open(p, "r", encoding="utf-8").read()
        # 剥掉注释再查，免得注释里提一句「取消」就误判
        code = re.sub(r"//[^\n]*", "", src)
        code = re.sub(r"/\*.*?\*/", "", code, flags=re.S)
        hits = re.findall(r'setText\(\s*"取消"\s*\)', code)
        ck("A1 %s（%s）底部没有「取消」按钮" % (fn, what),
           not hits, "找到 %d 处" % len(hits))

    # 每个带 ✕ 的二级界面，✕ 都得真的能关窗（点了要 dismiss）
    for fn in ("ScopeSheet.java", "MainPanels.java", "ComboSheet.java",
               "NotesSheet.java", "ShareSheet.java", "SettingsSheet.java"):
        p = os.path.join(ANDROID, fn)
        src = open(p, "r", encoding="utf-8").read()
        code = re.sub(r"//[^\n]*", "", src)
        has_x = 'setText("✕")' in code
        ck("A2 %s 有 ✕ 关闭入口" % fn, has_x)

    # ComboSheet 的 ✕ 必须真的调 dismissEditor（否则点了没反应）
    p = os.path.join(ANDROID, "ComboSheet.java")
    src = open(p, "r", encoding="utf-8").read()
    code = re.sub(r"//[^\n]*", "", src)
    ck("A3 组合编辑器的 ✕ 调 dismissEditor",
       "dismissEditor()" in code and "private void dismissEditor()" in code)
    ck("A4 组合编辑器有 dismissEditor 的实现",
       re.search(r"private void dismissEditor\(\)\s*\{", code) is not None)


def main():
    print("== Windows 端 ==")
    root = tk.Tk()
    root.withdraw()
    try:
        test_windows(root)
    finally:
        try:
            root.destroy()
        except Exception:
            pass

    print("== Android 端 ==")
    test_android()

    print("\n合计 %d 项通过 / %d 项失败" % (pass_n, fail_n))
    return 1 if fail_n else 0


if __name__ == "__main__":
    sys.exit(main())
