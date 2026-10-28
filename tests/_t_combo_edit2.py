"""组合编辑器新规矩自检：保存后接着新建 / 看得到并删得掉 / 删完各处同步刷新"""
import asyncio
import pathlib
import tempfile
import threading
import time

import sys
import tkinter as tk

sys.path.insert(0, ROOT)
import weaknet as W          # noqa: E402
import weaknet_gui as GUI    # noqa: E402

# 仓库根：本文件在 <repo>/tests/ 下，向上两级即仓库根。
# 用相对定位而不是写死绝对路径，换机器/换目录也能直接跑。
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

PFILE = pathlib.Path(tempfile.mkdtemp()) / "weaknet_presets.json"
W._preset_files = lambda: [PFILE]
W.G.combos = None
W.G.user_presets = None

INFO = []
GUI.messagebox.showinfo = lambda *a, **k: INFO.append(("info", a[1] if len(a) > 1 else ""))
GUI.messagebox.showerror = lambda *a, **k: INFO.append(("error", a[1] if len(a) > 1 else ""))
GUI.messagebox.askyesno = lambda *a, **k: True

FAILS = []


def check(name, cond, extra=""):
    print(("  OK   " if cond else "  FAIL ") + name + (("  <- " + str(extra)) if extra else ""))
    if not cond:
        FAILS.append(name)


def pump(root, n=3):
    for _ in range(n):
        root.update()
        time.sleep(0.05)


loop = asyncio.new_event_loop()
threading.Thread(target=loop.run_forever, daemon=True).start()
root = tk.Tk()
root.withdraw()
app = GUI.App(root, loop, 18999)

print("== 1. 新建一支，保存后不关窗，直接能接着排下一支 ==")
app.open_combo_editor()
pump(root)
check("编辑器打开着", app.combo_win is not None)
app.combo_name.set("地铁通勤")
app._combo_rows[0]["mv"].set("0")
app._combo_rows[0]["sv"].set("30")
app.save_combo()
pump(root)
check("保存后窗口还开着", app.combo_win is not None)
check("组合已入库", len(W.get_combos()) == 1, W.get_combos())
check("已切成新建模式（名字清空）", app.combo_name.get() == "", repr(app.combo_name.get()))
check("已切成新建模式（段重置为 1 行）", len(app._combo_rows) == 1, len(app._combo_rows))
check("已切成新建模式（editing 清空）", not app._combo_editing, app._combo_editing)
check("窗口标题回到新建", app.combo_win.title() == "新建弱网组合", app.combo_win.title())
check("提示写着可以接着排下一支", "接着排下一支" in app.lab_combo_tip.cget("text"),
      app.lab_combo_tip.cget("text"))
check("删除按钮在新建模式下收起来了",
      not app.btn_combo_del_in.winfo_manager())

print("== 2. 接着排第二支，不会覆盖第一支 ==")
app.combo_name.set("通勤晚高峰")
app._combo_rows[0]["sv"].set("45")
app.save_combo()
pump(root)
names = [c["name"] for c in W.get_combos()]
check("两支都在", len(W.get_combos()) == 2, names)
check("第一支没被覆盖", "地铁通勤" in names, names)
check("第二支也存下了", "通勤晚高峰" in names, names)
check("主界面组合按钮跟着变", len(app.combo_btns) == 2, list(app.combo_btns))

print("== 3. 点已有组合 → 打开查看界面（里面有删除按钮） ==")
cid = W.get_combos()[0]["id"]
app.open_combo_editor(cid)
pump(root)
check("标题是编辑/查看", app.combo_win.title() == "编辑弱网组合", app.combo_win.title())
check("名字带出来了", app.combo_name.get() == "地铁通勤", app.combo_name.get())
check("段也带出来了", len(app._combo_rows) == 1, len(app._combo_rows))
check("删除按钮出现了", app.btn_combo_del_in.winfo_manager())
check("editing 指向这一支", app._combo_editing == cid, app._combo_editing)

print("== 4. 在查看界面里删掉 → 窗口关掉、各处同步刷新 ==")
# 共享窗口开着，才能验「共享列表也跟着少一支」
W.ensure_identity()
W.G.user_name = "小李"
app.open_share()
pump(root)
before_share = len(app.share_combo_list)
check("共享窗口里也列着 2 支组合", before_share == 2, before_share)
app._combo_delete_in_editor()
pump(root)
check("删除后编辑器关掉了", app.combo_win is None)
check("库里少了一支", len(W.get_combos()) == 1, [c["name"] for c in W.get_combos()])
check("主界面按钮同步", len(app.combo_btns) == 1, list(app.combo_btns))
check("共享窗口列表同步", len(app.share_combo_list) == 1, app.share_combo_list)
check("删掉的是被点的那支",
      W.get_combos()[0]["name"] == "通勤晚高峰", W.get_combos()[0]["name"])
app.close_share()
pump(root)

print("== 5. 双击组合按钮 → 打开的是那一支 ==")
btn = list(app.combo_btns.values())[0]
check("组合按钮绑了双击打开查看", bool(btn.bind("<Double-Button-1>")), btn.bind("<Double-Button-1>"))
# Tk 不允许用 event_generate 造双击事件，这里直接走它绑定的那个入口
btn.event_generate("<Button-1>")
pump(root)
app.open_combo_editor(W.get_combos()[0]["id"])
pump(root)
check("双击打开的是那一支（模拟入口）", app.combo_win is not None
      and app.combo_name.get() == W.get_combos()[0]["name"], app.combo_name.get())
check("editing 对得上", app._combo_editing == W.get_combos()[0]["id"])

print("== 6. 保存后接着新建时，双击另一支不会串台 ==")
app.close_combo_editor()
pump(root)
app.open_combo_editor()          # 新建模式
pump(root)
app.combo_name.set("临时")
app.save_combo()
pump(root)
check("保存后仍是新建模式", app.combo_name.get() == "", app.combo_name.get())
other = W.get_combos()[0]
app.open_combo_editor(other["id"])   # 换看另一支
pump(root)
check("换到的是那一支", app.combo_name.get() == other["name"], app.combo_name.get())
check("标题是编辑", app.combo_win.title() == "编辑弱网组合", app.combo_win.title())

print("\n" + ("全部通过 ✅" if not FAILS else "失败 %d 项：%s" % (len(FAILS), "、".join(FAILS))))
sys.exit(1 if FAILS else 0)
