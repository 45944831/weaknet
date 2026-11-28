# -*- coding: utf-8 -*-
"""RFC 9112 §6.3 定界规则的单点回归。

这一组不跑网络，只钉住三件容易「看起来没事、实际错位」的判定：

  * Transfer-Encoding 只有 **最后一个** 编码是 chunked 时才按块读；
  * 1xx 优先于 HEAD/204/304 —— 对 HEAD 的 103 仍是过渡响应；
  * Connection / TE 是 `#token`，不能拿子串当判定。
"""
import weaknet as W


# ---------------------------------------------------------------- 响应定界

def test_te_chunked_not_last_is_eof():
    """`chunked, gzip`：chunked 不是最后一个编码 → 只能读到连接关闭。"""
    mode, length = W._resp_frame(200, [b"Transfer-Encoding: chunked, gzip\r\n"], "GET")
    assert (mode, length) == ("eof", 0)


def test_te_chunked_last_is_chunked():
    mode, _n = W._resp_frame(200, [b"Transfer-Encoding: gzip, chunked\r\n"], "GET")
    assert mode == "chunked"


def test_te_present_without_chunked_ignores_content_length():
    """有 TE 但没有 chunked：按 §6.3 第 4 步走 EOF，不能退回 CL。"""
    mode, _n = W._resp_frame(
        200, [b"Transfer-Encoding: gzip\r\n", b"Content-Length: 120\r\n"], "GET")
    assert mode == "eof", "有 TE 时 CL 必须被忽略"


def test_head_103_is_still_interim():
    """对 HEAD 的 103 是过渡响应，后面还有最终响应。"""
    mode, _n = W._resp_frame(103, [b"Link: </a.css>\r\n"], "HEAD")
    assert mode == "interim"


def test_100_continue_is_interim():
    """100 Continue 也是过渡响应，后面还有最终响应。

    变异测试把 `100 <= status` 改成 `100 < status` / `101 <= status` 都没被发现：
    现有用例只测了 103。100 是 `Expect: 100-continue` 的标准应答，判错会让
    代理把 100 当成最终响应，客户端等正文、代理等下一个请求。
    """
    mode, _n = W._resp_frame(100, [], "GET")
    assert mode == "interim"


def test_chunked_with_parameters_is_chunked():
    """`Transfer-Encoding: chunked;q=1` 仍是 chunked，参数不能当编码名。

    变异测试把 `split(b";", 1)[0]` 的 `[0]` 改成 `[1]` 后活了下来：现有用例
    只测了不带参数的 chunked。带参数的 TE 在真实网关里很常见。
    """
    mode, _n = W._resp_frame(200, [b"Transfer-Encoding: chunked;q=1\r\n"], "GET")
    assert mode == "chunked"


def test_eof_and_interim_are_not_reusable():
    """EOF 定界 / 1xx 之后上游连接不能复用。

    变异测试把这里的 `return False` 改成 `True` 后活了下来。判错会让代理在
    「读到 EOF 才结束」的响应之后继续往同一条连接发请求 —— 对端已经关了。
    """
    assert W._resp_keepalive(b"HTTP/1.1 200 OK\r\n", [], "eof") is False
    assert W._resp_keepalive(b"HTTP/1.1 103 Early Hints\r\n", [], "interim") is False


def test_non_cl_modes_report_zero_length():
    """interim / nobody / eof 的长度字段固定为 0，避免调用方误用。

    变异测试把这三处 `return ..., 0` 的 0 改成 1 后全部存活：调用方目前只在
    `cl` 模式下读长度，所以线上没有症状，但契约一旦被误读就会少读/多读正文。
    """
    assert W._resp_frame(103, [], "GET") == ("interim", 0)
    assert W._resp_frame(200, [], "HEAD") == ("nobody", 0)
    assert W._resp_frame(200, [], "GET") == ("eof", 0)


def test_head_200_is_nobody():
    mode, _n = W._resp_frame(200, [b"Content-Length: 5\r\n"], "HEAD")
    assert mode == "nobody"


def test_204_and_304_are_nobody():
    assert W._resp_frame(204, [], "GET")[0] == "nobody"
    assert W._resp_frame(304, [b"Content-Length: 9\r\n"], "GET")[0] == "nobody"


def test_205_is_not_special_cased():
    """205 的零长度由报文自己声明（CL:0 / 空 chunk），不靠特判。"""
    assert W._resp_frame(205, [b"Content-Length: 0\r\n"], "GET") == ("cl", 0)


# ---------------------------------------------------------------- 请求定界

def test_request_te_must_end_with_chunked():
    assert W._req_framing_error([b"Transfer-Encoding: gzip\r\n"]) is not None
    assert W._req_framing_error(
        [b"Transfer-Encoding: gzip\r\n", b"Transfer-Encoding: chunked\r\n"]) is None


def test_request_te_plus_cl_is_allowed():
    """TE+CL 不算错：TE 优先，调用方负责删 CL 并关连接。"""
    assert W._req_framing_error(
        [b"Transfer-Encoding: chunked\r\n", b"Content-Length: 9\r\n"]) is None


