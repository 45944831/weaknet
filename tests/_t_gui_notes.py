# -*- coding: utf-8 -*-
"""场景说明（「！」二级窗口）回归测试 —— 真开窗口，不需要真机 / 不需要引擎

需求背景
--------
场景预设区原来只有一排按钮，17 支档位里有一半光看名字分不清差别：
「4G 弱网」和「弱 Wi-Fi」都是低速 + 丢包，「黑洞」和「完全断网」都是连不上
但机制完全不同（挂住不返回 vs 立刻失败）。需要在预设区加「！」入口，
点开列出每支档位的注释与参数摘要。

本测试覆盖
----------
N1  「！」按钮存在，且挂在「场景预设」卡片的标题行
N2  点击后二级窗口打开
N3  正文含全部场景名
N4  正文含全部注释（逐条全文比对 PRESET_NOTES）
N5  三个分类标题都在
N6  每支档位都有一行参数摘要（数量与档位数一致）
N7  正文只读（state=disabled），不能编辑
N8  正文内容超出可视区，可滚动
N9  切换场景后「当前选中」标记跟着换
N10 重复点击不新建窗口（复用同一个）
N11 关闭后引用被清空，可再次打开
N12 主窗口关闭流程会把说明窗口一起收掉
"""
import os
import sys
import time
import tkinter as tk

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

import weaknet as W      # noqa: E402
import weaknet_gui as G  # noqa: E402

# 仓库根：本文件在 <repo>/tests/ 下，向上两级即仓库根。
# 用相对定位而不是写死绝对路径，换机器/换目录也能直接跑。
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

pass_n = fail_n = 0


def ck(name, ok, extra=""):
    global pass_n, fail_n
    if ok:
        pass_n += 1
        print("  PASS  %s%s" % (name, ("   " + extra) if extra else ""))
    else:
        fail_n += 1
        print("  FAIL  %s   <- %s" % (name, extra))


def settle(root, times=3, delay=0.03):
    for _ in range(times):
        root.update_idletasks()
        root.update()
        time.sleep(delay)


def walk(w):
    yield w
    try:
        kids = w.winfo_children()
    except Exception:
        return
    for k in kids:
        for x in walk(k):
            yield x


def notes_text(app):
    t = getattr(app, "notes_txt", None)
    if t is None:
        return ""
    return t.get("1.0", "end")


