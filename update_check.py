# -*- coding: utf-8 -*-
"""GitHub 新版本检查（Windows GUI 用）。

设计约束：
- 只做一件事：拿线上最新 Release 的 tag，跟本地 VERSION 比大小。
- 绝不阻塞界面：调用方必须放在后台线程（GUI 里走 update_q + 主线程泵）。
- 绝不打扰用户：断网 / API 限流 / 解析失败一律返回 None，界面保持默认灰色。
- 直连优先：WeakNet 自己会把系统代理指向本地 8888，urllib 默认读系统代理时
  请求会打回自己的代理端口；所以先用 ProxyHandler({}) 直连，失败再退回系统代理。
- 跳转地址不可信：API 响应的 html_url 会被 GUI 直接交给 webbrowser.open，
  必须先过域名白名单，否则假 API / 被污染的响应能把用户导到钓鱼站。

可用环境变量 WEAKNET_UPDATE_API 覆盖 API 地址，供本地测试起假服务器用。
"""

import json
import os
import re
import urllib.request
from urllib.parse import urlparse

OWNER = "45944831"
REPO = "weaknet"
API_LATEST = "https://api.github.com/repos/%s/%s/releases/latest" % (OWNER, REPO)
PAGE_LATEST = "https://github.com/%s/%s/releases/latest" % (OWNER, REPO)
# 只有这两个官方域名下的 https 链接才允许作为「点击下载」的跳转目标。
ALLOWED_PAGE_HOSTS = ("github.com", "www.github.com")
TIMEOUT = 6.0


def safe_page_url(url):
    """校验 API 给出的跳转链接，非法时回退到硬编码的发布页 PAGE_LATEST。

    校验 hostname 用精确匹配而不是 endswith：后者会把 evilgithub.com、
    github.com.attacker.example 这类域名误放行。scheme 必须是 https，端口
    只允许 443，避免把用户带到非官方服务上。
    还要拒绝 userinfo（`https://user@github.com/a`）：Android 端
    SafePageUrl.java 明确拒绝带用户名/密码的 URL，两端必须同口径；
    空主机名、0 或越界端口同样拒绝（畸形端口解析异常由 try 兜住）。
    """
    try:
        p = urlparse(str(url or ""))
        host = (p.hostname or "").lower().rstrip(".")
        port = p.port
        user = p.username
        pwd = p.password
    except (TypeError, ValueError):
        return PAGE_LATEST
    if user or pwd:
        return PAGE_LATEST
    if not host:
        return PAGE_LATEST
    if port is not None and not (0 < port <= 65535):
        return PAGE_LATEST
    if p.scheme == "https" and host in ALLOWED_PAGE_HOSTS and port in (None, 443):
        return url
    return PAGE_LATEST


def api_url():
    return os.environ.get("WEAKNET_UPDATE_API") or API_LATEST


def parse_version(s):
    """'v1.1.13' / '1.1.14' / 'v2.0-rc1' -> (1, 1, 13)；非法输入返回 ()。"""
    m = re.match(r"^[vV]?(\d+(?:\.\d+)*)", str(s or "").strip())
    if not m:
        return ()
    try:
        return tuple(int(x) for x in m.group(1).split("."))
    except (ValueError, OverflowError):
        return ()


def is_newer(latest, current):
    """线上 tag 是否严格大于本地版本；缺位按 0 补齐（1.2 == 1.2.0）。"""
    a, b = parse_version(latest), parse_version(current)
    if not a or not b:
        return False
    n = max(len(a), len(b))
    return a + (0,) * (n - len(a)) > b + (0,) * (n - len(b))


def _open(req, timeout):
    """先直连（绕开可能被本工具改写的系统代理），不行再走系统代理。"""
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        return opener.open(req, timeout=timeout)
    except Exception:
        return urllib.request.urlopen(req, timeout=timeout)


def fetch_latest(timeout=TIMEOUT, url=None):
    """返回 {'tag','page','name','draft','prerelease'}；失败抛异常，由 check 兜住。"""
    req = urllib.request.Request(
        url or api_url(),
        headers={"User-Agent": "WeakNet-update-check",
                 "Accept": "application/vnd.github+json"})
    with _open(req, timeout) as r:
        d = json.loads(r.read(1 << 20).decode("utf-8", "replace"))
    if not isinstance(d, dict) or not d.get("tag_name"):
        raise ValueError("bad release payload")
    return {
        "tag": d.get("tag_name") or "",
        "page": safe_page_url(d.get("html_url")),
        "name": d.get("name") or d.get("tag_name") or "",
        "draft": bool(d.get("draft")),
        "prerelease": bool(d.get("prerelease")),
    }


def check(current, timeout=TIMEOUT, url=None):
    """返回 {'has_new','latest','page'}；任何异常返回 None（调用方静默）。"""
    try:
        d = fetch_latest(timeout, url)
        # 草稿 / 预发布不算正式新版本，别拿它去提示用户升级
        if d["draft"] or d["prerelease"]:
            return {"has_new": False, "latest": d["tag"], "page": PAGE_LATEST}
        return {"has_new": is_newer(d["tag"], current),
                "latest": d["tag"], "page": d["page"] or PAGE_LATEST}
    except Exception:
        return None


def label_state(current, state, muted, err):
    """纯函数：算出版本号标签该显示的文字与颜色，便于单测，不依赖 tkinter。

    返回 (text, fg, bold)。state 为 None（检查失败）时保持默认灰色。
    """
    if state and state.get("has_new"):
        return ("v%s · 发现新版本 %s（点击下载）" % (current, state.get("latest") or ""),
                err, True)
    return ("v%s" % current, muted, False)
