# -*- coding: utf-8 -*-
"""B6/B7 专用：按传入端口以子进程方式启动引擎。

用法（由 _t_win_opt_e2e.py 调用）：
    python tests/_eng_sub.py --proxy-port 8889 --ctrl-port 8900
"""
import asyncio
import os
import pathlib
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import weaknet  # noqa: E402


if __name__ == "__main__":
    import signal

    if sys.platform == "win32":
        # 控制台里 Ctrl+Break 默认是硬终止（不走 atexit）；转成 KeyboardInterrupt
        # 才能验证「退出时把缓冲日志刷盘」这条路径。
        signal.signal(signal.SIGBREAK, signal.default_int_handler)

    # 让引擎把 argv[0] 当作 <repo>/weaknet.py，解析参数时行为与直接运行一致
    sys.argv[0] = os.path.join(ROOT, "weaknet.py")

    # B6 需要把空闲回收压缩到「3s 阈值 / 1s 扫描」才便于观测
    if os.environ.get("WN_IDLE_SEC"):
        weaknet.TUNNEL_IDLE_SEC = float(os.environ["WN_IDLE_SEC"])
    if os.environ.get("WN_REAP_SEC"):
        weaknet.IDLE_REAP_INTERVAL = float(os.environ["WN_REAP_SEC"])
    # B7 需要一份只属于子引擎的日志，才能确认是它退出时落盘的
    if os.environ.get("WN_LOG"):
        weaknet.LOG_FILE = pathlib.Path(os.environ["WN_LOG"])

    try:
        asyncio.run(weaknet.main())
    except KeyboardInterrupt:
        pass