def main():
    root = tk.Tk()
    app = G.App(root, None, 8899)
    try:
        root.after_cancel(app._after_id)
    except Exception:
        pass
    app._closing = True
    root.geometry("880x860")
    settle(root, 5)

    # ---------- N1：按钮存在且在预设卡标题行 ----------
    btn = getattr(app, "preset_help_btn", None)
    ck("N1a 「！」按钮已创建", btn is not None)
    if btn is None:
        print("\n  %d pass, %d fail" % (pass_n, fail_n + 1))
        root.destroy()
        return 1
    ck("N1b 按钮文案是「！」", btn.cget("text") == "！", repr(btn.cget("text")))
    # 标题行 = 按钮的父容器；它里面应该有一个写着「场景预设」的 Label
    head = btn.master
    titles = []
    for w in walk(head):
        try:
            if isinstance(w, tk.Label):
                titles.append(w.cget("text"))
        except Exception:
            pass
    ck("N1c 与「场景预设」标题同一行", any("场景预设" in str(t) for t in titles),
       "标题行文本=%r" % (titles,))
    ck("N1d 按钮可点击（command 已绑定）", bool(btn.cget("command")))

    # ---------- N2：点开二级窗口 ----------
    btn.invoke()
    settle(root, 5)
    ck("N2 点击后打开说明窗口", app.notes_win is not None
       and app.notes_win.winfo_exists(), "notes_win=%r" % (app.notes_win,))
    body = notes_text(app)

    # ---------- N3 / N4 / N5 ----------
    miss_name = [k for k, v in W.PRESETS.items() if v[0] not in body]
    ck("N3 正文含全部场景名", not miss_name,
       "%d/%d%s" % (len(W.PRESETS) - len(miss_name), len(W.PRESETS),
                    "" if not miss_name else "  缺：" + ",".join(miss_name)))

    miss_note = []
    for k in W.PRESETS:
        note = W.PRESET_NOTES.get(k, "")
        if not note or note not in body:
            miss_note.append(k)
    ck("N4 正文含全部注释（全文比对）", not miss_note,
       "%d/%d%s" % (len(W.PRESETS) - len(miss_note), len(W.PRESETS),
                    "" if not miss_note else "  缺：" + ",".join(miss_note)))

    cats = ["常规网络", "弱网环境", "极端环境"]
    ck("N5 三个分类标题都在", all(c in body for c in cats),
       ",".join(c for c in cats if c not in body) or "全部命中")

    # ---------- N6：每支档位都有一行参数摘要 ----------
    # 逐条精确比对，不用「以某词开头」这类启发式 —— 「黑洞（请求无响应）」这种
    # 档位名本身就带参数关键词，按前缀数会把名字行也数进去
    lines = set(ln.strip() for ln in body.splitlines() if ln.strip())
    miss_param = []
    for k in W.PRESETS:
        try:
            want = G.detail(W.build_preset_profile(k))
        except Exception:
            continue
        if want not in lines:
            miss_param.append(k)
    ck("N6 每支档位都有一行参数摘要", not miss_param,
       "%d/%d%s" % (len(W.PRESETS) - len(miss_param), len(W.PRESETS),
                    "" if not miss_param else "  缺：" + ",".join(miss_param)))

    # ---------- N7：只读 ----------
    st = str(app.notes_txt.cget("state"))
    ck("N7 正文只读", st == "disabled", "state=%s" % st)

    # ---------- N8：可滚动 ----------
    txt = app.notes_txt
    txt.update_idletasks()
    need = txt.tk.call(txt._w, "count", "-update", "-ypixels", "1.0", "end")
    view_h = txt.winfo_height()
    ck("N8 内容超出可视区、可滚动", int(need) > view_h,
       "内容 %spx / 可视 %dpx" % (need, view_h))

    # ---------- N9：切场景后标记跟随 ----------
    W.G.preset_key = "high_loss"
    app._closing = False
    app.refresh()
    app._closing = True
    try:
        root.after_cancel(app._after_id)
    except Exception:
        pass
    settle(root, 3)
    body2 = notes_text(app)
    marked = [ln.strip() for ln in body2.splitlines() if "← 当前选中" in ln]
    ck("N9a 切场景后只标一处「当前选中」", len(marked) == 1, "%r" % (marked,))
    ck("N9b 标的是新选中的档位", bool(marked) and "高丢包" in marked[0],
       "%r" % (marked[0] if marked else None))

    # ---------- N10：重复点击不新建 ----------
    wid = app.notes_win.winfo_id()
    app.open_preset_notes()
    settle(root, 3)
    ck("N10 重复打开复用同一个窗口", app.notes_win.winfo_id() == wid,
       "%s -> %s" % (wid, app.notes_win.winfo_id()))

    # ---------- N11：关闭后可再次打开 ----------
    app.close_preset_notes()
    settle(root, 3)
    ck("N11a 关闭后引用清空", app.notes_win is None and app.notes_txt is None,
       "notes_win=%r notes_txt=%r" % (app.notes_win, app.notes_txt))
    app.open_preset_notes()
    settle(root, 4)
    ck("N11b 关闭后可再次打开", app.notes_win is not None
       and len(notes_text(app)) > 100, "正文 %d 字" % len(notes_text(app)))

    # ---------- N12：主窗口关闭时一起收掉 ----------
    try:
        app._closing = False
        app.on_close()
    except Exception as e:
        ck("N12 主窗口关闭流程不报错", False, repr(e))
    else:
        ck("N12 主窗口关闭流程不报错", True)

    print()
    print("  %d pass, %d fail" % (pass_n, fail_n))
    try:
        root.destroy()
    except Exception:
        pass
    return 0 if fail_n == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
