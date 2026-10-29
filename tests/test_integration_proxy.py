# -*- coding: utf-8 -*-
"""经代理的真实链路用例：直连 / keep-alive / 半关闭 / 限速 / 延迟 / 停止即放行。

全部走 127.0.0.1 上的真实 socket，不用 mock：proxy 的管道、令牌桶、
DelayLine、FIN 转发是同一个整体，拆开测会漏掉它们之间的衔接问题。
"""
import socket
import threading
import time

import pytest

from support import HttpReader, connect_via_proxy

pytestmark = pytest.mark.integration


def request_head(origin, path, keep_alive=True):
    conn = "keep-alive" if keep_alive else "close"
    return ("GET %s HTTP/1.1\r\nHost: %s:%d\r\nConnection: %s\r\n\r\n"
            % (path, origin.host, origin.port, conn)).encode("latin1")


def fetch(engine, origin, path, timeout=30.0, half_close=False):
    """经 CONNECT 隧道取一次响应，返回 (status, headers, body, elapsed)。"""
    sock, pre = connect_via_proxy(engine.proxy, origin.host, origin.port,
                                  timeout=timeout)
    try:
        reader = HttpReader(sock, pre)
        t0 = time.monotonic()
        sock.sendall(request_head(origin, path))
        if half_close:
            sock.shutdown(socket.SHUT_WR)
        status, headers, body = reader.response()
        return status, headers, body, time.monotonic() - t0
    finally:
        sock.close()


def test_direct_pass_through(engine, http_origin):
    status, _h, body, elapsed = fetch(engine, http_origin, "/small")
    assert status == 200
    assert body == b"weaknet-origin-small"
    assert elapsed < 1.5, "直连档也要 %.2fs，管道里有人在睡" % elapsed


def test_direct_download_integrity(engine, http_origin):
    status, _h, body, _el = fetch(engine, http_origin, "/big?kb=256")
    assert status == 200
    assert body == b"x" * (256 * 1024)


def test_keep_alive_serves_two_requests(engine, http_origin):
    sock, pre = connect_via_proxy(engine.proxy, http_origin.host, http_origin.port)
    try:
        reader = HttpReader(sock, pre)
        for _ in range(2):
            sock.sendall(request_head(http_origin, "/small"))
            status, _h, body = reader.response()
            assert status == 200
            assert body == b"weaknet-origin-small"
    finally:
        sock.close()


def test_half_close_does_not_truncate_response(engine, http_origin):
    """客户端发完请求就半关闭：响应仍必须完整回来（旧实现会整条砍掉）。"""
    status, _h, body, _el = fetch(engine, http_origin, "/big?kb=64", half_close=True)
    assert status == 200
    assert body == b"x" * (64 * 1024)


def test_plain_http_proxy_request_without_connect(engine, http_origin):
    """浏览器式代理请求：GET http://host:port/path 直接发给代理端口。"""
    sock = socket.create_connection(engine.proxy, timeout=15)
    try:
        reader = HttpReader(sock)
        req = ("GET http://%s:%d/small HTTP/1.1\r\nHost: %s:%d\r\n"
               "Connection: close\r\n\r\n"
               % (http_origin.host, http_origin.port,
                  http_origin.host, http_origin.port))
        sock.sendall(req.encode("latin1"))
        status, _h, body = reader.response()
        assert status == 200
        assert body == b"weaknet-origin-small"
    finally:
        sock.close()


def test_throttle_slows_download(engine, http_origin):
    engine.apply(down_kbps=4000)                    # 500 KB/s
    status, _h, body, elapsed = fetch(engine, http_origin, "/big?kb=512")
    assert status == 200
    assert len(body) == 512 * 1024
    assert elapsed >= 0.7, "512KB@4000kbps 只用了 %.2fs（限速没生效）" % elapsed
    assert elapsed <= 3.0, "512KB@4000kbps 用了 %.2fs（远慢于设定）" % elapsed


def test_latency_applies_to_each_request(engine, http_origin):
    engine.apply(latency_up_ms=250, latency_down_ms=250)
    _s, _h, _b, first = fetch(engine, http_origin, "/small")
    assert first >= 0.4, "往返延迟只测到 %.2fs（应 ≥ 上行+下行）" % first

    assert engine.api.get("/api/stop").status == 200
    _s, _h, _b, second = fetch(engine, http_origin, "/small")
    assert second < 0.6, "停止后延迟仍在：%.2fs" % second


def test_global_bucket_caps_concurrent_downloads(engine, http_origin):
    engine.apply(down_kbps=4000)
    results = []

    def one():
        results.append(fetch(engine, http_origin, "/big?kb=256"))

    threads = [threading.Thread(target=one, daemon=True) for _ in range(2)]
    t0 = time.monotonic()
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)
    elapsed = time.monotonic() - t0
    assert len(results) == 2
    assert all(r[0] == 200 and len(r[2]) == 256 * 1024 for r in results)
    # 512KB 合计 @4000kbps 理论 1.0s；若每条连接各拿一份配额只要 ~0.5s
    assert elapsed >= 0.75, "两条并发合计 512KB 只用 %.2fs，配额被按连接分了" % elapsed


def test_stop_releases_inflight_download(engine, http_origin):
    engine.apply(down_kbps=400)
    done = {}
    state = {}

    def download():
        try:
            state["t0"] = time.monotonic()
            status, _h, body, _el = fetch(engine, http_origin, "/big?kb=2048",
                                          timeout=120)
            state["status"] = status
            state["len"] = len(body)
        except Exception as e:
            state["error"] = repr(e)
        finally:
            state["done_at"] = time.monotonic()
            done["ok"] = True

    th = threading.Thread(target=download, daemon=True)
    th.start()
    try:
        time.sleep(0.8)                         # 让它跑进慢速稳态
        assert not done, "2MB@400kbps 不可能 0.8s 下完"
        t_stop = time.monotonic()
        assert engine.api.get("/api/stop").status == 200
        th.join(timeout=6)
        assert done.get("ok"), "停止后下载仍未结束（积压被卡住）"
        released = state["done_at"] - t_stop
        assert "error" not in state, state["error"]
        assert state["status"] == 200
        assert state["len"] == 2048 * 1024, "停止后剩余数据没放行完整"
        assert released < 3.0, "停止后 %.2fs 才放行积压" % released
    finally:
        if th.is_alive():
            engine.api.get("/api/stop")
            th.join(timeout=5)
