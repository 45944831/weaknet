# -*- coding: utf-8 -*-
"""pytest 夹具。

设计要点
--------
* 每个用例都有自己的临时存储目录：自定义场景 / 设置 / 日志一律写 tmp_path，
  绝不动仓库里的 weaknet_presets.json 与 weaknet_settings.json。
* engine 在独立线程里跑自己的事件循环（端口传 0，随机分配），用例通过 HTTP
  访问它 —— 与 GUI/脚本用的是同一条路径，测的是真实管道而不是 mock。
* 共享服务默认关闭：共享会绑定 0.0.0.0 并广播 UDP，CI 里既慢又容易被防火墙拦，
  需要它的用例自己起 handle_share（见 test_integration_share.py）。
"""
import asyncio
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
for p in (str(ROOT), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

import weaknet as W  # noqa: E402
from support import CtrlClient  # noqa: E402

try:                        # Windows 控制台是 GBK：中文用例名打印不能炸
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


# ---------------------------------------------------------------- 状态复位

def reset_engine_state():
    """把模块级 G 复位成「刚启动、直连」的样子。

    profile 原地 clear/update 而不是换 dict：在途连接持有的是同一个 dict 引用，
    换掉的话上一个用例的连接会继续按旧参数跑（真机上这就是「点了停止还是慢」）。
    """
    prof = W.G.profile
    prof.clear()
    prof.update(W.default_profile())
    W.G.preset_key = "direct"
    W.G.limit_on = False
    W.G.last_profile = None
    W.G.gate_open = True
    W.G.gen = 0
    W.G.wake = None
    W.G.flap_task = None
    W.G.combo_task = None
    W.G.combo_id = None
    W.G.combo_step = -1
    W.G.combo_deadline = 0
    W.G.active.clear()
    W.G.contexts.clear()
    W.G.events.clear()
    W.G.inbox.clear()
    W.G.peers.clear()
    W.G.conns_total = 0
    W.G.conns_active = 0
    W.G.bytes_up = 0
    W.G.bytes_down = 0
    W.G.bytes_bypass = 0
    W.G.share_pin = ""
    W.G.allow_legacy = False
    W.G.share_port = 0
    W.G.share_server = None


@pytest.fixture
def tmp_store(tmp_path, monkeypatch):
    """把落盘位置全部指到 tmp_path，并装上假身份。"""
    store = tmp_path / "weaknet_presets.json"
    monkeypatch.setattr(W, "_PRESET_FILES", [store], raising=False)
    monkeypatch.setattr(W, "_settings_file",
                        lambda: tmp_path / "weaknet_settings.json")
    monkeypatch.setattr(W, "LOG_FILE", tmp_path / "weaknet.log", raising=False)
    monkeypatch.setattr(W, "_LOG_SINK", None, raising=False)
    monkeypatch.setattr(W.G, "user_presets", None)
    monkeypatch.setattr(W.G, "combos", None)
    W.G.inbox.clear()
    W.G.peers.clear()
    W.ensure_identity()
    return tmp_path


# ---------------------------------------------------------------- 进程内引擎

class Engine:
    """在别的事件循环里跑着的 WeakNet，用例只通过 socket 和它说话。"""

    def __init__(self, proxy_port, ctrl_port):
        self.proxy_port = proxy_port
        self.ctrl_port = ctrl_port
        self.proxy = ("127.0.0.1", proxy_port)
        self.api = CtrlClient("127.0.0.1", ctrl_port)

    def apply(self, **params):
        """下发自定义参数（会顺带打开限速）"""
        return self.api.post("/api/profile", params)

    def status(self):
        r = self.api.get("/api/status")
        assert r.status == 200, r.body
        return r.body

    def wait_status(self, pred, timeout=10.0):
        from support import wait_until
        return wait_until(lambda: pred(self.status()) or False, timeout)


class _EngineThread(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True, name="weaknet-engine")
        self.loop = asyncio.new_event_loop()
        self.ready = threading.Event()
        self.error = None
        self.proxy = None
        self.ctrl = None
        self.proxy_port = 0
        self.ctrl_port = 0

    def run(self):
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_until_complete(self._boot())
            self.ready.set()
            self.loop.run_forever()
        except BaseException as e:          # 启动失败要在主线程里报出来
            self.error = e
            self.ready.set()
        finally:
            try:
                self.loop.close()
            except Exception:
                pass

    async def _boot(self):
        self.proxy, self.ctrl = await W.serve("127.0.0.1", 0, 0)
        self.proxy_port = self.proxy.sockets[0].getsockname()[1]
        self.ctrl_port = self.ctrl.sockets[0].getsockname()[1]

    async def _shutdown(self):
        for srv in (self.proxy, self.ctrl):
            if srv is not None:
                srv.close()
        for srv in (self.proxy, self.ctrl):
            if srv is not None:
                await srv.wait_closed()
        for w in list(W.G.active):
            try:
                w.close()
            except Exception:
                pass
        me = asyncio.current_task()
        pending = [t for t in asyncio.all_tasks() if t is not me]
        for t in pending:
            t.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        await asyncio.sleep(0)

    def stop(self):
        """在引擎自己的循环里优雅收尾，再停循环、关循环。

        不能在别的线程直接 loop.stop() 然后 run_until_complete(_shutdown())：
        Windows 的 proactor 在仍有未完成的 overlapped I/O 时被重新驱动，
        会直接 access violation（进程崩溃，而不是用例失败）。
        """
        if not self.ready.is_set():
            return
        if self.error is None and self.loop.is_running():
            try:
                fut = asyncio.run_coroutine_threadsafe(self._shutdown(), self.loop)
                fut.result(timeout=10)
            except Exception:
                pass
        if self.loop.is_running():
            self.loop.call_soon_threadsafe(self.loop.stop)
        self.join(timeout=15)


@pytest.fixture
def engine(tmp_store, monkeypatch):
    reset_engine_state()

    async def _skip_sharing():
        return None

    monkeypatch.setattr(W, "start_sharing", _skip_sharing)
    th = _EngineThread()
    th.start()
    if not th.ready.wait(20) or th.error is not None:
        th.stop()
        pytest.fail("进程内引擎启动失败：%r" % (th.error,))
    try:
        yield Engine(th.proxy_port, th.ctrl_port)
    finally:
        th.stop()
        reset_engine_state()


# ---------------------------------------------------------------- 本地源站

class Origin:
    def __init__(self, server):
        self.server = server
        self.host = "127.0.0.1"
        self.port = server.server_address[1]


class _OriginHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def _send_bytes(self, body, ctype="application/octet-stream"):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        self.wfile.flush()

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
        if u.path == "/small":
            self._send_bytes(b"weaknet-origin-small", "text/plain")
            return
        if u.path == "/big":
            kb = max(1, int(q.get("kb", "512")))
            self._send_bytes(b"x" * (kb * 1024))
            return
        if u.path == "/slow":
            total = max(1, int(q.get("kb", "64"))) * 1024
            chunk = max(1, int(q.get("chunk", "4096")))
            delay = float(q.get("delay", "0.02"))
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(total))
            self.end_headers()
            sent = 0
            try:
                while sent < total:
                    n = min(chunk, total - sent)
                    self.wfile.write(b"s" * n)
                    self.wfile.flush()
                    sent += n
                    time.sleep(delay)
            except (BrokenPipeError, ConnectionResetError):
                pass
            return
        self.send_error(404)


@pytest.fixture
def http_origin():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _OriginHandler)
    th = threading.Thread(target=srv.serve_forever, daemon=True,
                          name="origin-http")
    th.start()
    try:
        yield Origin(srv)
    finally:
        srv.shutdown()
        srv.server_close()
        th.join(timeout=5)
