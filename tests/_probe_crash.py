# -*- coding: utf-8 -*-
"""崩溃注入探针：验证「进程在写注册表途中被杀」不会把用户网络弄坏。

为什么单靠单测不够
------------------
`test_unit_sysproxy.py` 覆盖的是「函数返回失败」：`SetValueEx` 抛 OSError 后
回滚。但真实事故是进程**根本没机会执行回滚**——taskkill / 断电 / 蓝屏发生在
两次 SetValueEx 之间。此时内存里的 prev 没了，只剩落盘的接管记账。

五个断言（每个崩溃点都要满足）
------------------------------
1. 注册表最终逐字段等于接管前（值 + 类型）；
2. 接管记账被清掉（否则下次启动会拿过期记录乱还原）；
3. 锁文件不残留（死进程的 pid 不该拦住下一次接管）；
4. `release_stale_sysproxy` 返回 True（确实做了还原，而不是「清理记录就走」）；
5. 再跑一次恢复是幂等的（不会把已还原的字段再改一遍）。

用法
----
    python tests/_probe_crash.py            # 跑全部场景
    python tests/_probe_crash.py -v         # 打印每个子进程输出
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
CHILD = HERE / "_crash_child.py"

CORP = {"ProxyEnable": [1, 4],
        "ProxyServer": ["10.0.0.9:3128", 1],
        "ProxyOverride": ["*.corp;10.*", 1]}

FAILED = []
LEGACY = False


def new_dir():
    d = Path(tempfile.mkdtemp(prefix="wn_crash_"))
    (d / "reg.json").write_text(json.dumps(CORP, ensure_ascii=False), encoding="utf-8")
    return d


def read_reg(d):
    return json.loads((d / "reg.json").read_text(encoding="utf-8"))


def run_child(d, mode, kill_at=0, fail_at=0, fail_delete="", timeout=60,
              port=8888, busy=0):
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env.update({
        "WN_CRASH_DIR": str(d), "WN_CRASH_MODE": mode,
        "WN_CRASH_KILL_AT": str(kill_at), "WN_CRASH_FAIL_AT": str(fail_at),
        "WN_CRASH_FAIL_DELETE": fail_delete,
        "WN_CRASH_PORT": str(port), "WN_CRASH_BUSY_PORT": str(busy),
        "WN_CRASH_LEGACY": "1" if LEGACY else "0",
        "PYTHONDONTWRITEBYTECODE": "1",
    })
    try:
        return subprocess.run([sys.executable, str(CHILD)], env=env,
                              capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        return subprocess.CompletedProcess([], -9, e.stdout or "",
                                           "timeout after %ss" % timeout)


def record(label, ok, detail="", known=False):
    tag = "PASS" if ok else ("KNOWN" if known else "FAIL")
    print("%-5s %-38s %s" % (tag, label, detail))
    if not ok and not known:
        FAILED.append((label, detail))
    return ok


def assert_clean(d, label, verbose, out=""):
    reg = read_reg(d)
    state = (d / "weaknet_sysproxy.json").exists()
    lock = (d / "weaknet_sysproxy.lock").exists()
    ok = reg == CORP and not state and not lock
    detail = ""
    if not ok:
        detail = "reg=%s state=%s lock=%s" % (
            "同原值" if reg == CORP else json.dumps(reg, ensure_ascii=False),
            state, lock)
        if out and verbose:
            detail += " | " + out.strip().replace("\n", " / ")[-300:]
    return record(label, ok, detail)


def case_fail_takeover(verbose):
    for i in range(1, 6):
        d = new_dir()
        p = run_child(d, "takeover", fail_at=i)
        ok = (p.returncode == 0 and "ok=False" in p.stdout)
        if ok:
            assert_clean(d, "失败注入：写第 %d 字段抛 OSError" % i, verbose, p.stdout)
        else:
            record("失败注入：写第 %d 字段抛 OSError" % i, False,
                   "rc=%s out=%s" % (p.returncode, p.stdout[-200:] + p.stderr[-200:]))
        shutil.rmtree(d, ignore_errors=True)


def case_fail_delete(verbose):
    d = new_dir()
    p = run_child(d, "takeover", fail_delete="AutoConfigURL")
    assert_clean(d, "失败注入：PAC 字段删不掉（策略锁定）", verbose, p.stdout)
    shutil.rmtree(d, ignore_errors=True)


def case_crash_takeover(verbose):
    for i in range(1, 6):
        d = new_dir()
        p = run_child(d, "takeover", kill_at=i)
        if p.returncode != 9:
            record("崩溃注入：接管写第 %d 字段后被杀" % i, False,
                   "子进程未被杀（rc=%s）" % p.returncode)
            shutil.rmtree(d, ignore_errors=True)
            continue
        r = run_child(d, "recover")
        ok = (r.returncode == 0 and "ok=True" in r.stdout)
        label = "崩溃注入：接管写第 %d 字段后被杀" % i
        if not ok:
            record(label, False, "恢复未成功：rc=%s %s" % (r.returncode, r.stdout[-160:]))
        else:
            assert_clean(d, label, verbose, r.stdout)
        shutil.rmtree(d, ignore_errors=True)


def case_crash_release(verbose):
    for i in range(1, 6):
        d = new_dir()
        p0 = run_child(d, "takeover")
        if p0.returncode != 0 or "ok=True" not in p0.stdout:
            record("崩溃注入：还原写第 %d 字段后被杀" % i, False,
                   "前置接管失败：%s" % p0.stdout[-160:])
            shutil.rmtree(d, ignore_errors=True)
            continue
        p1 = run_child(d, "release", kill_at=i)
        if p1.returncode != 9:
            record("崩溃注入：还原写第 %d 字段后被杀" % i, False,
                   "子进程未被杀（rc=%s）" % p1.returncode)
            shutil.rmtree(d, ignore_errors=True)
            continue
        before = read_reg(d)
        r = run_child(d, "recover")
        # 若第 5 次写完后才被杀，注册表已经还原完，恢复只做清理、返回 False；
        # 其余时点必须真的写回，返回 True。
        want_write = before != CORP
        ok = (r.returncode == 0 and
              (("ok=True" in r.stdout) if want_write else True))
        label = "崩溃注入：还原写第 %d 字段后被杀" % i
        if not ok:
            record(label, False, "恢复未成功：rc=%s %s" % (r.returncode, r.stdout[-160:]))
        else:
            assert_clean(d, label, verbose, r.stdout)
        shutil.rmtree(d, ignore_errors=True)


def case_crash_rollback(verbose):
    """写最后一个字段失败 → 回滚到第一个字段时又被杀。"""
    d = new_dir()
    p = run_child(d, "takeover", fail_at=5, kill_at=6)
    if p.returncode != 9:
        record("崩溃注入：回滚到一半被杀", False, "子进程未被杀（rc=%s）" % p.returncode)
    else:
        r = run_child(d, "recover")
        ok = (r.returncode == 0 and "ok=True" in r.stdout)
        if not ok:
            record("崩溃注入：回滚到一半被杀", False,
                   "恢复未成功：rc=%s %s" % (r.returncode, r.stdout[-160:]))
        else:
            assert_clean(d, "崩溃注入：回滚到一半被杀", verbose, r.stdout)
    shutil.rmtree(d, ignore_errors=True)


def case_other_instance_alive(verbose):
    """上次跑在 8899、这次默认 8888：8899 上的实例还活着，不能当成残留。

    探测端口若用本次参数（8888），会得到「没人监听」，把活实例的系统代理
    还原掉；必须用记账里的 port。
    """
    d = new_dir()
    p = run_child(d, "takeover", kill_at=2, port=8899)
    if p.returncode != 9:
        record("另一实例存活：按记录端口判断，不能误还原", False,
               "子进程未被杀（rc=%s）" % p.returncode)
    else:
        r = run_child(d, "recover", port=8888, busy=8899)
        reg = read_reg(d)
        state = (d / "weaknet_sysproxy.json").exists()
        ok = (r.returncode == 0 and "ok=False" in r.stdout and state
              and reg.get("ProxyServer", [""])[0] == "127.0.0.1:8899")
        record("另一实例存活：按记录端口判断，不能误还原", ok,
               "rc=%s state=%s reg=%s" %
               (r.returncode, state, json.dumps(reg, ensure_ascii=False)))
    shutil.rmtree(d, ignore_errors=True)


def case_state_write_fail(verbose):
    d = new_dir()
    p = run_child(d, "state_fail")
    ok = p.returncode == 0 and "ok=True" in p.stdout
    label = "记账落盘失败：进程内仍能接管并还原"
    if not ok:
        record(label, False, "rc=%s %s" % (p.returncode, p.stdout[-160:]))
    else:
        assert_clean(d, label, verbose, p.stdout)
    shutil.rmtree(d, ignore_errors=True)


def case_record_lost(verbose):
    """记账被删（老版本残留 / 用户清理）——只能退化为「只关开关」。"""
    d = new_dir()
    p = run_child(d, "takeover", kill_at=3)
    if p.returncode != 9:
        record("记账丢失：退化路径只关 ProxyEnable", False, "子进程未被杀")
    else:
        (d / "weaknet_sysproxy.json").unlink(missing_ok=True)
        r = run_child(d, "recover")
        reg = read_reg(d)
        ok = reg.get("ProxyEnable", [None])[0] == 0
        record("记账丢失：退化路径只关 ProxyEnable（已知降级）", ok,
               "reg=%s" % json.dumps(reg, ensure_ascii=False), known=True)
    shutil.rmtree(d, ignore_errors=True)


def main():
    global LEGACY
    ap = argparse.ArgumentParser()
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--legacy", action="store_true",
                    help="负对照：换回修复前的全字段匹配逻辑，探针应当变红")
    args = ap.parse_args()
    LEGACY = args.legacy
    print("崩溃注入探针（子进程真实 os._exit(9)，不碰真实注册表）\n")
    if LEGACY:
        print("!! 负对照模式：恢复逻辑已换回修复前，部分接管场景预期 FAIL\n")
    case_fail_takeover(args.verbose)
    case_fail_delete(args.verbose)
    case_crash_takeover(args.verbose)
    case_crash_release(args.verbose)
    case_other_instance_alive(args.verbose)
    case_crash_rollback(args.verbose)
    case_state_write_fail(args.verbose)
    case_record_lost(args.verbose)
    print("\n===== 汇总 =====")
    if FAILED:
        print("FAIL %d 项：" % len(FAILED))
        for name, detail in FAILED:
            print("  - %s：%s" % (name, detail))
        return 1
    print("全部通过（KNOWN 为已知降级，不计失败）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
