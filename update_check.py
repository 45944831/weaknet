# -*- coding: utf-8 -*-
"""GitHub 新版本检查（Windows GUI 用）。

设计约束：
- 只做一件事：拿线上最新 Release 的 tag，跟本地 VERSION 比大小。
- 绝不阻塞界面：调用方必须放在后台线程（GUI 里走 update_q + 主线程泵）。
- 绝不打扰用户：断网 / API 限流 / 解析失败一律返回 None，界面保持默认灰色。
- 直连优先：WeakNet 自己会把系统代理指向本地 8888，urllib 默认读系统代理时
  请求会打回自己的代理端口；所以先用 ProxyHandler({}) 直连，失败再退回系统代理。

可用环境变量 WEAKNET_UPDATE_API 覆盖 API 地址，供本地测试起假服务器用。
"""

import json
import os
import re
import urllib.request

OWNER = "45944831"
REPO = "weaknet"
API_LATEST = "https://api.github.com/repos/%s/%s/releases/latest" % (OWNER, REPO)
PAGE_LATEST = "https://github.com/%s/%s/releases/latest" % (OWNER, REPO)
TIMEOUT = 6.0


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
        "page": d.get("html_url") or PAGE_LATEST,
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
