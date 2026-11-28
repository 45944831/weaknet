# -*- coding: utf-8 -*-
"""期望来源检查器自身的用例。

一个「防线」如果不能证明自己会失败，它就只是装饰。这里用临时目录构造
各种坏味道的测试代码，确认 _t_expect.py 每一条规则都会红；最后再用真实
仓库跑一遍，防止登记表腐烂。
"""
import importlib.util
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parent.parent


def _load():
    spec = importlib.util.spec_from_file_location("_t_expect", ROOT / "tests" / "_t_expect.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


EXP = _load()


def _write(tmp_path, name, body):
    (tmp_path / name).write_text(body, encoding="utf-8")
    return tmp_path


def _run(tmp_path, sources, mirrors=None, formulas=None, root=None):
    return EXP.check(tests_dir=tmp_path, sources=sources,
                     mirrors=mirrors or {}, formulas=formulas or {},
                     root=root or tmp_path)


# ------------------------------------------------------------ 六条规则

def test_clean_file_passes(tmp_path, capsys):
    _write(tmp_path, "test_ok.py", "def test_a():\n    assert 1 + 1 == 2\n")
    assert _run(tmp_path, {"test_ok.py": ["产品决策"]}) == 0
    assert "通过" in capsys.readouterr().out


def test_unregistered_module_is_rejected(tmp_path, capsys):
    """新加的测试文件必须说明真值来源，否则没人知道它凭什么算对。"""
    _write(tmp_path, "test_new.py", "def test_a():\n    assert 1 + 1 == 2\n")
    assert _run(tmp_path, {}) == 1
    assert "X1" in capsys.readouterr().out


def test_unknown_source_is_rejected(tmp_path, capsys):
    _write(tmp_path, "test_ok.py", "def test_a():\n    assert 1 + 1 == 2\n")
    assert _run(tmp_path, {"test_ok.py": ["docs/没有这个文件.md"]}) == 1
    assert "X2" in capsys.readouterr().out


def test_source_must_exist_on_disk(tmp_path):
    """写一个真实存在的文件路径就通过 —— 路径是相对仓库根解析的。"""
    _write(tmp_path, "test_ok.py", "def test_a():\n    assert 1 + 1 == 2\n")
    (tmp_path / "note.md").write_text("x", encoding="utf-8")
    assert _run(tmp_path, {"test_ok.py": ["note.md"]}, root=tmp_path) == 0


def test_tautology_is_rejected(tmp_path, capsys):
    """两侧一模一样：无论实现怎么错它都绿。"""
    _write(tmp_path, "test_bad.py", "import weaknet as W\n\n"
                                    "def test_a():\n    assert W.NAME_MAX == W.NAME_MAX\n")
    assert _run(tmp_path, {"test_bad.py": ["产品决策"]}) == 1
    assert "X3" in capsys.readouterr().out


def test_mirror_is_rejected_when_unregistered(tmp_path, capsys):
    """接口输出和实现常量比：只能证明值传到了，不能证明值对。"""
    _write(tmp_path, "test_bad.py",
           "import weaknet as W\n\n"
           "def test_a(body):\n    assert body['n'] == W.NAME_MAX\n")
    assert _run(tmp_path, {"test_bad.py": ["产品决策"]}) == 1
    out = capsys.readouterr().out
    assert "X4" in out and "NAME_MAX" in out


def test_mirror_passes_when_registered(tmp_path):
    _write(tmp_path, "test_ok.py",
           "import weaknet as W\n\n"
           "def test_a(body):\n    assert body['n'] == W.NAME_MAX\n")
    ok = _run(tmp_path, {"test_ok.py": ["产品决策"]},
              mirrors={("test_ok.py", "NAME_MAX"): "管线回显：值由别处锚定"})
    assert ok == 0


def test_stale_registry_is_rejected(tmp_path, capsys):
    """白名单里躺着一条代码里已经没有的登记 —— 说明它已经不再保护任何东西。"""
    _write(tmp_path, "test_ok.py", "def test_a():\n    assert 1 + 1 == 2\n")
    assert _run(tmp_path, {"test_ok.py": ["产品决策"]},
                mirrors={("test_ok.py", "NAME_MAX"): "过期条目"}) == 1
    assert "X5" in capsys.readouterr().out


def test_formula_copy_is_rejected(tmp_path, capsys):
    """期望值用实现常量重新算一遍：上限一改，两边一起变。"""
    _write(tmp_path, "test_bad.py",
           "import weaknet as W\n\n"
           "def test_a():\n"
           "    assert W.step_seconds({'minutes': 9999}) == W.STEP_MAX_MINUTES * 60\n")
    assert _run(tmp_path, {"test_bad.py": ["产品决策"]}) == 1
    assert "X6" in capsys.readouterr().out


def test_formula_passes_when_registered(tmp_path):
    _write(tmp_path, "test_ok.py",
           "import weaknet as W\n\n"
           "def test_a():\n"
           "    assert W.step_seconds({'minutes': 9999}) == W.STEP_MAX_MINUTES * 60\n")
    assert _run(tmp_path, {"test_ok.py": ["产品决策"]},
                formulas={("test_ok.py", 4): "实测：换算是被测行为本身"}) == 0


# ------------------------------------------------------------ 不该误报的写法

def test_literal_expectation_is_not_a_mirror(tmp_path):
    """字面量期望是独立的：实现改了它就红，这正是我们要的。"""
    _write(tmp_path, "test_ok.py",
           "import weaknet as W\n\n"
           "def test_a():\n    assert W.NAME_MAX == 10\n")
    assert _run(tmp_path, {"test_ok.py": ["产品决策"]}) == 0


def test_spec_derived_expectation_is_not_a_mirror(tmp_path):
    """从 spec 推导出来的期望是独立真值，不能算抄实现。"""
    _write(tmp_path, "test_ok.py",
           "import weaknet as W\n\n"
           "def test_a(spec):\n"
           "    assert W.PROF_LIMITS == {p['key']: int(p['max']) for p in spec['params']}\n")
    assert _run(tmp_path, {"test_ok.py": ["规格真值"]}) == 0


def test_computed_input_is_not_a_formula_mirror(tmp_path):
    """构造输入用的乘法（"x" * 13）不是抄公式，不该报。"""
    _write(tmp_path, "test_ok.py",
           "import weaknet as W\n\n"
           "def test_a():\n"
           "    assert len(W.limit_item_name('x' * 13)) == 10\n")
    assert _run(tmp_path, {"test_ok.py": ["产品决策"]}) == 0


# ------------------------------------------------------------ 真实仓库

def test_real_repo_registry_is_clean():
    """真实仓库必须通过：防止有人删了登记、加了新测试却忘了登记。"""
    assert EXP.check() == 0


def test_every_source_tag_is_defined():
    """SOURCES 里用到的标签必须在 TAGS 里定义过，避免拼写不同却静默放过。"""
    used = {s for srcs in EXP.SOURCES.values() for s in srcs}
    unknown = {s for s in used if not s.startswith("RFC ")
               and "/" not in s and "." not in s and s not in EXP.TAGS}
    assert unknown == set(), unknown
