"""弱网组合 GUI 自检：不打包、不起服务端口，只实例化 App 走一遍组合流程。

弹窗（messagebox）会被替换成打印，否则无人值守时会卡在模态框上。
"""
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
W._preset_files = lambda: [PFILE]          # 隔离存档，别动用户真实的组合
W.G.combos = None
W.G.user_presets = None

INFO = []
GUI.messagebox.showinfo = lambda *a, **k: INFO.append(("info", a[1] if len(a) > 1 else ""))
GUI.messagebox.showerror = lambda *a, **k: INFO.append(("error", a[1] if len(a) > 1 else ""))
GUI.messagebox.askyesno = lambda *a, **k: True


def pump(root, n=3):
    for _ in range(n):
        root.update()
        time.sleep(0.05)


loop = asyncio.new_event_loop()
threading.Thread(target=loop.run_forever, daemon=True).start()
tk.NoDefaultRoot() if False else None
root = tk.Tk()
root.withdraw()          # 不真的把窗口露出来，避免抢焦点
app = GUI.App(root, loop, 18999)

print("1 组合区已创建：grid=%s 初始按钮=%d 提示=%r"
      % (bool(app.combo_grid.winfo_exists()), len(app.combo_btns),
         app.lab_combo.cget("text")[:24]))

# --- 新建：添加行到上限 ---
app.open_combo_editor()
pump(root)
print("2 编辑器默认行数=%d 名称=%r" % (len(app._combo_rows), app.combo_name.get()))
app.combo_name.set("地铁通勤")
app._combo_add_row({"key": "4g_weak", "minutes": 0, "seconds": 2})
app._combo_add_row({"key": "elevator", "minutes": 0, "seconds": 1})
for i, (key, mi, se) in enumerate([("5g", 0, 2), ("4g_weak", 0, 2), ("elevator", 0, 1)]):
    r = app._combo_rows[i]
    r["cb"].current(r["keys"].index(key))
    r["mv"].set(str(mi))
    r["sv"].set(str(se))
app._combo_add_row({"key": "high_loss", "minutes": 0, "seconds": 1})
app._combo_add_row({"key": "direct", "minutes": 9, "seconds": 0})
app._combo_add_row({"key": "5g", "minutes": 0, "seconds": 5})   # 第 6 段：应被拒
app._combo_add_row({"key": "5g", "minutes": 0, "seconds": 5})
print("3 行数=%d（上限 %d） 添加按钮=%r state=%s"
      % (len(app._combo_rows), W.MAX_COMBO_STEPS,
         app.btn_combo_add.cget("text"), app.btn_combo_add.cget("state")))

# --- 校验：某段时间全空必须拦下 ---
app._combo_rows[1]["mv"].set("0")
app._combo_rows[1]["sv"].set("0")
INFO.clear()
app.save_combo()
print("4 时间空的拦截：%s" % (str(INFO[-1]) if INFO else "没拦住（BUG）"))
app._combo_rows[1]["sv"].set("2")

# --- 校验：名字为空 ---
app.combo_name.set("  ")
INFO.clear()
app.save_combo()
print("5 空名拦截：%s" % (str(INFO[-1]) if INFO else "没拦住（BUG）"))
app.combo_name.set("地铁通勤")

# --- 正常保存 ---
app.save_combo()
pump(root)
combo = W.get_combos()[0]
print("6 已保存：name=%s steps=%s" % (combo["name"], [(s["key"], s["minutes"], s["seconds"]) for s in combo["steps"]]))
print("  场景区最后的按钮=%s 磁盘=%s" % (list(app.combo_btns.values())[0].cget("text"), PFILE.exists()))

# --- 选中：未开启限速时不生效 ---
W.G.limit_on = False
key = W.COMBO_KEY_PREFIX + combo["id"]
app.apply_preset(key)
app._sync_combo_bar()
print("7 未开启选中：preset_key=%s task=%s 提示=%r"
      % (W.G.preset_key, W.G.combo_task, app.lab_combo.cget("text")[:40]))

# --- 开启限速 → 开始播放 ---
app._run(W.set_limit(True))
time.sleep(0.3)
pump(root)
app._sync_combo_bar()
print("8 播放中：state=%s 提示=%r" % (W.combo_state() and W.combo_state()["step"],
                                    app.lab_combo.cget("text")[:40]))
time.sleep(2.2)
app._sync_combo_bar()
print("9 自动切段：step=%s/%s 当前 latency=%s 提示=%r"
      % (W.combo_state() and W.combo_state()["step"], len(combo["steps"]),
         W.G.profile["latency_down_ms"], app.lab_combo.cget("text")[:36]))

# --- 编辑 ---
app.edit_combo()
pump(root)
print("10 编辑弹窗：标题=%r 行数=%d 名称=%r"
      % (app.combo_win.title(), len(app._combo_rows), app.combo_name.get()))
app.combo_loop.set(True)
app.save_combo()
pump(root)
print("11 改循环后：loop=%s" % W.get_combos()[0]["loop"])

# --- 删除 ---
app.apply_preset(key)
info_before = len(app.combo_btns)
app.del_combo()
pump(root)
print("12 删除后：组合数=%d 场景区按钮=%d → %d preset_key=%s"
      % (len(W.get_combos()), info_before, len(app.combo_btns), W.G.preset_key))

app.root.destroy()
loop.call_soon_threadsafe(loop.stop)
print("DONE")
