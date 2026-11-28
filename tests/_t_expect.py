# -*- coding: utf-8 -*-
"""测试期望来源检查：防「改实现 → 改期望 → 绿」。

背景（第五件）：一套测试全绿，只能说明「实现与期望一致」，不能说明「实现是对的」。
后者取决于期望从哪来。当期望本身是从被测实现里抄来的（读常量、调同一个函数、
照着输出改文案），改实现时把期望一起改掉，测试永远绿，缺陷照样出厂。

这个脚本把「期望从哪来」变成可检查的东西：

  X1 覆盖   每个 tests/test_*.py 都必须在 SOURCES 里登记真值来源
  X2 可解析 来源必须是真实存在的文件、RFC 编号，或下列已定义标签之一
  X3 恒真   assert 两侧语法完全相同（写了等于没写）
  X4 镜像   断言一侧是被测符号（W.常量 / W.函数()），另一侧又拿不出独立期望
            → 必须登记在 MIRRORS 并写明为什么可以这样比
  X5 过期   MIRRORS 里登记的组合如果代码里已经不存在，必须删掉（白名单不能腐烂）
  X6 公式   断言里同时出现实现常量与乘除运算 —— 往往是把实现公式抄了一遍

之所以「登记」而不是一律禁止：管线回显（API 原样返回常量）和幂等不变量
（clean(x) == x）是合法写法，但它们只能证明「值传到了」，不能证明「值是对的」。
登记的作用是让这个区别写在代码里，评审时问得出「那这个值本身谁保证」。

用法：
    python tests/_t_expect.py            # 检查，有问题返回 1
    python tests/_t_expect.py --list     # 打印清单
"""
import argparse
import ast
import io
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TESTS = ROOT / "tests"
SRC = ROOT / "weaknet.py"

# ---------------------------------------------------------------- 来源标签

TAGS = {
    "规格真值": "spec/spec.json 或由它生成的常量",
    "协议真值": "RFC / HTTP 规范原文",
    "平台真值": "Android SDK、Java API、WinINET 等官方文档",
    "实测真值": "A/B 对照或真机实测数据",
    "跨端对拍": "两端独立实现互为证据",
    "产品决策": "写死的产品约束，不随实现变化",
    "文案快照": "界面 / 报告措辞的显式快照，改文案要一起改",
    "管线回显": "只验证值有没有传到，不验证值本身",
    "现状契约": "当前行为，改动前需人工确认",
}

# 文件 → 该文件期望值的依据。新增测试文件必须在这里登记。
SOURCES = {
    "test_device_api.py": ["平台真值", "docs/verification.md"],
    "test_device_premise.py": ["平台真值", "产品决策"],
    "test_golden_scripts.py": ["docs/build.md", "产品决策"],
    "test_gui_perf.py": ["跨端对拍", "android/src/com/weaknet/app/FrameStats.java"],
    "test_gui_refresh.py": ["产品决策", "文案快照"],
    "test_gui_state.py": ["产品决策", "文案快照"],
    "test_gui_text.py": ["spec/spec.json", "文案快照"],
    "test_integration_ctrl.py": ["spec/spec.json", "产品决策"],
    "test_integration_http_framing.py": ["RFC 9112 §6.3", "RFC 9110 §10.1.1"],
    "test_integration_proxy.py": ["RFC 9112 §9.3", "实测真值"],
    "test_integration_share.py": ["spec/spec.json", "RFC 9112 §6.3"],
    "test_perf.py": ["实测真值", "tests/perf/_perf_baseline.json"],
    "test_report_gen.py": ["产品决策", "文案快照"],
    "test_unit_combo_guard.py": ["产品决策"],
    "test_unit_defaults.py": ["spec/spec.json", "跨端对拍"],
    "test_unit_delayline.py": ["实测真值", "现状契约"],
    "test_unit_engine.py": ["实测真值", "产品决策"],
    "test_unit_expect_truth.py": ["tests/_t_expect.py", "产品决策"],
    "test_unit_http_framing_rules.py": ["RFC 9112 §6.3", "RFC 9110 §10.1.1"],
    "test_unit_name_limit.py": ["spec/spec.json", "产品决策"],
    "test_unit_no_proxy_auth.py": ["产品决策"],
    "test_unit_op_sequences.py": ["产品决策"],
    "test_unit_profile.py": ["spec/spec.json"],
    "test_unit_release_truth.py": ["tests/_t_release.py", "产品决策"],
    "test_unit_session.py": ["产品决策", "docs/verification.md"],
    "test_unit_share.py": ["spec/spec.json", "产品决策"],
    "test_unit_spec.py": ["spec/spec.json", "tools/gen_spec.py"],
    "test_unit_sysproxy.py": ["平台真值", "产品决策"],
    "test_unit_tab_sync.py": ["产品决策"],
    "test_unit_text_layout.py": ["产品决策", "文案快照"],
    "test_unit_tuning.py": ["平台真值"],
    "test_unit_tunnel_hard_stop.py": ["产品决策"],
    "test_unit_update.py": ["平台真值", "产品决策"],
    "test_unit_usb_takeover.py": ["平台真值", "产品决策"],
}

