"""静态检查：控制台 JS 语法 + 接管范围相关 DOM/接口约定是否齐全"""
import json
import re
import subprocess
import sys
import os

BASE = os.path.dirname(os.path.abspath(__file__))
HTML = os.path.join(BASE, "dashboard.html")
src = open(HTML, encoding="utf-8").read()

checks = []


def ck(name, cond):
    checks.append((name, bool(cond)))


# ---- 1. 提取 <script> 做语法检查 ----
m = re.search(r"<script>(.*?)</script>", src, re.S)
js = m.group(1) if m else ""
tmp = os.path.join(BASE, "_t_inline.js")
open(tmp, "w", encoding="utf-8").write(js)
node = subprocess.run(["node", "--check", tmp], capture_output=True, text=True)
ck("JS 语法 node --check", node.returncode == 0)
if node.returncode != 0:
    print(node.stderr[:400])

# ---- 2. 接管范围：主页面入口 + 二级界面的 DOM ----
for i in ["scopeCard", "scopeOpenBtn", "scopeBrief", "scopeTip",
          "scopePage", "scopeBackBtn", "scopeBackBtn2", "scopeCount",
          "scopeListHint", "scopeSearch", "scopeList", "scopeApplyBtn",
          "scopeReloadBtn", "scopeAllBtn", "scopeNoneBtn"]:
    ck("存在 #%s" % i, ('id="%s"' % i) in src)

# ---- 3. 三种模式的 radio ----
ck("三种模式 radio 齐全",
   all(('value="%s"' % v) in src for v in ("all", "only", "exclude")))

# ---- 4. 二级界面默认隐藏，且入口是按钮 ----
ck("二级界面默认隐藏", 'id="scopePage" style="display:none"' in src)
ck("主页面入口是按钮（不再是折叠标题）",
   'id="scopeOpenBtn"' in src and "scopeToggle" not in src)
ck("二级界面带返回按钮（顶部 + 底部两处）",
   "scopeBackBtn" in src and "scopeBackBtn2" in src)
ck("点击入口按钮打开二级界面", '$("scopeOpenBtn").onclick' in src)
ck("返回时丢弃未保存改动", "closeScopePage" in src and "scopeSnapshot" in src)
ck("保存后返回主页面", "closeScopePage(true)" in src)

# ---- 6. CSS ----
for c in [".scope-modes", ".scope-list", ".scope-row", ".scope-search"]:
    ck("CSS %s" % c, c in src)

for c in [".mychip", ".mc-del", ".my-empty", ".my-save"]:
    ck("CSS %s" % c, c in src)

# ---- 7. 接口调用约定 ----
ck("调用 /api/targets", '"/api/targets"' in src)
ck("读写 /api/scope", '"/api/scope"' in src)

# ---- 8. 自定义栏：保存 / 列出 / 删除 ----
for i in ["myBar", "myChips", "myEmpty", "myName", "saveMyBtn", "mySaveTip"]:
    ck("存在 #%s" % i, ('id="%s"' % i) in src)
ck("自定义栏默认隐藏（拿到列表后才显示）", 'id="myBar" style="display:none"' in src)
for p in ["/api/upresets", "/api/upreset", "/api/upreset/del"]:
    ck("调用 %s" % p, '"%s"' % p in src)
ck("自定义场景用 u:<id> 键应用",
   'data-key="u:' in src and '{key: b.dataset.key}' in src)
ck("init 里加载自定义栏", "await loadMy();" in src)
ck("未起名时不静默保存", "请先给这组参数起个名字" in src)

os.remove(tmp)

print("=== 控制台静态检查 ===")
ok = True
for n, v in checks:
    print(("  PASS  " if v else "  FAIL  ") + n)
    ok = ok and v
print("总体:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
