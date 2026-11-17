# -*- coding: utf-8 -*-
"""外部真值工具的统计口径单测。

钉死一件事：**主口径必须是接收端（sum_received），不是发送端（sum_sent）**。

2026-11-13 的教训：`-P 8` 共享瓶颈时，iperf3 客户端本地的发送计数会系统性
落后于真正发出去并被对端收到的字节。当时工具默认取发送端，于是同一个产品
被测出「限速低 36%」的假缺陷，而代理侧自计与服务端接收两个独立计数器都落在
设定值上。没有单测锁住口径，下一个改口径的人会把假缺陷再放出来一次。

对应实测量（限速 8000 kbps、-P 8、20s）：
    sum_sent = 17,170,432 B = 6708 kbps
    sum_received = 20,512,768 B = 8009 kbps
    代理 bytes_up = 8083 kbps
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from perf_iperf import _extract            # noqa: E402

pytestmark = pytest.mark.unit


def _doc(sent_bytes, sent_bps, recv_bytes, recv_bps, streams=1):
    return {"end": {
        "sum_sent": {"bytes": sent_bytes, "bits_per_second": sent_bps},
        "sum_received": {"bytes": recv_bytes, "bits_per_second": recv_bps},
        "streams": [{"sender": {"bytes": sent_bytes // streams,
                                "bits_per_second": sent_bps // streams},
                     "receiver": {"bytes": recv_bytes // streams,
                                  "bits_per_second": recv_bps // streams}}] * streams,
    }}


def test_primary_metric_is_receiver_side():
    """多流共享瓶颈的真实数据：发送端少计，取值必须是接收端。"""
    d = _doc(17170432, 6868953, 20512768, 8201425, streams=8)
    v = _extract(d)
    assert v is not None
    # 主口径 = 接收端 8201425 bps
    assert v["bps"] == 8201425
    assert v["kbps_1024"] == pytest.approx(8009, rel=0.01)
    # 发送端并列留存，供口径自证
    assert v["sender_bps"] == 6868953
    assert v["sender_bytes"] == 17170432
    assert v["receiver_bytes"] == 20512768


def test_gap_pct_is_negative_when_sender_undercounts():
    """发送端比接收端少 → gap 为负（有符号字段：负=客户端侧少计）。"""
    d = _doc(17170432, 6868953, 20512768, 8201425, streams=8)
    v = _extract(d)
    expect = (17170432 - 20512768) / 20512768 * 100.0
    assert v["sender_receiver_gap_pct"] == pytest.approx(expect, rel=1e-6)
    assert v["sender_receiver_gap_pct"] < 0


def test_fallback_to_sender_when_receiver_absent():
    """老版本 iperf3 / 某些 UDP 输出没有 sum_received：退回发送端，不能崩。"""
    d = {"end": {"sum_sent": {"bytes": 1000, "bits_per_second": 8000}}}
    v = _extract(d)
    assert v is not None
    assert v["bps"] == 8000
    assert v["sender_receiver_gap_pct"] is None


def test_error_payload_returns_none():
    assert _extract({"error": "boom"}) is None
    assert _extract({"end": {}}) is None


def test_stream_details_retained():
    """逐流明细要留档：并发档出现流被掐断时，靠它判断是限速还是断连。"""
    d = _doc(20512768, 8201425, 20512768, 8201425, streams=8)
    v = _extract(d)
    assert v["streams"] == 8
    assert len(v["stream_bps"]) == 8
    assert len(v["stream_recv_bps"]) == 8


# ---------------------------------------------------------------- 参数拼装

def test_cmd_default_does_not_change_legacy_flags():
    """默认路径必须与历史口径一致：不传 -l / -A。"""
    from perf_iperf import _iperf_cmd
    a = _iperf_cmd(5201, 1, 20, 3)
    assert "-l" not in a and "-A" not in a
    assert a[:2] == ["-c", "127.0.0.1"]


def test_cmd_loss_block_is_bytes_and_affinity_is_core():
    """-l 的单位是字节（CLI 用 KB 输入）；-A 只在显式 >=0 时出现。"""
    from perf_iperf import _iperf_cmd
    a = _iperf_cmd(5201, 1, 20, 3, block_kb=4, affinity=2)
    assert a[a.index("-l") + 1] == str(4 * 1024)
    assert a[a.index("-A") + 1] == "2"


def test_cmd_udp_and_reverse_compose():
    from perf_iperf import _iperf_cmd
    a = _iperf_cmd(5201, 4, 10, 2, reverse=True, udp=True, kbps=8000)
    assert "-R" in a and "-u" in a
    assert a[a.index("-b") + 1] == "8000K"