def test_request_content_length_validation():
    assert W._req_framing_error([b"Content-Length: 0\r\n"]) is None
    assert W._req_framing_error([b"Content-Length: 5, 5\r\n"]) is None
    assert W._req_framing_error([b"Content-Length: 5, 6\r\n"]) is not None
    assert W._req_framing_error(
        [b"Content-Length: 5\r\n", b"Content-Length: 6\r\n"]) is not None
    assert W._req_framing_error([b"Content-Length: -1\r\n"]) is not None
    assert W._req_framing_error([b"Content-Length: abc\r\n"]) is not None


# ---------------------------------------------------------------- token 判定

def test_connection_token_is_not_substring():
    """`x-close` 不是 `close` 选项，不能因此把连接判成短连接。"""
    assert W._client_keepalive([b"Connection: x-close\r\n"]) is True
    assert W._client_keepalive([b"Connection: keep-alive, close\r\n"]) is False


def test_http10_keepalive_requires_explicit_token():
    assert W._client_keepalive([], "HTTP/1.0") is False
    assert W._client_keepalive([b"Connection: keep-alive\r\n"], "HTTP/1.0") is True


def test_down_head_drops_content_length():
    out = W._down_head([b"HTTP/1.1 200 OK\r\n",
                        b"Content-Length: 3\r\n",
                        b"Transfer-Encoding: chunked\r\n",
                        b"\r\n"],
                       drop_content_length=True)
    assert b"content-length" not in out.lower()
    assert out.startswith(b"HTTP/1.1 200 OK\r\n")
    assert out.endswith(b"\r\n\r\n")


# ------------------------------------------------- 复用判定（变异测试补）
#
# `_resp_keepalive` 在本轮变异测试里 6 个变异体全部存活：原有集成用例只覆盖
# 「正常 200 + CL」这一条路径，HTTP/1.0、Connection: close、1xx/EOF 都没单测。
# 这里把四种组合钉死；判错的后果是代理往一条已关闭/不该复用的连接发下一个请求。

def test_resp_keepalive_matrix():
    ok = b"HTTP/1.1 200 OK\r\n"
    assert W._resp_keepalive(ok, [], "cl") is True
    assert W._resp_keepalive(ok, [b"Connection: close\r\n"], "cl") is False
    assert W._resp_keepalive(b"HTTP/1.0 200 OK\r\n", [], "cl") is False
    assert W._resp_keepalive(
        b"HTTP/1.0 200 OK\r\n", [b"Connection: keep-alive\r\n"], "cl") is True
    assert W._resp_keepalive(ok, [b"Connection: x-close\r\n"], "cl") is True


def test_down_head_default_keeps_headers_untouched():
    """两个开关都关时原样返回：默认值被改成 True 会静默改写所有响应头。"""
    head = [b"HTTP/1.1 200 OK\r\n", b"Content-Length: 5\r\n",
            b"Connection: keep-alive\r\n", b"\r\n"]
    assert W._down_head(head) == b"".join(head)


def test_down_head_force_close_keeps_end_to_end_headers():
    out = W._down_head([b"HTTP/1.1 200 OK\r\n",
                        b"X-Keep: 1\r\n",
                        b"Proxy-Connection: keep-alive\r\n",
                        b"Connection: keep-alive\r\n",
                        b"Content-Length: 5\r\n",
                        b"\r\n"],
                       force_close=True)
    assert b"X-Keep: 1" in out, "端到端头不能一起被删"
    assert b"Proxy-Connection" not in out
    assert b"Content-Length: 5" in out
    assert out.count(b"Connection: close") == 1


# ------------------------------------------------- 请求改写（变异测试补）
#
# `_rewrite_origin_request` 此前没有直接单测，只有集成用例间接经过。
# Upgrade 丢失 = WebSocket 握手失败；默认 keep-alive 丢失 = 每条请求都重连。

def test_rewrite_origin_keeps_upgrade_and_drops_proxy_headers():
    out = W._rewrite_origin_request(
        "GET", "/ws",
        [b"Upgrade: websocket\r\n", b"Connection: Upgrade\r\n",
         b"Proxy-Connection: keep-alive\r\n", b"Keep-Alive: timeout=5\r\n",
         b"Host: example.com\r\n"])
    low = out.lower()
    assert b"connection: upgrade" in low
    assert b"proxy-connection" not in low
    assert b"keep-alive:" not in low
    assert b"host: example.com" in low


def test_rewrite_origin_defaults_to_keepalive():
    out = W._rewrite_origin_request("GET", "/", [])
    assert b"Connection: keep-alive" in out


def test_rewrite_origin_drops_cl_when_te_present():
    out = W._rewrite_origin_request(
        "POST", "/", [b"Transfer-Encoding: chunked\r\n",
                      b"Content-Length: 9\r\n",
                      b"Expect: 100-continue\r\n"])
    low = out.lower()
    assert b"content-length" not in low
    assert b"expect" not in low
    assert b"transfer-encoding: chunked" in low
