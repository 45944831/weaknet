"""GUI 接管范围自检（无需人工操作）：
主界面只留入口按钮 → 打开二级窗口罗列候选 → 勾选 / 搜索 / 全选 / 清空 →
保存后回主界面且摘要更新 → 返回按钮（含未保存时的确认与回滚）"""
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

# ---- 1. 主界面：卡片 + 入口按钮，不再有折叠区和列表 ----
ck("接管范围卡片已构建", getattr(app, "scope_card", None) is not None)
ck("入口按钮存在", getattr(app, "scope_open_btn", None) is not None)
ck("入口按钮文案正确", app.scope_open_btn.cget("text") == "选择接管的程序…",
   app.scope_open_btn.cget("text"))
ck("未打开时没有二级窗口", app.scope_win is None)
ck("摘要显示为全部程序", app.lab_scope.cget("text") == "全部程序",
   app.lab_scope.cget("text"))

# ---- 2. 打开二级窗口 ----
app.open_scope()
root.update()
ck("二级窗口已打开", app.scope_win is not None)
ck("窗口标题", app.scope_win.title() == "接管范围", app.scope_win.title())
ck("候选列表非空", app.scope_box.size() > 0, "共 %d 项" % app.scope_box.size())
ck("列表显示全部候选",
   app.scope_box.size() == len(app.scope_items),
   "%d vs %d" % (app.scope_box.size(), len(app.scope_items)))
ck("计数初始为 0", app.lab_scope_count.cget("text") == "已选 0 项",
   app.lab_scope_count.cget("text"))
ck("列表计数显示总数",
   app.lab_scope_list.cget("text") == "共 %d 项" % len(app.scope_items),
   app.lab_scope_list.cget("text"))

# ---- 3. 重复打开不会建第二个窗口 ----
w1 = app.scope_win
app.open_scope()
root.update()
ck("重复打开复用同一窗口", app.scope_win is w1)

# ---- 4. 勾选两项（走真实事件通路）----
items = list(app.scope_box.get(0, "end"))
targets = [i for i in items if i in ("python.exe", "curl.exe")] or list(items[:2])
app.scope_box.selection_clear(0, "end")
for t in targets:
    app.scope_box.selection_set(items.index(t))
app._scope_on_select()
root.update()
ck("勾选已记入 scope_sel", sorted(app.scope_sel) == sorted(targets),
   "%s vs %s" % (sorted(app.scope_sel), sorted(targets)))
ck("计数显示已选 %d 项" % len(targets),
   "已选 %d 项" % len(targets) in app.lab_scope_count.cget("text"),
   app.lab_scope_count.cget("text") + " (targets=%s)" % targets)
ck("已改动时标未保存", "未保存" in app.lab_scope_count.cget("text"),
   app.lab_scope_count.cget("text"))

# ---- 5. 搜索过滤 + 全选只作用于筛出来的项 ----
app.scope_search.set(targets[0][:4])
app._scope_fill()
root.update()
shown = list(app.scope_box.get(0, "end"))
ck("搜索后列表被筛短", len(shown) < len(items), "%d vs %d" % (len(shown), len(items)))
ck("筛出的都含关键词", all(targets[0][:4] in s for s in shown), str(shown[:3]))
ck("计数显示筛选比例",
   app.lab_scope_list.cget("text").startswith("筛出"),
   app.lab_scope_list.cget("text"))
app._scope_all(True)
root.update()
ck("全选只加筛出来的项",
   all(s in app.scope_sel for s in shown) and len(app.scope_sel) >= len(shown),
   "%s" % sorted(app.scope_sel))
app._scope_all(False)
root.update()
ck("清空只清筛出来的项",
   all(s not in app.scope_sel for s in shown), "%s" % sorted(app.scope_sel))
app.scope_search.set("")
app._scope_fill()
root.update()

# ---- 6. 未保存时点返回：取消 → 留在窗口；确认 → 关闭并回滚 ----
for t in targets:
    app.scope_box.selection_set(list(app.scope_box.get(0, "end")).index(t))
app._scope_on_select()
app.scope_mode.set("only")
root.update()
ck("改模式也算未保存", app._scope_dirty())

G.messagebox.askyesno = lambda *a, **k: False
ck("取消确认时不关闭", app.close_scope() is False and app.scope_win is not None)
G.messagebox.askyesno = lambda *a, **k: True
ck("确认后关闭窗口", app.close_scope() is True and app.scope_win is None)
ck("放弃改动后模式回到引擎值", app.scope_mode.get() == W.G.scope_mode,
   "%s vs %s" % (app.scope_mode.get(), W.G.scope_mode))
ck("放弃改动后选择已回滚", app.scope_sel == set(W.G.scope_list),
   "%s vs %s" % (sorted(app.scope_sel), W.G.scope_list))

# ---- 7. 重新打开：以引擎值为起点 ----
app.open_scope()
root.update()
ck("重开后选择为空", app.scope_sel == set(), str(sorted(app.scope_sel)))
ck("重开后模式为 all", app.scope_mode.get() == "all", app.scope_mode.get())

# ---- 8. 勾选并保存 ----
app.scope_mode.set("only")
for t in targets:
    app.scope_box.selection_set(list(app.scope_box.get(0, "end")).index(t))
app._scope_on_select()
app.apply_scope()
root.update()
ck("保存后窗口关闭", app.scope_win is None)
ck("only 模式已生效", W.G.scope_mode == "only", W.G.scope_mode)
ck("名单与勾选一致", sorted(W.G.scope_list) == sorted(targets),
   "%s vs %s" % (W.G.scope_list, targets))
ck("主界面摘要已更新",
   app.lab_scope.cget("text").startswith("仅接管 %d 个程序" % len(targets)),
   app.lab_scope.cget("text"))

# ---- 9. 再打开：回填已保存的选择 ----
app.open_scope()
root.update()
ck("重开后回填选择", sorted(app.scope_sel) == sorted(targets), str(sorted(app.scope_sel)))
ck("重开后模式回填 only", app.scope_mode.get() == "only", app.scope_mode.get())
sel_now = [list(app.scope_box.get(0, "end"))[i] for i in app.scope_box.curselection()]
ck("列表里确实选中", sorted(sel_now) == sorted(targets), str(sel_now))
ck("已保存时不标未保存", "未保存" not in app.lab_scope_count.cget("text"),
   app.lab_scope_count.cget("text"))

# ---- 10. 恢复到全部接管，避免影响其它用例 ----
app.clear_scope()
app.scope_mode.set("all")
app.apply_scope()
root.update()
ck("清空后名单为空", W.G.scope_list == [], str(W.G.scope_list))
ck("all 模式已生效", W.G.scope_mode == "all", W.G.scope_mode)
ck("摘要回到全部程序", app.lab_scope.cget("text") == "全部程序", app.lab_scope.cget("text"))

# ---- 11. 引擎侧的兜底仍然有效 ----
W.set_scope("bogus", ["x"])
ck("非法模式回落 all", W.G.scope_mode == "all", W.G.scope_mode)
W.set_scope("only", ["A.EXE", "a.exe", " b.exe ", ""])
ck("名单去重+小写+去空", W.G.scope_list == ["a.exe", "b.exe"], str(W.G.scope_list))
W.set_scope("all", [])

try:
    root.destroy()
except Exception:
    pass

print("=== GUI 接管范围（二级窗口）自检 ===")
ok = True
for n, v, e in checks:
    print(("  PASS  " if v else "  FAIL  ") + n + (("  <- " + e) if (e and not v) else ""))
    ok = ok and v
print("总体:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
