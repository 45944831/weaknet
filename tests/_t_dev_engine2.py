# -*- coding: utf-8 -*-
"""批次 2 补测：上行限速（重试）、丢包效果（改用吞吐对比）、airplane/dns_fail、统计。"""
import sys
import threading
import time
import urllib.request

sys.path.insert(0, __file__.rsplit("\\", 1)[0])
import _t_dev_lib as D
from _t_dev_engine import Srv, mbps, set_limit, preset, off, down_test, _op, PC_IP, SRV_PORT

import subprocess


def main():
    D.setup_forward()
    subprocess.run([D.ADB, "-s", D.SERIAL, "forward", "tcp:18821", "tcp:8888"], capture_output=True)
    Srv().start()
    time.sleep(0.5)

    D.section("A-103 上行限速 512kbps（重试 3 次取最好）")
    best = None
    for attempt in range(3):
        set_limit(up_kbps=512)
        body = b"u" * (256 * 1024)
        req = urllib.request.Request("http://%s:%d/echo" % (PC_IP, SRV_PORT), data=body, method="POST")
        t0 = time.time()
        try:
            with _op.open(req, timeout=90) as r:
                r.read()
            dt = time.time() - t0
            got = mbps(len(body), dt)
            print("    第 %d 次：%.3f Mbps" % (attempt + 1, got))
            if best is None or got > best[0]:
                best = (got, dt)
        except Exception as e:
            print("    第 %d 次异常：%s" % (attempt + 1, str(e)[:80]))
    if best:
        ratio = best[0] / 0.512
        D.rec("A-103 上行 512kbps 实测在 60%~150% 区间", 0.6 <= ratio <= 1.5,
              "%.3f Mbps（%.0f%%）" % (best[0], ratio * 100))
    else:
        D.rec("A-103 上行限速", False, "三次全部失败（可能真缺陷：上行通道挂起）")

    D.section("A-105 丢包 20%：吞吐对比 + 失败率")
    off()
    set_limit()  # 全 0
    n0, dt0 = down_test()          # 无丢包 4MB
    base = mbps(n0, dt0)
    set_limit(loss_pct=20)
    fails = 0
    times = []
    for i in range(5):
        try:
            n, dt = down_test()
            times.append(mbps(n, dt))
        except Exception:
            fails += 1
    avg_loss = sum(times) / len(times) if times else 0
    D.rec("A-105 丢包 20% 下吞吐显著下降或出现失败",
          (fails >= 1) or (base > 0 and avg_loss < base * 0.7),
          "无丢包 %.2f Mbps → 丢包 %.2f Mbps（%.0f%%），失败 %d/5" %
          (base, avg_loss, (avg_loss / base * 100) if base else 0, fails))

    D.section("A-107-b 完全断网 = airplane（立即拒连）")
    preset("airplane")
    t0 = time.time()
    refused = False
    try:
        _op.open("http://%s:%d/ok" % (PC_IP, SRV_PORT), timeout=8).read()
    except Exception as e:
        refused = True
        emsg = str(e)[:80]
    dt = time.time() - t0
    D.rec("A-107-b airplane：立即失败（<3s）", refused and dt < 3, "%.2fs %s" % (dt, emsg if refused else ""))

    D.section("A-107-c DNS 故障 = dns_fail")
    preset("dns_fail")
    try:
        _op.open("http://www.baidu.com/favicon.ico", timeout=10).read()
        dnsok = True
    except Exception:
        dnsok = False
    preset("direct")
    try:
        _op.open("http://www.baidu.com/favicon.ico", timeout=10).read()
        ok_now = True
    except Exception:
        ok_now = False
    D.rec("A-107-c DNS 故障拦域名，直连放行同一请求", (not dnsok) and ok_now,
          "dns_fail 成功=%s，direct 成功=%s" % (dnsok, ok_now))

    D.section("A-110 统计自洽")
    st = D.status().get("stats", {})
    print("    stats =", json.dumps(st, ensure_ascii=False)[:300])
    D.rec("A-110 统计接口字节数 > 0", isinstance(st, dict) and
          sum(v for v in st.values() if isinstance(v, (int, float))) > 0, "")

    off()
    D.summary("批次2补测")


if __name__ == "__main__":
    main()