# 镜像断言登记：(文件, 被测符号) → 为什么可以这样比
# 「为什么」必须回答：这个值本身由谁保证？如果只能答「实现就是这么写的」，那就不该登记。
MIRRORS = {
    ("test_device_api.py", "SPEC_HASH"):
        "管线回显：接口有没有把 SPEC_HASH 传出来；值本身由 test_unit_spec 对 spec 文件哈希锚定",
    ("test_gui_text.py", "PRESET_SHORT"):
        "规格真值：GUI 没有自己抄一份短名（两边都从 spec 派生），这是同源检查而非取值检查",
    ("test_integration_ctrl.py", "PARAM_SPECS"):
        "管线回显：/api/spec 是否原样返回参数表；值本身由 test_unit_spec 对 spec 锚定",
    ("test_integration_ctrl.py", "SPEC_HASH"):
        "管线回显：接口是否带出规格指纹；值本身由 test_unit_spec 对 spec 文件哈希锚定",
    ("test_integration_ctrl.py", "build_preset_profile"):
        "管线回显：选中预设后 /api/status 是否回同一份参数；取值由 test_unit_profile 对 spec 锚定",
    ("test_integration_ctrl.py", "default_profile"):
        "管线回显：停止后是否回到直连档；取值由 test_unit_profile 对 spec 默认值锚定",
    ("test_unit_defaults.py", "DEFAULT_COMBOS"):
        "跨端对拍：与 gen_spec 生成的 SpecGen.DEFAULT_COMBOS_JSON 逐字比对，spec 由 --check 保证同源",
    ("test_unit_defaults.py", "DEFAULT_SCENES"):
        "跨端对拍：与 gen_spec 生成的 SpecGen.DEFAULT_SCENES_JSON 逐字比对，spec 由 --check 保证同源",
    ("test_unit_defaults.py", "DEFAULT_SEED"):
        "跨端对拍 + 落盘回读：种子与 Android 生成值一致，且写进存档后能读回",
    ("test_unit_name_limit.py", "NAME_MAX"):
        "产品决策：截断结果长度不超过上限；具体 10 字由同文件字面量锚定",
    ("test_unit_name_limit.py", "_share_clean"):
        "幂等不变量：内置预设名不该被清洗函数改动，期望就是输入原值；这条证明的是「没多做事」",
    ("test_unit_http_framing_rules.py", "_down_head"):
        "幂等不变量：两个开关都关时必须原样返回，期望就是输入拼接；证明「没多做事」，不改写本身另有逐项断言",
    ("test_unit_profile.py", "DEFAULT_PROFILE_NAME"):
        "规格真值：直连档名派生自 spec 的 presets.direct.name，由 test_unit_spec 锚定",
    ("test_unit_profile.py", "MAX_COMBO_STEPS"):
        "规格真值：夹取后段数等于 spec 的 combo.max_steps，由 test_unit_spec 锚定",
    ("test_unit_spec.py", "COMBO_DOC"):
        "规格真值：本文件即以 spec.json 为真值来源，逐字重建后比对",
    ("test_unit_spec.py", "PARAM_NOTES"):
        "规格真值：本文件即以 spec.json 为真值来源",
    ("test_unit_spec.py", "SPEC_HASH"):
        "规格真值：对 spec.json 原始字节做 sha256 取前 8 位，与实现无关",
    ("test_unit_sysproxy.py", "SYSPROXY_OVERRIDE"):
        "产品决策：断言的是「我们写进去的就是这个值」，字符串内容本身是本地回环放行规则",
}

