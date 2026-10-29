# -*- coding: utf-8 -*-
"""spec.json 单一真源的静态一致性用例。

规则的意图：参数、预设、文案、组合上限只允许存在一份（spec/spec.json）。
Python 运行时直接读它；Android 侧由 tools/gen_spec.py 生成 SpecGen.java。
任何一边「顺手改一下」，这里都会先红，而不是等到装了 APK 才发现两端不一样。
"""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

import weaknet as W

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parent.parent
SPEC_PATH = ROOT / "spec" / "spec.json"
SPECGEN = ROOT / "android" / "src" / "com" / "weaknet" / "app" / "SpecGen.java"
PROFILE_JAVA = ROOT / "android" / "src" / "com" / "weaknet" / "app" / "Profile.java"


def load_spec():
    return json.loads(SPEC_PATH.read_text(encoding="utf-8"))


def test_spec_hash_matches_file():
    raw = SPEC_PATH.read_bytes()
    assert W.SPEC_HASH == hashlib.sha256(raw).hexdigest()[:8]


def test_param_table_complete_and_ordered():
    spec = load_spec()
    expected = [(p["key"], p["label"], p.get("unit", ""), p["max"], p["default"])
                for p in spec["params"]]
    actual = [(p["key"], p["label"], p.get("unit", ""), p["max"], p["default"])
              for p in W.PARAM_SPECS]
    assert actual == expected
    assert len({p["key"] for p in W.PARAM_SPECS}) == len(W.PARAM_SPECS)
    assert W.PROF_LIMITS == {p["key"]: int(p["max"]) for p in spec["params"]}


def test_presets_and_notes_match_spec():
    spec = load_spec()
    assert list(W.PRESETS) == [p["key"] for p in spec["presets"]]
    for p in spec["presets"]:
        name, cat, params = W.PRESETS[p["key"]]
        assert name == p["name"]
        assert cat == p["cat"]
        assert {k: float(v) for k, v in params.items()} == \
            {k: float(v) for k, v in (p.get("params") or {}).items()}
    assert W.PRESET_NOTES == {k: v for k, v in spec["notes"].items() if v}
    assert W.PARAM_NOTES == spec["param_notes"]


def test_combo_doc_is_composed_from_spec():
    combo = load_spec()["combo"]
    parts = [combo["intro"]]
    for e in combo["examples"]:
        parts.append("\n".join([e["name"], e["desc"], e["effect"], e["steps"]]))
    parts.extend(combo["tips"])
    assert W.COMBO_DOC == "\x1f".join(parts)
    intro, examples, tips = W.combo_doc_parts()
    assert intro == combo["intro"]
    assert len(examples) == len(combo["examples"])
    assert tips == combo["tips"]


def test_limits_and_share_constants_match_spec():
    spec = load_spec()
    assert (W.SHARE_VER, W.PIN_LEN, W.MAX_INBOX, W.MAX_SHARED_STEPS) == \
        (spec["share"]["ver"], spec["share"]["pin_len"],
         spec["share"]["inbox_max"], spec["share"]["max_shared_steps"])
    assert (W.MAX_COMBO_STEPS, W.STEP_MAX_MINUTES, W.STEP_MAX_SECONDS) == \
        (spec["combo"]["max_steps"], spec["combo"]["max_minutes"],
         spec["combo"]["max_seconds"])


def test_specgen_is_in_sync_with_spec():
    proc = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "gen_spec.py"), "--check"],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert proc.returncode == 0, (proc.stdout + proc.stderr).strip()
    assert W.SPEC_HASH in SPECGEN.read_text(encoding="utf-8")


def test_profile_java_has_no_second_copy_of_data():
    src = PROFILE_JAVA.read_text(encoding="utf-8")
    assert "电梯" not in src
    assert "Wi-Fi 良好" not in src
    assert "PARAM_NOTES.put" not in src
