# -*- coding: utf-8 -*-
"""真机契约用例（默认不跑：pytest -m device）。

前置：
    set ANDROID_SERIAL=<设备序列号>      # adb devices 里那个
    pytest -m device

夹具会自己 adb forward 一个随机本地端口到手机的 8899，用完撤掉；
不会安装/卸载/清数据 —— 只读接口。唯一的写操作是：探活不通时把 App
拉到前台（见 android 夹具），因为冻结状态下接口不响应。
"""
import os
import shutil
import socket
import subprocess
import time

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


def _adb(adb, serial, *args, timeout=20):
    """adb 调用统一入口。

    stdin 必须接 DEVNULL：`adb shell` 在 stdin 继承到父进程的管道/终端时
    会一直等输入不返回，在 Windows 上表现为 subprocess.TimeoutExpired
    （本地实测 `adb shell am start` 卡满 20s 才抛）。
    """
    return subprocess.run([adb, "-s", serial] + list(args),
                          capture_output=True, text=True,
                          stdin=subprocess.DEVNULL, timeout=timeout)


def _probe(client, tries=4):
    """探活：真发一次请求，回了才算通。

    只看端口是否 LISTEN 会误判 —— App 被系统冻结时 socket 仍在
    LISTEN，但一个字节都不回，连上去只会一直等到超时。
    """
    for i in range(tries):
        try:
            client.request("GET", "/api/status", timeout=3)
            return True
        except Exception:
            if i + 1 < tries:
                time.sleep(0.4)
    return False


@pytest.fixture(scope="module")
def android():
    serial = os.environ.get("ANDROID_SERIAL")
    if not serial:
        pytest.skip("未设置 ANDROID_SERIAL")
    adb = shutil.which("adb")
    if not adb:
        pytest.skip("找不到 adb")
    port = free_port()
    r = _adb(adb, serial, "forward", "tcp:%d" % port, "tcp:8899")
    if r.returncode != 0:
        pytest.skip("adb forward 失败：%s" % ((r.stderr or r.stdout).strip(),))
    client = CtrlClient("127.0.0.1", port, timeout=10)
    try:
        # 手机把后台 App 冻结后，8899 还在 LISTEN 却完全不回包（实测 5s
        # 超时）；`am start` 拉到前台后立刻恢复。不做这一步的话，手机放
        # 一夜再跑 `pytest -m device` 会整片超时，看着像产品坏了。
        if not _probe(client):
            try:
                _adb(adb, serial, "shell", "am", "start", "-n",
                     "com.weaknet.app/.MainActivity")
            except subprocess.SubprocessError as exc:
                pytest.skip("拉起 App 失败（%s）" % (type(exc).__name__,))
            if not _probe(client, tries=25):
                pytest.skip("App 不响应 8899（后台被冻结，拉前台也没起来）")
        yield client
    finally:
        _adb(adb, serial, "forward", "--remove", "tcp:%d" % port)


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