# 公式镜像登记：(文件, 行号) → 依据。行号变动会失效，用来提醒重新看一遍。
FORMULAS = {
    ("test_unit_engine.py", 172):
        "实测真值：cap = rate × BURST_SEC 的换算，BIT/字节与 BURST_SEC 由同文件字面量与 _t_spec_parity 跨端对拍锚定",
}


# ---------------------------------------------------------------- 源码符号

def load_symbols():
    src = io.open(str(SRC), encoding="utf-8").read()
    tree = ast.parse(src)
    funcs = set(re.findall(r"^(?:async )?def ([a-zA-Z_]\w*)\(", src, re.M))
    consts = set()
    for n in tree.body:
        if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name):
            nm = n.targets[0].id
            if re.fullmatch(r"[A-Z][A-Z0-9_]{2,}", nm):
                consts.add(nm)
    for n in tree.body:
        if isinstance(n, ast.ClassDef) and n.name == "G":
            for b in n.body:
                if isinstance(b, ast.Assign) and len(b.targets) == 1 and isinstance(b.targets[0], ast.Name):
                    nm = b.targets[0].id
                    if re.fullmatch(r"[a-z_]{3,}", nm):
                        consts.add("G." + nm)
    return funcs, consts


FUNCS, CONSTS = load_symbols()


def _w_attr(node):
    """node 是 W.X / weaknet.X 时返回 X。"""
    if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
            and node.value.id in ("W", "weaknet")):
        return node.attr
    return None


def w_symbol(node):
    """整棵子树恰好是「被测符号」时返回名字：W.常量 / W.函数() / 裸函数()。"""
    nm = _w_attr(node)
    if nm and nm in CONSTS:
        return nm
    if isinstance(node, ast.Call):
        f = node.func
        nm = _w_attr(f)
        if nm and nm in FUNCS:
            return nm
        if isinstance(f, ast.Name) and f.id in FUNCS:
            return f.id
    return None


def has_weaknet_ref(node):
    for x in ast.walk(node):
        if isinstance(x, ast.Attribute) and isinstance(x.value, ast.Name) \
                and x.value.id in ("W", "G", "weaknet"):
            return True
        if isinstance(x, ast.Name) and x.id in FUNCS:
            return True
    return False


def independent(node):
    """这一侧能不能独立给出期望值。

    判据：不含被测实现引用，且不是裸变量/下标（那通常是被测输出），
    并且至少含一个字面量、调用或运算 —— 即「自己算出来的」。
    """
    if has_weaknet_ref(node):
        return False
    if isinstance(node, ast.Constant):
        return True
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return all(independent(e) for e in node.elts)
    if isinstance(node, ast.Dict):
        return all(independent(v) for v in node.values if v is not None)
    if isinstance(node, ast.BinOp):
        return independent(node.left) and independent(node.right)
    if isinstance(node, ast.UnaryOp):
        return independent(node.operand)
    if isinstance(node, ast.Compare):
        return True
    if isinstance(node, (ast.ListComp, ast.DictComp, ast.SetComp, ast.GeneratorExp)):
        return True
    if isinstance(node, ast.Call):
        return all(independent(a) for a in node.args)
    return False


def _root_name(node):
    while isinstance(node, ast.Attribute):
        node = node.value
    return node.id if isinstance(node, ast.Name) else ""


def has_const_ref(node):
    """子树里有没有引用 weaknet 的常量（含 W.Bucket.BURST_SEC 这种多级属性）。"""
    return any(isinstance(x, ast.Attribute) and _root_name(x) in ("W", "weaknet")
               for x in ast.walk(node))


def formula_mirror(node):
    """断言里有没有「实现常量参与乘除」——典型的抄实现公式。

    只看乘除本身是否含实现常量：`"😀" * 13` 这种构造输入的运算不算。
    """
    for x in ast.walk(node):
        if isinstance(x, ast.BinOp) and isinstance(x.op, (ast.Mult, ast.Div)):
            if has_const_ref(x):
                return True
    return False


# ---------------------------------------------------------------- 扫描

