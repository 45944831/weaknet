# -*- coding: utf-8 -*-
"""GitHub 新版本提示：版本比较、网络异常静默、红色标签状态、GUI 接线。

起一个本机 HTTP 假服务器当 GitHub API，测的是真实的 urllib 路径（含
ProxyHandler 直连兜底），不 mock 掉网络层；断网用例则指向一个没人监听的端口。
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

import update_check as UC
import weaknet as W

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parent.parent
GUI = (ROOT / "weaknet_gui.py").read_text(encoding="utf-8")


# ---------------------------------------------------------------- 版本比较

@pytest.mark.parametrize("raw, want", [
    ("v1.1.13", (1, 1, 13)), ("1.1.13", (1, 1, 13)),
    ("V2.0", (2, 0)), ("v1.2.3-rc1", (1, 2, 3)),
    ("", ()), ("garbage", ()), (None, ()),
])
def test_parse_version(raw, want):
    assert UC.parse_version(raw) == want


@pytest.mark.parametrize("latest, current, want", [
    ("v1.1.14", "1.1.13", True),
    ("v2.0", "1.9.9", True),
    ("v1.1.13", "1.1.13", False),
    ("v1.1.12", "1.1.13", False),
    ("v1.2", "1.2.0", False),          # 缺位按 0 补齐，不能误报
    ("nonsense", "1.1.13", False),
    ("v1.1.13", "nonsense", False),
])
def test_is_newer(latest, current, want):
    assert UC.is_newer(latest, current) is want


# ---------------------------------------------------------------- 假 GitHub API

class _Handler(BaseHTTPRequestHandler):
    payload = {}
    status = 200

    def do_GET(self):
        body = json.dumps(self.payload).encode()
        self.send_response(self.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@pytest.fixture
def fake_api():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield srv
    srv.shutdown()
    srv.server_close()
    t.join(2)


def _url(srv):
    return "http://127.0.0.1:%d/repos/x/y/releases/latest" % srv.server_address[1]


def test_check_reports_new_version_and_page(fake_api):
    _Handler.payload = {"tag_name": "v9.9.9", "name": "WeakNet v9.9.9",
                        "html_url": "https://github.com/45944831/weaknet/releases/tag/v9.9.9"}
    r = UC.check(W.VERSION, timeout=3, url=_url(fake_api))
    assert r["has_new"] is True
    assert r["latest"] == "v9.9.9"
    assert r["page"].endswith("/releases/tag/v9.9.9")


def test_check_same_version_is_not_new(fake_api):
    _Handler.payload = {"tag_name": "v" + W.VERSION, "html_url": UC.PAGE_LATEST}
    r = UC.check(W.VERSION, timeout=3, url=_url(fake_api))
    assert r["has_new"] is False


def test_check_ignores_draft_and_prerelease(fake_api):
    _Handler.payload = {"tag_name": "v99.0.0", "prerelease": True, "draft": False}
    assert UC.check(W.VERSION, timeout=3, url=_url(fake_api))["has_new"] is False
    _Handler.payload = {"tag_name": "v99.0.0", "draft": True}
    assert UC.check(W.VERSION, timeout=3, url=_url(fake_api))["has_new"] is False


def test_check_is_silent_on_network_error():
    # 端口 1 基本没人监听；任何异常都必须被吞掉，不能让 GUI 崩
    assert UC.check(W.VERSION, timeout=1, url="http://127.0.0.1:1/latest") is None


def test_check_is_silent_on_bad_payload(fake_api):
    _Handler.payload = {"message": "Bad credentials"}
    assert UC.check(W.VERSION, timeout=3, url=_url(fake_api)) is None


def test_env_override(monkeypatch):
    monkeypatch.setenv("WEAKNET_UPDATE_API", "http://127.0.0.1:12345/latest")
    assert UC.api_url() == "http://127.0.0.1:12345/latest"
    monkeypatch.delenv("WEAKNET_UPDATE_API")
    assert UC.api_url() == UC.API_LATEST


# ---------------------------------------------------------------- 标签状态

def test_label_state_red_when_new():
    text, fg, bold = UC.label_state("1.1.13",
                                    {"has_new": True, "latest": "v1.1.14"},
                                    "#6b7280", "#c0392b")
    assert "1.1.14" in text and "1.1.13" in text
    assert fg == "#c0392b" and bold is True


def test_label_state_muted_when_current_or_failed():
    for state in (None, {"has_new": False, "latest": "v1.1.13"}):
        text, fg, bold = UC.label_state("1.1.13", state, "#6b7280", "#c0392b")
        assert text == "v1.1.13" and fg == "#6b7280" and bold is False


def test_apply_update_state_really_changes_tk_label():
    """把结果真的落到 tkinter 控件上，确认红色不是只停在返回值里。"""
    tk = pytest.importorskip("tkinter")
    from weaknet_gui import App, ERR, MUTED
    root = tk.Tk()
    root.geometry("+3000+3000")
    try:
        lab = tk.Label(root, text="v1.1.13", fg=MUTED)
        app = object.__new__(App)          # 不跑完整 __init__（需要引擎与端口）
        app.lab_version = lab
        app._update_state = {"has_new": True, "latest": "v1.1.14", "page": UC.PAGE_LATEST}
        app._apply_update_state()
        assert str(lab.cget("fg")) == str(ERR)
        assert "1.1.14" in str(lab.cget("text"))
        app._update_state = {"has_new": False, "latest": "v1.1.14"}
        app._apply_update_state()
        assert str(lab.cget("fg")) == str(MUTED)
    finally:
        root.destroy()


# ---------------------------------------------------------------- GUI 接线

def test_gui_wires_version_label_and_click():
    assert "import update_check as UC" in GUI
    assert "self.lab_version = tk.Label(head" in GUI
    assert 'self.lab_version.bind("<Button-1>"' in GUI
    assert "webbrowser.open(url)" in GUI
    assert "UC.PAGE_LATEST" in GUI
    assert "self._update_pump()" in GUI        # 结果必须回主线程
    assert "_queue_update_check(force=True)" in GUI
