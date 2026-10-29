# -*- coding: utf-8 -*-
"""存量回归脚本的 pytest 入口。

`_t_*.py` 是历史验证资产：它们带着更细的源码解析比对（Java 端逐字对文案、
spec 与生成物逐项对），重写成 pytest 反而容易把断言写松。这里不重写，
只用子进程登记它们的退出码，让它们进入统一的 ``pytest -q``，
而不是靠人记「发版前要跑哪几个脚本」。

新增用例请写在 ``test_*.py``；这里只收「无交互、无真机依赖、稳定」的脚本。
"""
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
pytestmark = pytest.mark.unit

SCRIPTS = [
    ("param_notes", "_t_param_notes.py"),
    ("combo_notes", "_t_combo_notes.py"),
    ("spec_parity", "_t_spec_parity.py"),
    ("version", "_t_version.py"),
]


@pytest.mark.parametrize("script", [s[1] for s in SCRIPTS],
                         ids=[s[0] for s in SCRIPTS])
def test_golden_script(script):
    proc = subprocess.run(
        [sys.executable, str(HERE / script)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=180)
    output = (proc.stdout or "") + (proc.stderr or "")
    assert proc.returncode == 0, "退出码 %d\n%s" % (proc.returncode, output[-4000:])
