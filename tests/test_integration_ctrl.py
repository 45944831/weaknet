# -*- coding: utf-8 -*-
"""控制接口契约用例（进程内真实服务）。

契约的意义：同一份脚本改个 host/port 要能在 Windows 与 Android 上跑。
这里只断言「状态码 + 顶层字段 + 关键语义」，不依赖机器上的具体数据，
所以能进 CI 反复跑；平台差异（VPN/悬浮窗等）也按 501 明确表达。
"""
import socket
import threading
import time

import pytest

import weaknet as W

pytestmark = pytest.mark.integration


CASES = [
    # 方法, 路径, 请求体, 期望状态, 必需顶层字段
    ("GET", "/api/status", None, 200,
     ("version", "build", "profile", "preset_key", "limit_on", "listen",
      "stats", "events", "scope", "me", "spec_hash")),
    ("GET", "/api/presets", None, 200, None),
    ("GET", "/api/params", None, 200, ("spec_hash", "items")),
    ("GET", "/api/me", None, 200,
     ("name", "id", "platform", "share", "share_ver", "pin_len")),
    ("GET", "/api/share/status", None, 200,
     ("name", "id", "share_port", "discover", "lan_ip", "inbox_count",
      "share_ver", "allow_legacy", "inbox_max", "peers", "inbox")),
    ("POST", "/api/share/status", {}, 405, ("error",)),
    ("GET", "/api/peers", None, 200, ("items", "self", "discover")),
    ("GET", "/api/inbox", None, 200, ("items",)),
    ("POST", "/api/inbox/resolve", {"id": "__nope__"}, 200, ("ok",)),
    ("POST", "/api/push", {"peer": "", "ids": []}, 400, ("error",)),
    ("GET", "/api/__nope__", None, 404, ("error",)),
    ("DELETE", "/api/status", None, 405, None),
    ("POST", "/api/vpn", {"enable": False}, 501, ("error",)),
    ("GET", "/api/float", None, 501, ("error",)),
]


@pytest.mark.parametrize("method,path,body,want,keys", CASES,
                         ids=["%s %s" % (c[0], c[1]) for c in CASES])
def test_contract(engine, method, path, body, want, keys):
    r = engine.api.request(method, path, body)
    assert r.status == want, (r.status, r.body)
    if keys:
        assert isinstance(r.body, dict), r.body
        missing = [k for k in keys if k not in r.body]
        assert not missing, "缺少顶层字段：%r（实际：%r）" % (missing, sorted(r.body))


def test_status_reports_current_spec_hash(engine):
    body = engine.status()
    assert body["spec_hash"] == W.SPEC_HASH
    assert body["limit_on"] is False
    assert body["preset_key"] == "direct"


def test_params_payload_is_the_spec_itself(engine):
    r = engine.api.get("/api/params")
    assert r.status == 200
    assert r.body["spec_hash"] == W.SPEC_HASH
    assert r.body["items"] == W.PARAM_SPECS


def test_selecting_preset_does_not_start_throttling(engine):
    r = engine.api.post("/api/preset", {"key": "wifi"})
    assert r.status == 200 and r.body["ok"] is True
    body = engine.status()
    assert body["preset_key"] == "wifi"
    assert body["limit_on"] is False, "选个场景就把流量限住了"
    assert W.has_limits(body["profile"]) is False


def test_run_toggles_limit_and_applies_preset(engine):
    assert engine.api.post("/api/preset", {"key": "wifi"}).status == 200
    r = engine.api.post("/api/run", {"enable": True})
    assert r.status == 200 and r.body["limit_on"] is True
    body = engine.status()
    assert body["limit_on"] is True
    assert body["profile"] == W.build_preset_profile("wifi")

    r = engine.api.post("/api/run", {"enable": False})
    assert r.status == 200 and r.body["limit_on"] is False
    body = engine.status()
    assert body["limit_on"] is False
    assert body["profile"] == W.default_profile(), "停止后参数应回到直连"
    assert body["preset_key"] == "wifi", "停止不该丢掉用户选的场景"


def test_run_rejects_non_boolean_enable(engine):
    r = engine.api.post("/api/run", {"enable": "false"})
    assert r.status == 400
    assert engine.status()["limit_on"] is False


def test_custom_profile_is_clamped_and_enables(engine):
    r = engine.api.post("/api/profile", {"down_kbps": -5, "latency_up_ms": 123})
    assert r.status == 200
    body = engine.status()
    assert body["profile"]["down_kbps"] == 0
    assert body["profile"]["latency_up_ms"] == 123
    assert body["limit_on"] is True
    assert body["preset_key"] == "custom"


