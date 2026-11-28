# -*- coding: utf-8 -*-
"""经代理的 HTTP/1.1 报文定界：HEAD / 204 / 304 / 103→200 / 带体复用 / EOF 定界 / IPv6 CONNECT。

这一组用例全部盯同一件事：**代理有没有把报文边界如实转达给客户端**。
边界错了不会报错，只会让客户端挂住或把正文读歪 —— 浏览器表现是「一直转圈」，
脚本表现是「偶发超时」，都是最难归因的那类问题。

用裸 socket 而不是 http.client：需要逐字节看清代理到底发了什么（比如
HEAD 有没有状态行、1xx 之后有没有继续读最终响应）。
"""
import socket
import socketserver
import threading

import pytest

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------- 源站

class _Handler(socketserver.BaseRequestHandler):
    """极简 HTTP/1.1 源站：按路径决定响应形态，支持同连接多请求。"""

    def handle(self):
        buf = b""
        while True:
            while b"\r\n\r\n" not in buf:
                part = self.request.recv(65536)
                if not part:
                    return
                buf += part
            head, _, buf = buf.partition(b"\r\n\r\n")
            lines = head.split(b"\r\n")
            try:
                method, path, _v = lines[0].decode("latin1").split()
            except ValueError:
                return
            hdrs = {}
            for ln in lines[1:]:
                k, _, v = ln.partition(b":")
                hdrs[k.strip().lower()] = v.strip()

            n = int(hdrs.get(b"content-length", b"0") or 0)
            while len(buf) < n:
                part = self.request.recv(65536)
                if not part:
                    return
                buf += part
            body = buf[:n]
            buf = buf[n:]
            if hdrs.get(b"transfer-encoding", b"").lower().strip() == b"chunked":
                body, buf = self._read_chunked(buf)
                if body is None:
                    return

            if not self._dispatch(method, path, body, hdrs):
                return

    def _read_chunked(self, buf):
        """按 chunk 边界读出请求体（源站侧也要支持，否则测不到转发正确性）。"""
        out = b""
        while True:
            while b"\r\n" not in buf:
                part = self.request.recv(65536)
                if not part:
                    return None, buf
                buf += part
            line, _, buf = buf.partition(b"\r\n")
            try:
                sz = int(line.split(b";")[0] or b"0", 16)
            except ValueError:
                return None, buf
            if sz == 0:
                while b"\r\n" not in buf:      # trailer
                    part = self.request.recv(65536)
                    if not part:
                        return out, buf
                    buf += part
                _t, _, buf = buf.partition(b"\r\n")
                return out, buf
            while len(buf) < sz + 2:
                part = self.request.recv(65536)
                if not part:
                    return out, buf
                buf += part
            out += buf[:sz]
            buf = buf[sz + 2:]

    def _send(self, data):
        self.request.sendall(data)

    def _dispatch(self, method, path, body, hdrs=None):
        if path.startswith("/hdrs"):
            # 把源站实际收到的头原样回显，用来断言代理有没有删 CL / Expect
            text = b"".join(b"%s: %s\n" % (k, v) for k, v in (hdrs or {}).items())
            self._send(b"HTTP/1.1 200 OK\r\nContent-Length: %d\r\n\r\n" % len(text))
            self._send(text)
            return True
        if path.startswith("/te-cl"):
            # 同时给 CL 和 chunked：按 RFC 9112 §6.3，TE 优先，且中间件必须删 CL
            self._send(b"HTTP/1.1 200 OK\r\nContent-Length: 3\r\n"
                       b"Transfer-Encoding: chunked\r\n\r\n"
                       b"5\r\nhello\r\n0\r\n\r\n")
            return True
        if path.startswith("/h103"):
            # HEAD 的 103 之后仍然有最终响应：不能把 103 当成最终响应
            self._send(b"HTTP/1.1 103 Early Hints\r\nLink: </a.css>\r\n\r\n")
            self._send(b"HTTP/1.1 200 OK\r\nContent-Length: 5\r\n\r\n")
            return True
        if path.startswith("/head"):
            # HEAD：头就是全部内容，绝不能有正文
            self._send(b"HTTP/1.1 200 OK\r\nContent-Length: 5\r\n\r\n")
            return True
        if path.startswith("/204"):
            self._send(b"HTTP/1.1 204 No Content\r\n\r\n")
            return True
        if path.startswith("/304"):
            self._send(b"HTTP/1.1 304 Not Modified\r\nETag: \"x\"\r\n\r\n")
            return True
        if path.startswith("/103"):
            # 先给一个临时响应，再给最终响应（Early Hints 的真实形态）
            self._send(b"HTTP/1.1 103 Early Hints\r\nLink: </a.css>\r\n\r\n")
            self._send(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nhi")
            return True
        if path.startswith("/eof"):
            # 没有 CL / TE：正文靠连接关闭定界
            self._send(b"HTTP/1.1 200 OK\r\n\r\neof-body")
            return False
        if path.startswith("/echo"):
            self._send(b"HTTP/1.1 200 OK\r\nContent-Length: %d\r\n\r\n" % len(body))
            self._send(body)
            return True
        self._send(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok")
        return True


class _Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True
    address_family = socket.AF_INET


class _Server6(_Server):
    address_family = socket.AF_INET6


@pytest.fixture
def origin():
    srv = _Server(("127.0.0.1", 0), _Handler)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    try:
        yield srv.server_address[1]
    finally:
        srv.shutdown()
        srv.server_close()


@pytest.fixture
def origin6():
    try:
        srv = _Server6(("::1", 0), _Handler)
    except OSError:
        pytest.skip("本机不支持 IPv6 回环")
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    try:
        yield srv.server_address[1]
    finally:
        srv.shutdown()
        srv.server_close()


# ---------------------------------------------------------------- 客户端

class Reader:
    def __init__(self, sock):
        self.sock = sock
        self.buf = bytearray()

    def _fill(self):
        part = self.sock.recv(65536)
        if not part:
            return False
        self.buf.extend(part)
        return True

    def _line(self):
        while b"\r\n" not in self.buf:
            if not self._fill():
                return None
        line, _, rest = self.buf.partition(b"\r\n")
        self.buf = bytearray(rest)
        return bytes(line)

    def head(self):
        """读一个状态行 + 头。返回 (status, headers)；连接断了返回 (None, {})。"""
        first = self._line()
        if not first:
            return None, {}
        try:
            status = int(first.split(b" ")[1])
        except Exception:
            raise AssertionError("状态行不合法：%r" % first)
        headers = {}
        while True:
            line = self._line()
            if line is None:
                break
            if not line:
                break
            k, _, v = line.partition(b":")
            headers[k.strip().lower()] = v.strip()
        return status, headers

    def body(self, headers, method="GET", status=None):
        """按报文定界读正文。返回 (bytes, 是否读到 EOF)。"""
        if method == "HEAD":
            return b"", False
        if status in (204, 304):
            return b"", False
        if headers.get(b"transfer-encoding", b"").lower().startswith(b"chunked"):
            out = b""
            while True:
                line = self._line()
                if line is None:
                    return out, True
                sz = int(line.split(b";")[0] or b"0", 16)
                if sz == 0:
                    self._line()          # trailer 结束
                    return out, False
                while len(self.buf) < sz + 2:
                    if not self._fill():
                        return out, True
                out += bytes(self.buf[:sz])
                del self.buf[:sz + 2]
        if b"content-length" in headers:
            n = int(headers[b"content-length"])
            while len(self.buf) < n:
                if not self._fill():
                    return bytes(self.buf), True
            out = bytes(self.buf[:n])
            del self.buf[:n]
            return out, False
        # 无 CL / TE：读到连接关闭
        while self._fill():
            pass
        out = bytes(self.buf)
        self.buf = bytearray()
        return out, True


def _proxy_sock(engine, timeout=20.0):
    return socket.create_connection(engine.proxy, timeout=timeout)


def _abs(origin_port, path):
    return "http://127.0.0.1:%d%s" % (origin_port, path)


def _req(method, url, extra=b"", keep=True):
    conn = b"keep-alive" if keep else b"close"
    return (b"%s %s HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: %s\r\n"
            % (method.encode(), url.encode(), conn)) + extra + b"\r\n"


# ---------------------------------------------------------------- 用例

def test_head_gets_status_line(engine, origin):
    """HEAD 必须收到状态行与头（旧实现把它当「无正文」直接跳过，什么都不发）。"""
    s = _proxy_sock(engine)
    try:
        r = Reader(s)
        s.sendall(_req("HEAD", _abs(origin, "/head")))
        status, headers = r.head()
        assert status == 200, "HEAD 的响应头没发出来"
        assert headers.get(b"content-length") == b"5"
        body, _eof = r.body(headers, "HEAD")
        assert body == b""
    finally:
        s.close()


def test_204_has_status_line(engine, origin):
    s = _proxy_sock(engine)
    try:
        r = Reader(s)
        s.sendall(_req("GET", _abs(origin, "/204")))
        status, headers = r.head()
        assert status == 204
        assert r.body(headers, status=status) == (b"", False)
    finally:
        s.close()


def test_304_has_status_line(engine, origin):
    s = _proxy_sock(engine)
    try:
        r = Reader(s)
        s.sendall(_req("GET", _abs(origin, "/304")))
        status, headers = r.head()
        assert status == 304
        assert headers.get(b"etag") == b'"x"'
        assert r.body(headers, status=status) == (b"", False)
    finally:
        s.close()


def test_103_then_final_response(engine, origin):
    """1xx 之后必须继续读最终响应，否则客户端永远停在「等响应」上。"""
    s = _proxy_sock(engine)
    try:
        r = Reader(s)
        s.sendall(_req("GET", _abs(origin, "/103")))
        status, _h = r.head()
        assert status == 103, "临时响应没有转给客户端"
        status2, headers2 = r.head()
        assert status2 == 200, "1xx 之后没有继续读最终响应"
        assert r.body(headers2)[0] == b"hi"
    finally:
        s.close()


def test_post_body_on_reused_connection(engine, origin):
    """同一条连接上先 GET 再 POST：正文必须完整转发。

    旧实现在复用路径上只写请求头，正文留在客户端缓冲里，随后被当成
    「下一个请求」解析 —— 整条连接从此错位。
    """
    s = _proxy_sock(engine)
    try:
        r = Reader(s)
        s.sendall(_req("GET", _abs(origin, "/keep")))
        status, headers = r.head()
        assert status == 200 and r.body(headers)[0] == b"ok"

        payload = b"a" * 4096
        s.sendall(_req("POST", _abs(origin, "/echo"),
                       b"Content-Length: %d\r\n" % len(payload)) + payload)
        status2, headers2 = r.head()
        assert status2 == 200
        assert r.body(headers2)[0] == payload, "POST 正文被截断或错位"
    finally:
        s.close()


def test_chunked_post_body_on_reused_connection(engine, origin):
    """分块请求体同样要按 chunk 边界转发。"""
    s = _proxy_sock(engine)
    try:
        r = Reader(s)
        s.sendall(_req("GET", _abs(origin, "/keep")))
        r.head()

        s.sendall(_req("POST", _abs(origin, "/echo"), b"Transfer-Encoding: chunked\r\n")
                  + b"5\r\nhello\r\n6\r\n world\r\n0\r\n\r\n")
        status, headers = r.head()
        assert status == 200
        assert r.body(headers)[0] == b"hello world"
    finally:
        s.close()


def test_eof_framed_response_closes_client(engine, origin):
    """没有长度边界的响应：正文发完后必须关客户端连接。

    否则客户端只能一直等 EOF 才知道正文结束 —— 浏览器表现就是「一直转圈」。
    """
    s = _proxy_sock(engine)
    try:
        r = Reader(s)
        s.sendall(_req("GET", _abs(origin, "/eof")))
        status, headers = r.head()
        assert status == 200
        assert headers.get(b"connection", b"").lower() == b"close", \
            "EOF 定界的响应必须显式告诉客户端要关连接"
        body, saw_eof = r.body(headers)
        assert body == b"eof-body"
        assert saw_eof, "正文之后客户端没有收到连接关闭"
    finally:
        s.close()


def test_connect_bracket_ipv6(engine, origin6):
    """CONNECT [::1]:port 必须能解析（旧实现按第一个冒号切分，直接失败）。"""
    s = _proxy_sock(engine)
    try:
        s.sendall(("CONNECT [::1]:%d HTTP/1.1\r\nHost: [::1]\r\n\r\n" % origin6)
                  .encode("latin1"))
        r = Reader(s)
        status, _h = r.head()
        assert status == 200, "方括号 IPv6 的 CONNECT 没被接受"
        s.sendall(b"GET /keep HTTP/1.1\r\nHost: [::1]\r\nConnection: close\r\n\r\n")
        status2, headers2 = r.head()
        assert status2 == 200
        assert r.body(headers2)[0] == b"ok"
    finally:
        s.close()


def test_second_request_cannot_reach_control_port(engine, origin):
    """同一条连接上的第二个请求也要过目标限制，不能绕过控制口保护。"""
    s = _proxy_sock(engine)
    try:
        r = Reader(s)
        s.sendall(_req("GET", _abs(origin, "/keep")))
        status, headers = r.head()
        assert status == 200
        r.body(headers)
        # 换成控制口：必须被 403 挡下
        s.sendall(_req("GET", "http://127.0.0.1:%d/api/status" % engine.ctrl_port))
        status2, _h2 = r.head()
        assert status2 == 403, "第二个请求绕过了目标限制"
    finally:
        s.close()


# ---------------------------------------------------------------- RFC 9112 §6.3 定界

def test_chunked_request_with_content_length_drops_cl(engine, origin):
    """TE 与 CL 并存：必须按 chunked 读、删掉 CL 转发、响应后关连接。

    RFC 9112 §6.3 第 3 步：TE 覆盖 CL，中间件 MUST 先移除 CL 再转发；
    §6.1：处理过这种请求后 MUST 关闭连接，避免残留字节被当成下一条请求。
    """
    s = _proxy_sock(engine)
    try:
        r = Reader(s)
        s.sendall(_req("POST", _abs(origin, "/echo"),
                       b"Transfer-Encoding: chunked\r\nContent-Length: 99\r\n")
                  + b"5\r\nhello\r\n0\r\n\r\n")
        status, headers = r.head()
        assert status == 200
        assert headers.get(b"connection", b"").lower() == b"close", \
            "TE+CL 并存的请求处理完后必须关闭连接"
        assert r.body(headers)[0] == b"hello", "正文被 CL 干扰，读错边界"
    finally:
        s.close()


def test_response_with_te_and_cl_drops_cl(engine, origin):
    """响应同时带 TE 和 CL：转发给客户端前必须删掉 CL。

    留着 CL，客户端会按 CL 读完就停，chunked 的结尾留在连接里，
    下一条响应必然错位（旧实现原样转发）。
    """
    s = _proxy_sock(engine)
    try:
        r = Reader(s)
        s.sendall(_req("GET", _abs(origin, "/te-cl")))
        status, headers = r.head()
        assert status == 200
        assert b"content-length" not in headers, "响应 TE+CL 并存时 CL 没有被删掉"
        assert r.body(headers)[0] == b"hello"
    finally:
        s.close()


def test_expect_100_continue_answered_by_proxy(engine, origin):
    """Expect: 100-continue 必须由代理应答，客户端不能靠自己的超时。

    RFC 9110 §10.1.1：收到 100-continue 的服务器 MUST 回 100 或最终状态。
    旧实现把 Expect 转给上游后立刻阻塞读正文 —— 客户端要等 curl 默认的 1s
    超时才发体，每个大 POST 白亏一秒，且这段时间被算进「网络延迟」。
    """
    s = _proxy_sock(engine, timeout=8.0)
    try:
        r = Reader(s)
        s.sendall(b"POST " + _abs(origin, "/hdrs").encode() + b" HTTP/1.1\r\n"
                  b"Host: 127.0.0.1\r\nExpect: 100-continue\r\n"
                  b"Content-Length: 5\r\n\r\n")
        status, _h = r.head()
        assert status == 100, "代理没有应答 100-continue"
        s.sendall(b"hello")
        status2, headers2 = r.head()
        assert status2 == 200
        body, _eof = r.body(headers2)
        assert b"expect:" not in body.lower(), "Expect 不应继续转给上游"
    finally:
        s.close()


def test_head_with_early_hints_keeps_final_response(engine, origin):
    """对 HEAD 的 103 仍是过渡响应，后面还有最终响应。

    旧实现先判 method == HEAD 再判 1xx，把 103 当成最终响应发完就等下一条
    请求，真正的 200 留在上游缓冲里 —— 两边状态机从此错位。
    """
    s = _proxy_sock(engine)
    try:
        r = Reader(s)
        s.sendall(_req("HEAD", _abs(origin, "/h103")))
        status, _h = r.head()
        assert status == 103, "HEAD 的临时响应没有转给客户端"
        status2, headers2 = r.head()
        assert status2 == 200, "HEAD 的 103 之后没有继续读最终响应"
        assert headers2.get(b"content-length") == b"5"
    finally:
        s.close()


def test_invalid_content_length_gets_400(engine, origin):
    """CL 不是十进制数：必须 400 并关连接，不能当成 0 继续复用。"""
    s = _proxy_sock(engine)
    try:
        r = Reader(s)
        s.sendall(_req("POST", _abs(origin, "/echo"), b"Content-Length: abc\r\n") + b"x")
        status, _h = r.head()
        assert status == 400
    finally:
        s.close()


def test_transfer_encoding_not_ending_with_chunked_gets_400(engine, origin):
    """请求的 TE 不以 chunked 结尾时长度无法确定，RFC 9112 §6.3 要求 400。"""
    s = _proxy_sock(engine)
    try:
        r = Reader(s)
        s.sendall(_req("POST", _abs(origin, "/echo"), b"Transfer-Encoding: gzip\r\n"))
        status, _h = r.head()
        assert status == 400
    finally:
        s.close()
