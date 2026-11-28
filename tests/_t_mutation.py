# -*- coding: utf-8 -*-
"""变异测试探针：验证「测试真的会因实现改坏而变红」。

为什么需要它
------------
测试数量、覆盖率、断言条数都只能证明「代码被执行过」，不能证明「错误会被
抓住」。本项目出现过两次反例：测试跟着实现写（把 bug 当成期望），以及
`assert W.X == W.X` 这类恒真断言。变异测试从另一头问：把实现里的一个运算符
改错，测试会不会红？不会红的变异体叫「幸存者」，它指向的要么是缺断言，
要么是等价变异。

做法
----
1. 用 `ast` 定位目标函数，用 `tokenize` 在函数范围内生成单点变异：
   比较符互换、`and/or` 互换、`not` 删除、整数 ±1、布尔取反、`+ - * /` 互换。
2. 把仓库复制到临时目录（绝不改工作区），只替换 `weaknet.py`。
3. 跑该函数对应的测试子集：绿 = 幸存，红 = 杀死，收集/语法错误 = 无效变异。
4. 输出变异得分与幸存者清单。等价变异需人工判定，不能直接当「测试没问题」。

用法
----
    python tests/_t_mutation.py                 # 跑默认抽样（99 个变异体，约 6 分钟）
    python tests/_t_mutation.py --list          # 只列出变异体，不跑
    python tests/_t_mutation.py --only Bucket.consume
    python tests/_t_mutation.py --json _tmp/mutation.json
    python tests/_t_mutation.py --min-score 85   # CI 门禁：低于门槛返回 1

设计取舍
--------
* 默认单进程串行：目标测试含令牌桶速率、延迟投递等时间敏感用例，并发跑会把
  CPU 争抢导致的超时误判成「杀死」，反而掩盖幸存者。
* 每个变异体都删一次 `__pycache__`：同一秒内改写同长度源码时，CPython 可能
  继续加载旧 pyc，导致「跑了变异体、其实还是原实现」。
* 变异体只做单点替换，不做多步组合：组合变异几乎必然被杀死，但对定位缺口的
  信息量更低。
"""
import argparse
import ast
import io
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
import tokenize
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "weaknet.py"

# 函数 → 最多保留多少个变异体（均匀抽样）。数字是按「一条测试 2~3 秒」估的，
# 总量控制在一次咖啡时间内；要全量跑把 cap 调大即可。
TARGETS = [
    ("Bucket.sync", 6),
    ("Bucket.cap", 4),
    ("Bucket.consume", 18),
    ("DelayLine._idle", 4),
    ("DelayLine._peer_alive", 8),
    ("DelayLine._drain", 5),
    ("DelayLine._mark_dead", 6),
    ("DelayLine.submit", 10),
    ("_hdr_get", 4),
    ("_hdr_values", 5),
    ("_hdr_tokens", 5),
    ("_resp_frame", 14),
    ("_resp_keepalive", 7),
    ("_client_keepalive", 6),
    ("_req_framing_error", 12),
    ("_down_head", 10),
    ("_rewrite_origin_request", 9),
]

FRAMING_UNIT = ["tests/test_unit_http_framing_rules.py"]
FRAMING_INTEG = ["tests/test_integration_http_framing.py"]


def tests_for(qualname):
    """每个函数跑能观察到它的最小用例集。

    全集跑一遍要 10 秒，99 个变异体就是 16 分钟；按文件收窄后单轮 3~5 分钟。

    **不要用 `-k` 按测试名收窄**：新加的用例只要名字不含关键字就被跳过，
    于是「补了断言仍然 SURVIVED」—— 本探针第一版就这样误报过两个幸存者
    （`test_rewrite_origin_defaults_to_keepalive` 被 `-k content_length` 排除）。
    按文件收窄是安全的：文件里所有用例都会跑。
    """
    if qualname.startswith("Bucket."):
        return ["tests/test_unit_engine.py"]
    if qualname.startswith("DelayLine."):
        return ["tests/test_unit_delayline.py"]
    if qualname in ("_rewrite_origin_request", "_down_head"):
        # 只有这两个函数的变异体能被 socket 级用例杀死（其余单测已足够）
        return FRAMING_UNIT + FRAMING_INTEG
    return FRAMING_UNIT


def find_functions(tree):
    """返回 {qualname: FunctionDef}，含类方法。"""
    out = {}
    stack = []

    class V(ast.NodeVisitor):
        def visit_ClassDef(self, node):
            stack.append(node.name)
            self.generic_visit(node)
            stack.pop()

        def visit_FunctionDef(self, node):
            out[".".join(stack + [node.name])] = node
            stack.append(node.name)
            self.generic_visit(node)
            stack.pop()

        visit_AsyncFunctionDef = visit_FunctionDef

    V().visit(tree)
    return out