def test_stop_endpoint_is_idempotent(engine):
    engine.apply(latency_up_ms=200)
    for _ in range(2):
        r = engine.api.get("/api/stop")
        assert r.status == 200 and r.body["ok"] is True
    assert engine.status()["limit_on"] is False


def test_bad_preset_key_returns_400(engine):
    assert engine.api.post("/api/preset", {"key": "no-such"}).status == 400


def test_status_stays_responsive_while_pushes_stall(engine):
    """4 个推送卡在对端（对方 accept 后不回包）时，/api/status 仍要秒回。

    旧实现把推送放在控制线程里 join，4 条并发就能占满处理线程，
    GUI 每 500ms 的轮询会表现为「推送时界面卡住」。
    """
    preset = engine.api.post("/api/upreset",
                             {"name": "卡推送", "profile": {"down_kbps": 300}})
    assert preset.status == 200
    pid = preset.body["id"]

    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(8)
    stalled = []
    stop = threading.Event()

    def acceptor():
        while not stop.is_set():
            try:
                c, _ = srv.accept()
            except OSError:
                return
            stalled.append(c)

    threading.Thread(target=acceptor, daemon=True).start()
    push_port = srv.getsockname()[1]

    def push():
        try:
            engine.api.post("/api/push",
                            {"peer": {"ip": "127.0.0.1", "share": push_port},
                             "ids": [pid]}, timeout=10)
        except Exception:
            pass

    threads = [threading.Thread(target=push, daemon=True) for _ in range(4)]
    for t in threads:
        t.start()
    try:
        time.sleep(0.4)
        t0 = time.monotonic()
        r = engine.api.get("/api/status", timeout=5)
        elapsed = time.monotonic() - t0
        assert r.status == 200
        assert elapsed < 0.5, "推送进行中 /api/status 用了 %.2fs" % elapsed
    finally:
        stop.set()
        srv.close()
        for c in stalled:
            try:
                c.close()
            except OSError:
                pass
        for t in threads:
            t.join(timeout=3)


# ---- P0-4：畸形/超限请求必须被明确拒绝，而不是挂着或静默成功 ----

def _raw_http(engine, payload, timeout=5.0):
    """直接发原始字节，绕开 http.client 的规范化（这里要测的正是畸形输入）"""
    s = socket.create_connection(("127.0.0.1", engine.ctrl_port), timeout=timeout)
    try:
        s.sendall(payload)
        chunks = []
        while True:
            b = s.recv(65536)
            if not b:
                break
            chunks.append(b)
        return b"".join(chunks)
    finally:
        s.close()


def _code(raw):
    return int(raw.split(b" ", 2)[1])


BAD = [
    ("内容长度为负", b"POST /api/profile HTTP/1.1\r\nHost: 127.0.0.1\r\n"
                    b"Content-Length: -1\r\n\r\n", 400),
    ("内容长度非数字", b"POST /api/profile HTTP/1.1\r\nHost: 127.0.0.1\r\n"
                      b"Content-Length: abc\r\n\r\n", 400),
    ("内容长度超上限", b"POST /api/profile HTTP/1.1\r\nHost: 127.0.0.1\r\n"
                      b"Content-Length: 1099511627776\r\n\r\n", 413),
    ("分块编码不支持", b"POST /api/profile HTTP/1.1\r\nHost: 127.0.0.1\r\n"
                      b"Transfer-Encoding: chunked\r\n\r\n0\r\n\r\n", 400),
    ("浏览器 Origin 一律拒绝", b"GET /api/status HTTP/1.1\r\nHost: 127.0.0.1\r\n"
                             b"Origin: http://evil.example\r\n\r\n", 403),
    ("主机名非回环拒绝", b"GET /api/status HTTP/1.1\r\nHost: evil.example\r\n\r\n", 403),
]


@pytest.mark.parametrize("name,payload,want", BAD, ids=[b[0] for b in BAD])
def test_malformed_request_is_rejected(engine, name, payload, want):
    assert _code(_raw_http(engine, payload)) == want, name


def test_oversized_header_line_rejected(engine):
    pad = b"X-Pad: " + b"a" * (17 * 1024) + b"\r\n"
    raw = _raw_http(engine, b"GET /api/status HTTP/1.1\r\nHost: 127.0.0.1\r\n"
                    + pad + b"\r\n")
    assert _code(raw) == 431


def test_too_many_header_lines_rejected(engine):
    lines = b"".join(b"X-N%d: 1\r\n" % i for i in range(101))
    raw = _raw_http(engine, b"GET /api/status HTTP/1.1\r\nHost: 127.0.0.1\r\n"
                    + lines + b"\r\n")
    assert _code(raw) == 431