def scan_text(text):
    """对一段测试源码做检查，返回 (flags, assert 数)。flags: [(kind, lineno, key, detail)]"""
    tree = ast.parse(text)
    lines = text.splitlines()
    flags = []
    n_assert = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assert):
            continue
        n_assert += 1
        t = node.test
        if not (isinstance(t, ast.Compare) and len(t.ops) == 1):
            continue
        # X3 恒真
        if isinstance(t.ops[0], ast.Eq) and ast.dump(t.left) == ast.dump(t.comparators[0]):
            flags.append(("X3", node.lineno, "", lines[node.lineno - 1].strip()[:100]))
            continue
        if not isinstance(t.ops[0], ast.Eq):
            continue
        a, b = t.left, t.comparators[0]
        is_formula = formula_mirror(t)
        # 公式镜像只报 X6：它已经要求登记，不必再按普通镜像报一次，
        # 否则同一条断言要进两张表，维护成本翻倍且容易漏。
        if not is_formula:
            for x, y in ((a, b), (b, a)):
                sym = w_symbol(y)
                if not sym:
                    continue
                if independent(x):
                    continue
                flags.append(("X4", node.lineno, sym, lines[node.lineno - 1].strip()[:100]))
                break
        # X6 公式
        if is_formula:
            flags.append(("X6", node.lineno, "", lines[node.lineno - 1].strip()[:100]))
    return flags, n_assert


def scan(path):
    return scan_text(io.open(str(path), encoding="utf-8").read())


def check(verbose=False, tests_dir=None, sources=None, mirrors=None,
          formulas=None, root=None):
    """检查。参数可注入，便于用临时目录验证「它真的会红」。"""
    problems = []
    tests_dir = Path(tests_dir) if tests_dir else TESTS
    root = Path(root) if root else ROOT
    sources = SOURCES if sources is None else sources
    mirrors = MIRRORS if mirrors is None else mirrors
    formulas = FORMULAS if formulas is None else formulas

    tests = sorted(p.name for p in tests_dir.glob("test_*.py"))
    # X1 覆盖
    for fn in tests:
        if fn not in sources:
            problems.append("X1 %s 未在 SOURCES 登记真值来源" % fn)
    for fn in sources:
        if fn not in tests:
            problems.append("X5 SOURCES 里的 %s 已不存在" % fn)
    # X2 来源可解析
    for fn, srcs in sources.items():
        if not srcs:
            problems.append("X2 %s 的来源为空" % fn)
        for s in srcs:
            if s in TAGS:
                continue
            if re.match(r"^RFC \d{4}", s):
                continue
            if (root / s).exists():
                continue
            problems.append("X2 %s 的来源无法解析：%s" % (fn, s))
    # X4 / X6 登记
    seen = set()
    total = 0
    for fn in tests:
        flags, n = scan(tests_dir / fn)
        total += n
        for kind, ln, key, detail in flags:
            if kind == "X3":
                problems.append("X3 %s:%d 断言两侧完全相同" % (fn, ln))
            elif kind == "X4":
                seen.add((fn, key))
                if (fn, key) not in mirrors:
                    problems.append("X4 %s:%d 镜像断言未登记：%s | %s" % (fn, ln, key, detail))
            elif kind == "X6":
                seen.add((fn, ln))
                if (fn, ln) not in formulas:
                    problems.append("X6 %s:%d 公式镜像未登记 | %s" % (fn, ln, detail))
    # X5 白名单过期
    for fn, key in mirrors:
        if (fn, key) not in seen:
            problems.append("X5 MIRRORS 登记已过期：%s / %s" % (fn, key))
    for fn, ln in formulas:
        if (fn, ln) not in seen:
            problems.append("X5 FORMULAS 登记已过期：%s:%d" % (fn, ln))

    if verbose:
        print("模块                                用例  断言  来源")
        for fn in tests:
            _f, n = scan(tests_dir / fn)
            cnt = len(re.findall(r"^def test_", io.open(str(tests_dir / fn), encoding="utf-8").read(), re.M))
            print("  %-34s %4d  %4d  %s" % (fn, cnt, n, "、".join(sources.get(fn, ["（未登记）"]))))
        print("\n共 %d 个模块、%d 条断言；镜像登记 %d 组、公式登记 %d 组"
              % (len(tests), total, len(mirrors), len(formulas)))

    if problems:
        print("\n发现 %d 个问题：" % len(problems))
        for p in problems:
            print("  " + p)
        return 1
    print("期望来源检查通过：%d 个模块、%d 条断言、%d 组镜像均已登记来源"
          % (len(tests), total, len(mirrors)))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="测试期望来源检查")
    ap.add_argument("--list", action="store_true", help="打印各模块来源与断言数")
    args = ap.parse_args(argv)
    return check(verbose=args.list)


if __name__ == "__main__":
    sys.exit(main())
