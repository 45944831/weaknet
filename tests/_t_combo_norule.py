"""弱网组合「不能嵌套组合」规则自检。

覆写 messagebox 避免模态框卡住；存档隔离到临时目录，不碰用户真实数据。
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
W._preset_files = lambda: [PFILE]
W.G.combos = None
W.G.user_presets = None

INFO = []
GUI.messagebox.showinfo = lambda *a, **k: INFO.append(a[1] if len(a) > 1 else "")
GUI.messagebox.showerror = lambda *a, **k: INFO.append(a[1] if len(a) > 1 else "")
GUI.messagebox.askyesno = lambda *a, **k: True


def pump(root, n=4):
    for _ in range(n):
        root.update()
        time.sleep(0.05)


def last_info():
    return str(INFO[-1]) if INFO else "（无提示）"


loop = asyncio.new_event_loop()
threading.Thread(target=loop.run_forever, daemon=True).start()
root = tk.Tk()
root.withdraw()
app = GUI.App(root, loop, 18999)

# 先存两支组合，用来当「不该出现在下拉里」的靶子
c_a, _ = W.upsert_combo("外层组合A", [{"key": "5g", "minutes": 0, "seconds": 2}])
c_b, _ = W.upsert_combo("外层组合B", [{"key": "4g", "minutes": 0, "seconds": 2}])
W.G.combos = None
app.reload_combo()

print("1 已有组合数：%d %s" % (len(app.combo_items), [c["name"] for c in app.combo_items]))

labels, keys = app._scene_options()
nested_in = [k for k in keys if k.startswith(W.COMBO_KEY_PREFIX)]
name_in = [lb for lb in labels if lb in ("外层组合A", "外层组合B")]
print("2 下拉候选 %d 项；混入组合 key=%s，混入组合名=%s（都应为空）" % (len(keys), nested_in, name_in))

# 打开编辑器，逐行核对 Combobox 的实际 values
app.open_combo_editor()
pump(root)
vals = []
for r in app._combo_rows:
    cb = r.get("cb")
    if cb is not None:
        vals.extend(cb.cget("values"))
hit = [v for v in vals if v in ("外层组合A", "外层组合B", "🔀 外层组合A")]
print("3 编辑器下拉实际值含组合的项：%s（应为空）" % hit)

# 强行把一行改成组合 key（模拟绕过下拉的路径），保存必须被拦
app.combo_name.set("偷偷嵌套")
r0 = app._combo_rows[0]
r0["keys"] = [W.COMBO_KEY_PREFIX + c_a["id"]] + list(r0["keys"])
r0["cb"].config(values=["外层组合A"] + list(r0["cb"].cget("values")))
r0["cb"].current(0)
r0["mv"].set("0")
r0["sv"].set("5")
INFO.clear()
app.save_combo()
pump(root)
saved = W.get_combos()
stored = [c for c in saved if c["name"] == "偷偷嵌套"]
print("4 绕路保存：拦住=%s | 提示=%s" % (not stored, last_info()[:34]))

# 正常保存仍要能用（别把规则做成误伤）——重开编辑器，连前面被污染的行一起丢掉
app.close_combo_editor()
pump(root)
app.open_combo_editor()
pump(root)
app._combo_add_row()          # 第二段
pump(root)
for i, (k, s) in enumerate([("5g", 1), ("4g_weak", 1)]):
    r = app._combo_rows[i]
    r["cb"].current(list(r["keys"]).index(k))
    r["mv"].set("0")
    r["sv"].set(str(s))
app.combo_name.set("正常组合")
INFO.clear()
app.save_combo()
pump(root)
W.G.combos = None
ok_saved = [c for c in W.get_combos() if c["name"] == "正常组合"]
print("5 正常保存：成功=%s steps=%s"
      % (bool(ok_saved), [s["key"] for s in ok_saved[0]["steps"]] if ok_saved else None))

# 旧存档里带着嵌套段：打开编辑器应把它去掉并提示
W.G.combos = None
dirty = W.upsert_combo(
    "历史脏组合",
    [{"key": "5g", "minutes": 0, "seconds": 1},
     {"key": W.COMBO_KEY_PREFIX + c_b["id"], "minutes": 0, "seconds": 3},
     {"key": "elevator", "minutes": 0, "seconds": 1}])
W.G.combos = None
app.reload_combo()
target = [c for c in app.combo_items if c["name"] == "历史脏组合"]
print("6 脏组合入库时已被清洗：steps=%s（应只有 5g、elevator）"
      % [s["key"] for s in target[0]["steps"]] if target else "6 组合被整体丢弃")

# 播放回归：清洗后的组合仍能按序跑
async def play():
    tgt = [c for c in W.get_combos() if c["name"] == "历史脏组合"][0]
    W.G.limit_on = True
    await W.start_combo(tgt["id"])
    await asyncio.sleep(0.15)
    st = W.combo_state()
    print("7 播放第 1 段：step=%s latency=%s（5G=20ms）"
          % (st["step"], W.G.profile["latency_down_ms"]))
    await asyncio.sleep(1.2)
    st = W.combo_state()
    print("  自动跳到第 2 段（跳过被剿的那段后是 elevator）：latency=%s"
          % W.G.profile["latency_down_ms"])
    await W.stop_combo()


asyncio.run_coroutine_threadsafe(play(), loop).result(timeout=30)

root.destroy()
print("\n结论：嵌套拦截 %s" % ("全部通过" if not nested_in and not name_in and not hit
                              and not stored and ok_saved else "有失败项，见上"))
