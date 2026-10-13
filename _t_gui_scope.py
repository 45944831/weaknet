"""GUI 接管范围自检（无需显示窗口）：构建、默认值、多选、应用、模式切换"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tkinter as tk
import weaknet as W
import weaknet_gui as G

checks = []


def ck(name, cond, extra=""):
    checks.append((name, bool(cond), extra))


root = tk.Tk()
root.withdraw()
app = G.App(root, loop=None, ctrl_port=8899)
root.deiconify()
root.update()

# ---- 1. 控件存在 ----
ck("接管范围卡片已构建", hasattr(app, "scope_card") and app.scope_card is not None)
ck("候选列表控件存在", hasattr(app, "scope_box"))
ck("模式变量默认 all", app.scope_mode.get() == "all")

# ---- 2. 默认折叠 ----
state = lambda: app.scope_card.winfo_manager()
ck("默认折叠", state() == "", "状态=%r" % state())

# ---- 3. 点标题展开（只触发一次）----
head = app.scope_card.winfo_parent()
head = root.nametowidget(head)
head_children = head.winfo_children()
app.scope_toggle()
root.update()
ck("展开后可见", state() == "pack", "状态=%r" % state())
app.scope_toggle()
root.update()
ck("再点收起", state() == "", "状态=%r" % state())

# 展开以便操作列表
app.scope_toggle()
root.update()

# ---- 4. 候选列表非空 ----
n = app.scope_box.size()
ck("候选程序列表非空", n > 0, "共 %d 项" % n)

# ---- 5. 多选并应用（only 模式）----
items = app.scope_box.get(0, "end")
targets = [i for i in items if i in ("python.exe", "curl.exe")] or list(items[:2])
app.scope_box.selection_clear(0, "end")
for t in targets:
    app.scope_box.selection_set(items.index(t))
app.scope_mode.set("only")
app.apply_scope()
root.update()
ck("only 模式已生效", W.G.scope_mode == "only", "mode=%s" % W.G.scope_mode)
ck("名单与选择一致", sorted(W.G.scope_list) == sorted(targets),
   "引擎=%s 选择=%s" % (W.G.scope_list, targets))

# ---- 6. 应用后列表回选正确 ----
app.reload_scope()
sel = [items[i] for i in app.scope_box.curselection()]
ck("刷新后回选保持", sorted(sel) == sorted(targets), "回选=%s" % sel)

# ---- 7. 切换到 exclude ----
app.scope_mode.set("exclude")
app.apply_scope()
root.update()
ck("exclude 模式已生效", W.G.scope_mode == "exclude", "mode=%s" % W.G.scope_mode)
ck("exclude 名单不变", sorted(W.G.scope_list) == sorted(targets))

# ---- 8. 清空选择 + all ----
app.clear_scope()
app.scope_mode.set("all")
app.apply_scope()
root.update()
ck("清空后名单为空", W.G.scope_list == [], "list=%s" % W.G.scope_list)
ck("all 模式已生效", W.G.scope_mode == "all")

# ---- 9. 非法模式被兜底 ----
W.set_scope("bogus", ["x"])
ck("非法模式回落 all", W.G.scope_mode == "all", "mode=%s" % W.G.scope_mode)
W.set_scope("only", ["A.EXE", "a.exe", " b.exe ", ""])
ck("名单去重+小写+去空", W.G.scope_list == ["a.exe", "b.exe"], "list=%s" % W.G.scope_list)

# ---- 10. 折叠不影响其它卡片 ----
ck("自定义卡片折叠仍可用", app.custom_card is not None)
ck("日志框仍存在", app.logbox is not None)

root.destroy()

print("=== GUI 接管范围自检 ===")
ok = True
for n, v, e in checks:
    print(("  PASS  " if v else "  FAIL  ") + n + (("  <- " + e) if (e and not v) else ""))
    ok = ok and v
print("总体:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
