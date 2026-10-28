"""环境共享窗口：弱网组合那一栏的自检（不起端口、不真推送）

跑 App 实例 → 打开共享窗口 → 看组合列表是否在、勾选中之后推送按钮是否亮、
收件箱里收到组合时是否标了「组合」；真正的 HTTP 推送用假的 executor 顶替。
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

PUSHED = []
DROPPED = []          # 模拟对方回执「这个组合我收不下」


def fake_run(self, coro, *a, **k):
    """顶掉 App._run：把 push_to_peer 的协程同步跑完（它内部才是 HTTP）"""
    return asyncio.run_coroutine_threadsafe(coro, self.loop).result(5)


async def fake_push_to_peer(peer, item_ids, combo_ids=None):
    PUSHED.append((list(item_ids or []), list(combo_ids or [])))
    return {"ok": True, "to": "对面小张", "accepted": ["a"], "pending": [],
            "dropped": list(DROPPED)}


W.push_to_peer = fake_push_to_peer
GUI.App._run = fake_run

FAILS = []


def check(name, cond, extra=""):
    print(("  OK   " if cond else "  FAIL ") + name + (("  <- " + str(extra)) if extra else ""))
    if not FAILS and not cond:
        pass
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

# 先造 1 条自定义 + 1 支组合
p1, _ = W.upsert_user_preset("隧道断流", {"down_kbps": 200, "loss_pct": 20})
cb, _ = W.upsert_combo("地铁通勤",
                       [{"key": "5g", "minutes": 0, "seconds": 30},
                        {"key": "u:" + p1["id"], "minutes": 1, "seconds": 0}])

print("== 1. 打开共享窗口 ==")
W.ensure_identity()
W.G.user_name = "小李"
app.open_share()
pump(root)
check("窗口已建", app.share_win is not None)
check("有组合那一栏", hasattr(app, "share_combo") and app.share_combo.winfo_exists())
check("组合列表渲染出 1 支", len(app.share_combo_list) == 1, app.share_combo_list)
txt = app.share_combo.get(0)
check("列表行写出组合摘要", "地铁通勤" in txt and "→" in txt, txt)

print("== 2. 空选 → 推送按钮灰着 ==")
# 塞一个「在线设备」：直接进 G.peers，刷新列表时才会被留下来
W.G.peers["p1"] = {"id": "p1", "name": "对面小张", "ip": "192.168.1.9",
                   "platform": "windows", "share": 8900, "at": time.time()}
app.reload_share()
pump(root)
check("设备列表里能看到对面", len(app.share_peer_list) == 1,
      [p["name"] for p in app.share_peer_list])
app._share_sync_btns()
pump(root)
check("没勾任何内容时不可点", app.btn_push.cget("state") == "disabled",
      app.btn_push.cget("state"))

print("== 3. 只勾组合也能推 ==")
app.share_combo.selection_set(0)
app.share_peers.selection_set(0)
app._share_sync_btns()
pump(root)
check("勾选组合后按钮亮起", app.btn_push.cget("state") == "normal",
      app.btn_push.cget("state"))
app.push_share()
pump(root)
check("引擎收到的是 combo_ids（items 为空）",
      PUSHED and PUSHED[-1][1] == [cb["id"]] and PUSHED[-1][0] == [], PUSHED)
tip = app.lab_share_tip.cget("text")
check("提示里标注了这是组合", "🔀" in tip and "地铁通勤" in tip, tip)

print("== 4. 环境与组合一起勾 ==")
W.G.peers["p1"]["at"] = time.time()
app.reload_share()
app.share_peers.selection_set(0)
app.share_mine.selection_set(0)
app.share_combo.selection_set(0)
pump(root)
check("勾选状态在刷新后仍保留",
      tuple(app.share_mine.curselection()) == (0,)
      and tuple(app.share_combo.curselection()) == (0,),
      (app.share_mine.curselection(), app.share_combo.curselection()))
app.push_share()
pump(root)
check("两类同时推送：items + combos 都有值",
      PUSHED[-1][0] == [p1["id"]] and PUSHED[-1][1] == [cb["id"]], PUSHED[-1])

print("== 4b. 对方收不下时要有回执 ==")
# 对方那台机器上这段场景没有、又没带参数快照 → 组合会被丢弃。
# 这种情况下推送方必须看到提示，否则会以为推成功了。
DROPPED.clear()
DROPPED.append({"name": "地铁通勤", "reason": "本机没有这一档"})
app.push_share()
pump(root)
tip3 = app.lab_share_tip.cget("text")
check("提示里说明对方收不下", "收不下" in tip3, tip3)
check("提示里写清是哪支组合", "地铁通勤" in tip3, tip3)
DROPPED.clear()

print("== 5. 收件箱同时列出环境 / 组合 ==")
# 模拟别人推来：同名环境 + 同名组合（都该进收件箱）
other = {"from": "小张", "from_id": "xx",
         "items": [{"name": "隧道断流", "profile": {"down_kbps": 50}}],
         "combos": [{"name": "地铁通勤", "loop": True,
                     "steps": [{"key": "5g", "minutes": 0, "seconds": 9}]}]}
W.receive_share(other)
app.reload_share()
pump(root)
rows = [app.share_inbox.get(i) for i in range(app.share_inbox.size())]
check("收件箱 2 条", len(rows) == 2, rows)
check("环境条目标「环境」", any(r.startswith("[环境]") for r in rows), rows)
check("组合条目标「组合」", any(r.startswith("[组合]") for r in rows), rows)

print("== 6. 处理收件箱里的组合 ==")
idx = [i for i, r in enumerate(rows) if r.startswith("[组合]")][0]
app.share_inbox.selection_clear(0, "end")
app.share_inbox.selection_set(idx)
W.G.inbox[app.share_inbox_list[idx]["id"]]  # 选中项确实在
GUI.simpledialog.askstring = lambda *a, **k: "小张版通勤"
GUI.simpledialog.askstring.__name__ = "askstring"
app.resolve_share("rename")
pump(root)
names = [c["name"] for c in W.get_combos()]
check("另存后的组合出现在列表里", "小张版通勤" in names, names)
tip2 = app.lab_share_tip.cget("text")
check("提示写明是组合", "🔀" in tip2 and "小张版通勤" in tip2, tip2)
app.share_combo.delete(0, "end")
app.reload_share()
pump(root)
check("共享窗口的列表跟着刷新（2 支）", len(app.share_combo_list) == 2,
      [c["name"] for c in app.share_combo_list])

app.close_share()
pump(root)
check("关闭后不再重复刷新", app.share_win is None)

print("\n" + ("全部通过 ✅" if not FAILS else "失败 %d 项：%s" % (len(FAILS), "、".join(FAILS))))
sys.exit(1 if FAILS else 0)
