# -*- coding: utf-8 -*-
"""真机契约用例（默认不跑：pytest -m device）。

前置：
    set ANDROID_SERIAL=<设备序列号>      # adb devices 里那个
    pytest -m device

夹具会自己 adb forward 一个随机本地端口到手机的 8899，用完撤掉；
不会安装/卸载/清数据 —— 只读接口，不改手机上的任何状态。
"""
import os
import shutil
import socket
import subprocess

import pytest

import weaknet as W
from support import CtrlClient

pytestmark = pytest.mark.device


def free_port():
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
    finally:
        s.close()


@pytest.fixture(scope="module")
def android():
    serial = os.environ.get("ANDROID_SERIAL")
    if not serial:
        pytest.skip("未设置 ANDROID_SERIAL")
    adb = shutil.which("adb")
    if not adb:
        pytest.skip("找不到 adb")
    port = free_port()
    r = subprocess.run([adb, "-s", serial, "forward",
                        "tcp:%d" % port, "tcp:8899"],
                       capture_output=True, text=True, timeout=20)
    if r.returncode != 0:
        pytest.skip("adb forward 失败：%s" % ((r.stderr or r.stdout).strip(),))
    try:
        yield CtrlClient("127.0.0.1", port, timeout=10)
    finally:
        subprocess.run([adb, "-s", serial, "forward", "--remove",
                        "tcp:%d" % port],
                       capture_output=True, timeout=20)


CASES = [
    ("GET", "/api/status", None, 200,
     ("version", "build", "profile", "preset_key", "limit_on", "listen",
      "me", "spec_hash")),
    ("GET", "/api/params", None, 200, ("spec_hash", "items")),
    ("GET", "/api/presets", None, 200, None),
    ("GET", "/api/share", None, 200, ("share_ver", "peers")),
    ("POST", "/api/share", {}, 405, ("error",)),
    ("GET", "/api/share/status", None, 200, ("share_ver", "peers", "inbox")),
    ("GET", "/api/share/inbox", None, 200, ("items",)),
    ("POST", "/api/share/push", {"peer": "", "ids": []}, 400, ("error",)),
    ("POST", "/api/sysproxy", {"enable": False}, 501, ("error",)),
    ("GET", "/api/float", None, 200, None),
    ("GET", "/api/__nope__", None, 404, ("error",)),
]


@pytest.mark.parametrize("method,path,body,want,keys", CASES,
                         ids=["%s %s" % (c[0], c[1]) for c in CASES])
def test_android_contract(android, method, path, body, want, keys):
    r = android.request(method, path, body)
    assert r.status == want, (r.status, r.body)
    if keys:
        assert isinstance(r.body, dict), r.body
        missing = [k for k in keys if k not in r.body]
        assert not missing, "缺少顶层字段：%r（实际：%r）" % (missing, sorted(r.body))


def test_android_spec_hash_matches_repo(android):
    """APK 里的规格哈希必须等于仓库里的 spec.json —— 端与端说同一套话。"""
    r = android.get("/api/params")
    assert r.status == 200
    assert r.body["spec_hash"] == W.SPEC_HASH, (
        "APK 规格 %s ≠ 仓库 %s：APK 是用旧 spec 生成的"
        % (r.body.get("spec_hash"), W.SPEC_HASH))


def test_android_params_table_matches_repo(android):
    r = android.get("/api/params")
    assert r.status == 200

    def project(items):
        # 两端 items 的附加字段不完全相同（Android 另带 type/min/step），
        # 契约是这些核心字段逐项一致。
        return [(str(i["key"]), i["label"], i.get("unit", ""), i["max"],
                 i["default"]) for i in items]

    assert project(r.body["items"]) == project(W.PARAM_SPECS)