CMP = {"==": "!=", "!=": "==", "<": "<=", "<=": "<", ">": ">=", ">=": ">"}
ARITH = {"+": "-", "-": "+", "*": "/", "/": "*", "//": "/"}
NAMES = {"and": "or", "or": "and", "not": "", "True": "False", "False": "True"}


def candidates(src, node):
    """函数范围内的单点变异候选：[(line, col, old, new, src2)]"""
    out = []
    lines = src.splitlines(keepends=True)
    toks = list(tokenize.generate_tokens(io.StringIO(src).readline))
    for tok in toks:
        sr, sc = tok.start
        er, ec = tok.end
        if sr < node.lineno or er > node.end_lineno or sr != er:
            continue
        new = None
        if tok.type == tokenize.OP and tok.string in CMP:
            new = CMP[tok.string]
        elif tok.type == tokenize.OP and tok.string in ARITH:
            new = ARITH[tok.string]
        elif tok.type == tokenize.NAME and tok.string in NAMES:
            new = NAMES[tok.string]
        elif tok.type == tokenize.NUMBER:
            try:
                n = int(tok.string)
            except ValueError:
                continue                    # 浮点/复数先不动，噪声太大
            new = str(n + 1)
        if new is None or new == tok.string:
            continue
        line = lines[sr - 1]
        mutated = list(lines)
        mutated[sr - 1] = line[:sc] + new + line[ec:]
        out.append((sr, sc, tok.string, new, "".join(mutated)))
    return out


def sample(items, cap):
    """按顺序均匀抽样，保留跨度；cap<=0 表示全要。"""
    if cap <= 0 or len(items) <= cap:
        return items
    step = len(items) / float(cap)
    return [items[int(i * step)] for i in range(cap)]


def build_mutants(src, caps):
    tree = ast.parse(src)
    funcs = find_functions(tree)
    mutants = []
    for qual, cap in caps:
        node = funcs.get(qual)
        if node is None:
            print("!! 找不到函数：%s" % qual)
            continue
        cands = candidates(src, node)
        # 同一个起始位置只留一个替换（比较符不会和算术符重叠，但 not/True 可能）
        for sr, sc, old, new, src2 in sample(cands, cap):
            try:
                compile(src2, "weaknet.py", "exec")
            except SyntaxError:
                continue
            mutants.append({"func": qual, "line": sr, "col": sc,
                            "old": old, "new": new, "src": src2,
                            "tests": tests_for(qual)})
    return mutants


def make_tree():
    d = Path(tempfile.mkdtemp(prefix="wn_mut_"))
    shutil.copy2(SRC, d / "weaknet.py")
    shutil.copy2(ROOT / "version.py", d / "version.py")
    shutil.copy2(ROOT / "text_layout.py", d / "text_layout.py")
    shutil.copy2(ROOT / "pytest.ini", d / "pytest.ini")
    shutil.copytree(ROOT / "tests", d / "tests",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    if (ROOT / "spec").is_dir():
        shutil.copytree(ROOT / "spec", d / "spec")
    return d


def clean_cache(d):
    for p in (d / "__pycache__", d / "tests" / "__pycache__"):
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)


