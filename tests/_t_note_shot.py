# -*- coding: utf-8 -*-
"""渲染参数说明小窗并截图（排版回归用）

用法：python _t_note_shot.py <param_key> <out.png>
"""
import os
import sys
import time
import tkinter as tk

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

import weaknet_gui as G   # noqa: E402
import _shot_win as SW    # noqa: E402

# 仓库根：本文件在 <repo>/tests/ 下，向上两级即仓库根。
# 用相对定位而不是写死绝对路径，换机器/换目录也能直接跑。
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def main():
    key = sys.argv[1] if len(sys.argv) > 1 else "latency_up_ms"
    out = sys.argv[2] if len(sys.argv) > 2 else "_shot_note.png"

    root = tk.Tk()
    root.title("WeakNetNoteShot")
    root.geometry("900x900+0+0")
    app = G.App(root, None, 8899)
    try:
        root.after_cancel(app._after_id)
    except Exception:
        pass
    app._closing = True
    root.update()

    app.open_param_note(key)
    for _ in range(6):
        root.update_idletasks()
        root.update()
        time.sleep(0.05)

    win = app.param_win
    print("param_win reqwidth=%d reqheight=%d"
          % (win.winfo_reqwidth(), win.winfo_reqheight()))
    SW.grab("参数说明 · " + dict((f[0], f[1]) for f in G.FIELDS)[key], out)

    root.destroy()


if __name__ == "__main__":
    main()
