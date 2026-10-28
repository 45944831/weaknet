# -*- coding: utf-8 -*-
"""验证弱网组合播放时按时长自动切段。"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _t_dev_lib as L
from _t_combo_editor import dump_tree, by_desc, by_text, tap, open_editor


def main():
    L.setup_forward()
    # 0) 回到 App 的「组合」页
    open_editor()
    # 1) 关掉编辑器
    nodes = dump_tree()
    close = by_desc(nodes, "关闭组合编辑器")
    if close:
        tap(close[0])
    time.sleep(0.8)

    # 2) 先选组合（此时还没开限速，只记录 presetKey）
    nodes = dump_tree()
    cards = [n for n in nodes if n["text"] and "面板测试组合" in n["text"]]
    if not cards:
        print("没找到组合卡片，当前节点文本：",
              [n["text"] for n in nodes if n["text"]][:20])
        return
    tap(cards[0], wait=1.2)
    st = L.status() or {}
    print("选中后 preset_key =", st.get("preset_key"), " limit_on =", st.get("limit_on"))

    # 3) 开限速总开关 —— 这一步会走 resumeIfCombo()，把组合的第一段顶上来
    r = L.api("/api/run", {"enable": True})
    print("run enable ->", r)
    # 同时开 VPN 接管，让流量真的走弱网
    L.api("/api/vpn", {"enable": True})
    time.sleep(1.5)

    st = L.status() or {}
    print("播放后 preset_key =", st.get("preset_key"),
          " limit_on =", st.get("limit_on"),
          " profile =", (st.get("profile") or {}).get("name"))

    # 4) 采样 14 秒看切段
    print("\n--- 采样 profile 变化 ---")
    prev = None
    t0 = time.time()
    while time.time() - t0 < 14:
        s = L.status()
        if s:
            pn = (s.get("profile") or {}).get("name")
            pk = s.get("preset_key")
            if pn != prev:
                print("  t=%5.1fs  preset=%-18s profile=%s"
                      % (time.time() - t0, pk, pn))
                prev = pn
        time.sleep(0.25)

    # 5) 停掉
    L.api("/api/run", {"enable": False})
    L.api("/api/vpn", {"enable": False})
    print("\n已停止")


if __name__ == "__main__":
    main()
