"""GUI 自定义栏自检（无需真正显示窗口）：保存 / 列表 / 应用 / 删除 / 重名 / 持久化"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tkinter as tk
import weaknet as W
import weaknet_gui as G

checks = []


def ck(name, cond, extra=""):
    checks.append((name, bool(cond), extra))


def run(coro):
    """没有引擎线程时的替代执行器"""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# 干净起步
for it in list(W.get_user_presets()):
    W.delete_user_preset(it["id"])
W.G.user_presets = None

root = tk.Tk()
root.withdraw()
app = G.App(root, loop=None, ctrl_port=8899)
app._run = run
root.update()

# ---- 1. 控件 ----
ck("自定义栏列表控件存在", hasattr(app, "mine_box"))
ck("名称输入框存在", hasattr(app, "mine_name"))
ck("初始为空提示", "还没有保存" in app.lab_mine.cget("text"), app.lab_mine.cget("text"))
root.deiconify()
root.update()
ck("自定义栏列表默认即可见（不在折叠区里）", bool(app.mine_box.winfo_ismapped()))
root.withdraw()

# ---- 2. 空名字不保存 ----
G.messagebox.showinfo = lambda *a, **k: None
app.mine_name.set("   ")
try:
    app.mine_name.set("   ")
    G.messagebox.showinfo = lambda *a, **k: checks.append(("（提示已拦截）", True, ""))
    W.upsert_user_preset_called = False
    app.save_mine()
except Exception as e:
    ck("空名字保存不抛异常", False, repr(e))
ck("空名字未产生条目", len(W.get_user_presets()) == 0, str(W.get_user_presets()))

# ---- 3. 正常保存 ----
for k in app.vars:
    app.vars[k].set("0")
app.vars["down_kbps"].set("1800")
app.vars["up_kbps"].set("600")
app.vars["latency_down_ms"].set("120")
app.vars["loss_pct"].set("4")
app.mine_name.set("地铁 3G 抖动")
app.save_mine()
root.update()
items = W.get_user_presets()
ck("保存成功", len(items) == 1, str(items))
ck("名字正确", items and items[0]["name"] == "地铁 3G 抖动")
ck("参数已入库", items and items[0]["profile"]["down_kbps"] == 1800)
ck("列表已刷新", app.mine_box.size() == 1)
ck("列表文本含名字与摘要", "地铁 3G 抖动" in app.mine_box.get(0) and "kbps" in app.mine_box.get(0),
   app.mine_box.get(0) if app.mine_box.size() else "")
ck("保存后清空输入框", app.mine_name.get() == "")

# ---- 4. 重名自动加序号 ----
app.mine_name.set("地铁 3G 抖动")
app.vars["down_kbps"].set("900")
app.save_mine()
root.update()
names = [it["name"] for it in W.get_user_presets()]
ck("重名自动去重", names == ["地铁 3G 抖动", "地铁 3G 抖动 (2)"], str(names))
ck("两条参数互不干扰",
   W.get_user_presets()[0]["profile"]["down_kbps"] == 1800
   and W.get_user_presets()[1]["profile"]["down_kbps"] == 900)

# ---- 5. 应用选中的场景 ----
pid = W.get_user_presets()[1]["id"]
app.mine_box.selection_clear(0, "end")
app.mine_box.selection_set(1)
app.apply_mine()
root.update()
ck("应用后 preset_key 指向该场景", W.G.preset_key == "u:" + pid, W.G.preset_key)
ck("应用后参数为保存值", W.G.profile["down_kbps"] == 900, str(W.G.profile["down_kbps"]))
ck("应用后场景名显示为自定义名", W.G.profile["name"] == "地铁 3G 抖动 (2)", W.G.profile["name"])

# ---- 6. 删除（跳过确认弹窗）----
G.messagebox.askyesno = lambda *a, **k: True
app.mine_box.selection_clear(0, "end")
app.mine_box.selection_set(0)
app.del_mine()
root.update()
left = W.get_user_presets()
ck("删除生效", len(left) == 1 and left[0]["id"] == pid, str(left))
ck("删除后列表同步", app.mine_box.size() == 1)

# ---- 7. 落盘 + 可被重新加载 ----
ck("已写入 JSON 文件", any(f.exists() for f in W._preset_files()),
   str(W._preset_files()))
reloaded = W.load_user_presets()
ck("重新读取内容一致",
   len(reloaded) == 1 and reloaded[0]["id"] == pid
   and reloaded[0]["profile"]["down_kbps"] == 900, str(reloaded))

# ---- 8. 引擎侧列表变化时自动重画 ----
W.upsert_user_preset("控制台那端新增的", {"down_kbps": 100})
orig = app.mine_sig
try:
    app.refresh.__self__  # noqa
except Exception:
    pass
W.G.user_presets = W.load_user_presets()
app.reload_mine()
root.update()
ck("外部新增后刷新可见", app.mine_box.size() == 2, str(app.mine_box.size()))
ck("签名随数据变化", app.mine_sig != orig)

# ---- 9. summarize 摘要 ----
ck("摘要含限速信息", "↓1800" in G.summarize({"down_kbps": 1800, "up_kbps": 600}))
ck("全零摘要为不限制", G.summarize(W.default_profile()) == "（不限制）")

# 清理，别把测试数据留在用户机器上
for it in list(W.get_user_presets()):
    W.delete_user_preset(it["id"])
root.destroy()

print("=== GUI 自定义栏自检 ===")
ok = True
for n, v, e in checks:
    print(("  PASS  " if v else "  FAIL  ") + n + (("  <- " + e) if (e and not v) else ""))
    ok = ok and v
print("总体:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