def run_tests(d, test_files, timeout):
    env = {k: v for k, v in __import__("os").environ.items()
           if k != "PYTHONPATH"}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    # 变异体经常把循环条件改坏 → 死循环。默认 30s 的 run_async 超时会把
    # 一轮 99 个变异体拖到一小时；5s 足以区分「慢」和「卡死」。
    env["WN_TEST_TIMEOUT"] = "5"
    cmd = [sys.executable, "-m", "pytest", "-x", "-q", "--tb=no",
           "-p", "no:cacheprovider"] + test_files
    t0 = time.time()
    try:
        p = subprocess.run(cmd, cwd=str(d), env=env, timeout=timeout,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    except subprocess.TimeoutExpired:
        return "timeout", "(超过 %ds)" % timeout, time.time() - t0
    out = p.stdout.decode("utf-8", "replace")
    if p.returncode == 0:
        return "survived", "", time.time() - t0
    if p.returncode == 1:
        m = re.search(r"FAILED (\S+)", out)
        return "killed", (m.group(1) if m else "(退出 1，未解析到用例名)"), time.time() - t0
    return "invalid", ("pytest 退出码 %d: %s" %
                       (p.returncode, out.strip().splitlines()[-1][:200] if out.strip() else "(无输出)")), time.time() - t0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true", help="只列出变异体")
    ap.add_argument("--only", default="", help="只跑 qualname 含该子串的")
    ap.add_argument("--cap", type=int, default=0, help="覆盖每个函数的上限（0=用默认）")
    ap.add_argument("--timeout", type=int, default=20,
                    help="单个变异体的硬超时；CPU 死循环时 run_async 的取消无法生效")
    ap.add_argument("--json", default="")
    ap.add_argument("--min-score", type=float, default=0.0,
                    help="低于该分数返回 1（CI 门禁用；等价变异要人工判定，"
                         "所以门槛取略低于实测值，而不是 100）")
    args = ap.parse_args()

    src = SRC.read_text(encoding="utf-8")
    caps = [(q, args.cap or c) for q, c in TARGETS]
    mutants = build_mutants(src, caps)
    if args.only:
        mutants = [m for m in mutants if args.only in m["func"]]
    if not mutants:
        print("没有变异体。")
        return 2

    if args.list:
        for i, m in enumerate(mutants, 1):
            print("%3d. %-26s :%-5d %r -> %r" %
                  (i, m["func"], m["line"], m["old"], m["new"]))
        print("共 %d 个" % len(mutants))
        return 0

    d = make_tree()
    try:
        clean_cache(d)
        status, detail, secs = run_tests(
            d, ["tests/test_unit_engine.py", "tests/test_unit_delayline.py",
                "tests/test_unit_http_framing_rules.py",
                "tests/test_integration_http_framing.py"], args.timeout)
        if status != "survived":
            print("基线未通过（%s %s），变异测试无意义，先修测试。" % (status, detail))
            return 2
        print("基线通过（%.1fs），临时树：%s\n" % (secs, d))

        results = []
        t0 = time.time()
        for i, m in enumerate(mutants, 1):
            (d / "weaknet.py").write_text(m["src"], encoding="utf-8")
            clean_cache(d)
            st, detail, secs = run_tests(d, m["tests"], args.timeout)
            m2 = {k: m[k] for k in ("func", "line", "old", "new")}
            m2.update({"status": st, "detail": detail, "secs": round(secs, 2)})
            results.append(m2)
            mark = {"killed": "KILLED  ", "survived": "SURVIVED",
                    "invalid": "INVALID ", "timeout": "TIMEOUT "}[st]
            print("[%3d/%3d] %s %-26s :%-5d %r -> %r  %s" %
                  (i, len(mutants), mark, m["func"], m["line"],
                   m["old"], m["new"], detail))
            sys.stdout.flush()
    finally:
        shutil.rmtree(d, ignore_errors=True)

    valid = [r for r in results if r["status"] in ("killed", "survived", "timeout")]
    killed = [r for r in valid if r["status"] in ("killed", "timeout")]
    survived = [r for r in valid if r["status"] == "survived"]
    invalid = [r for r in results if r["status"] == "invalid"]
    score = (100.0 * len(killed) / len(valid)) if valid else 0.0

    print("\n===== 汇总 =====")
    per = {}
    for r in valid:
        k = per.setdefault(r["func"], [0, 0])
        if r["status"] in ("killed", "timeout"):
            k[0] += 1
        else:
            k[1] += 1
    for fn in [q for q, _ in TARGETS if q in per]:
        k, s = per[fn]
        print("  %-26s 杀死 %2d / 幸存 %2d  %5.1f%%" %
              (fn, k, s, 100.0 * k / (k + s)))
    print("\n变异得分：%d/%d = %.1f%%（无效 %d 个，未计入）" %
          (len(killed), len(valid), score, len(invalid)))
    if survived:
        print("\n幸存者（需人工判定：缺断言 or 等价变异）：")
        for r in survived:
            print("  - %s:%-5d %r -> %r" %
                  (r["func"], r["line"], r["old"], r["new"]))
    print("总耗时 %.1fs" % (time.time() - t0))

    if args.json:
        p = Path(args.json)
        if not p.is_absolute():
            p = ROOT / p
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"score": round(score, 2),
                                 "killed": len(killed), "valid": len(valid),
                                 "invalid": len(invalid), "results": results},
                                ensure_ascii=False, indent=2), encoding="utf-8")
        print("已写入 %s" % p)
    if args.min_score and score < args.min_score:
        print("\n变异得分 %.1f%% 低于门槛 %.1f%%：测试没有想象中会红，"
              "逐个看幸存者是不是缺断言。" % (score, args.min_score))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
