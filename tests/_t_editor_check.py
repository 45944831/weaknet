# -*- coding: utf-8 -*-
"""打开「我的弱网组合」编辑器并截图，用于目检排版。

ColorOS 的 uiautomator dump 不可靠（返回的控件树残缺），
所以这里用「滚动 + 固定坐标点击 + 截图目检」的方式走。
"""
import os
import sys

# 仓库根：本文件在 <repo>/tests/ 下，向上两级即仓库根。
# 用相对定位而不是写死绝对路径，换机器/换目录也能直接跑。
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _t_dev_lib as L


def open_editor(tag="x01"):
    L.app_restart(wait=3.5)
    # 滚到底部找「我的弱网组合」卡片
    for _ in range(8):
        L.sh("input swipe 540 1800 540 900 260")
        time.sleep(0.25)
    time.sleep(0.6)
    L.shot(tag + "_main")
    # 「组合」标签页
    L.sh("input tap 775 560")
    time.sleep(1.0)
    L.shot(tag + "_combo_tab")
    # 长按第一个组合打开编辑器
    L.sh("input swipe 540 1185 540 1185 900")
    time.sleep(1.2)
    L.shot(tag + "_editor")
    print("done ->", tag)


if __name__ == "__main__":
    open_editor(sys.argv[1] if len(sys.argv) > 1 else "x01")
