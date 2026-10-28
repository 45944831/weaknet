# -*- coding: utf-8 -*-
"""真机（Android 13）测试支撑库。

统一封装：adb 调用、界面控件树解析与点击、截图、控制接口。
要点：
  * Git Bash 会把 /sdcard/... 转成 Git 安装目录下的路径（路径穿透），
    所有 adb 命令前必须 export MSYS_NO_PATHCONV=1 或在 python 里 os.environ 设置。
  * 部分 ROM 禁止 adb appops 授权（MANAGE_APP_OPS_MODES 缺失），只能走 UI 点击。
  * 控制接口走 adb forward tcp:18899 -> 设备 8899。
  * 设备与 adb 路径都不写死：先看环境变量 WEAKNET_SERIAL / WEAKNET_ADB，
    再退到「adb devices 只有一台」的自动探测，换机器不用改代码。
"""
import os
import sys

# 仓库根：本文件在 <repo>/tests/ 下，向上两级即仓库根。
# 用相对定位而不是写死绝对路径，换机器/换目录也能直接跑。
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

import json
import os
import re
import subprocess
import time
import urllib.request



os.environ["MSYS_NO_PATHCONV"] = "1"


PKG = "com.weaknet.app"
CTRL = "http://127.0.0.1:18899"
SHOT_DIR = os.path.join(ROOT, "_shots")


def _find_adb():
    """定位 adb：环境变量 -> PATH -> 常见安装位置。"""
    env = os.environ.get("WEAKNET_ADB")
    if env and os.path.exists(env):
        return env
    found = shutil_which("adb")
    if found:
        return found
    for p in (
        r"C:/Program Files (x86)/Android/android-sdk/platform-tools/adb.exe",
        r"C:/Program Files/Android/android-sdk/platform-tools/adb.exe",
        os.path.expanduser("~/AppData/Local/Android/Sdk/platform-tools/adb.exe"),
        os.path.expanduser("~/Library/Android/sdk/platform-tools/adb"),
        "/usr/bin/adb",
    ):
        if os.path.exists(p):
            return p
    return "adb"


def shutil_which(cmd):
    """shutil.which 的薄封装（保持本文件不依赖额外 import）。"""
    import shutil
    return shutil.which(cmd)


def _pick_serial():
    """选设备：环境变量优先；否则只在恰好一台在线时自动选它。

    多台在线时不猜 —— 猜错就会把测试打到另一台设备上，
    报错还难查，直接让使用者显式指定。
    """
    env = os.environ.get("WEAKNET_SERIAL")
    if env:
        return env
    try:
        out = subprocess.run([_find_adb(), "devices"], capture_output=True,
                             text=True, timeout=10).stdout
        devs = [l.split("\t")[0].strip() for l in out.splitlines()[1:]
                if l.strip() and "\tdevice" in l]
        if len(devs) == 1:
            return devs[0]
        if len(devs) > 1:
            raise RuntimeError(
                "检测到多台设备 %s，请设置环境变量 WEAKNET_SERIAL 指定目标机" % devs)
    except Exception:
        pass
    return ""


ADB = _find_adb()
SERIAL = _pick_serial()

R = []


# ---------- 断言记录 ----------
def rec(name, ok, detail=""):
    R.append((name, bool(ok)))
    print(("  PASS " if ok else "  FAIL ") + name + ("  | " + detail if detail else ""), flush=True)
    return ok


def section(t):
    print("\n=== " + t + " ===", flush=True)


def summary(title="汇总"):
    ok = sum(1 for _, o in R if o)
    bad = [n for n, o in R if not o]
    print("\n" + "=" * 60)
    print("%s：%d/%d 通过" % (title, ok, len(R)))
    if bad:
        print("未通过：")
        for b in bad:
            print("  - " + b)
    print("=" * 60)
    return ok, len(R), bad


# ---------- adb ----------
def adb(*args, timeout=120):
    p = subprocess.run([ADB, "-s", SERIAL] + list(args), capture_output=True,
                       text=True, encoding="utf-8", errors="ignore", timeout=timeout)
    return (p.stdout or "").strip(), (p.stderr or "").strip(), p.returncode


def overlay_windows():
    """当前盖在本 App 上面的悬浮窗（系统级窗口）列表。

    这类窗口（悬浮时钟、录屏工具等）不属于前台 App，但会盖在界面上，
    而且 **uiautomator 的控件树里根本看不到它们** —— 于是：
      * 点击落到悬浮窗上，被它吃掉，界面纹丝不动；
      * 脚本却「正常跑完」，看起来像功能坏了。
    实测踩过：设备上的毫秒悬浮时钟正好盖住「组合」标签页，
    点它十次没反应，最后靠截图才看出是被浮窗挡了。

    返回窗口列表（每项含包名），非空就说明当前有东西挡着。
    """
    out = sh("dumpsys window windows | grep -E 'Window\\{|mOwnerUid'")
    # 更直接：只看 TYPE_ 属于悬浮窗类型且当前可见的
    out = sh("dumpsys window | grep -E 'mCurrentFocus|mFocusedApp'")
    names = []
    for line in (out or "").splitlines():
        m = re.search(r"(?:Window|ActivityRecord)\{[^}]*\s+\S+\s+([A-Za-z0-9_.]+)/", line)
        if m:
            names.append(m.group(1))
    return [n for n in names if n and n != PKG]


def dismiss_overlays():
    """把挡在上面的第三方悬浮窗关掉。

    优先用「禁用用户」——悬浮窗 App 通常有守护机制，force-stop 会被拉回来，
    禁用则一劳永逸（只在测试期间有效，测完可以 pm enable 恢复）。
    """
    pkgs = set(overlay_windows())
    # 已知爱抢前台的常驻悬浮窗，主动一并处理
    pkgs |= {"com.wb.msclock"}
    for p in sorted(pkgs):
        if p in ("com.android.launcher", "com.android.systemui",
                 "com.android.settings", PKG):
            continue
        sh("am force-stop %s" % p)
        sh("pm disable-user %s" % p)
    return sorted(pkgs)


def ensure_foreground(retries=4):
    """保证本 App 在前台，返回是否成功。

    设备上常驻着别的 App（悬浮时钟、系统设置弹窗等），它们会时不时抢到前台。
    一旦前台被抢走，后续所有基于控件树的定位全部落空 —— 元素找不到、
    坐标点空，脚本却「正常跑完」，看起来像功能坏了，其实是环境问题。

    关键点：`am start` 只是把任务栈带到前台，launcher / 悬浮窗仍可能留着焦点。
    所以判定不能只看「是不是本 App 的任务」，要看 **焦点窗口** 是不是本 App，
    并且在拉起后等焦点稳定（拉起瞬间焦点往往还在旧窗口上）。
    """
    for attempt in range(retries):
        fg = foreground_pkg()
        if fg == PKG:
            return True
        # 焦点在别的 App：先把它压回后台（系统界面强停不了就跳过）
        if fg and fg != "com.android.launcher":
            sh("am force-stop %s" % fg)
            time.sleep(0.6)
        # 包被禁用时 am start / monkey 都会失败，先恢复启用
        ensure_enabled()
        # 拉起本 App；用 monkey 兜底（部分 ROM 上 am start 会被忽略）
        sh("am start -n %s/.MainActivity >/dev/null 2>&1" % PKG)
        time.sleep(1.6)
        if foreground_pkg() != PKG:
            sh("monkey -p %s -c android.intent.category.LAUNCHER 1 >/dev/null 2>&1" % PKG)
            time.sleep(1.8)
        # 焦点稳定：连续两次读到本 App 才算真的在前台
        for _ in range(3):
            if foreground_pkg() == PKG:
                time.sleep(0.5)
                if foreground_pkg() == PKG:
                    return True
            time.sleep(0.5)
    return foreground_pkg() == PKG


def sh(cmd, timeout=180):
    out, err, rc = adb("shell", cmd, timeout=timeout)
    return out


def shot(name):
    os.makedirs(SHOT_DIR, exist_ok=True)
    path = os.path.join(SHOT_DIR, name + ".png")
    p = subprocess.run([ADB, "-s", SERIAL, "exec-out", "screencap", "-p"],
                       capture_output=True, timeout=60)
    if p.stdout and len(p.stdout) > 1000:
        with open(path, "wb") as f:
            f.write(p.stdout)
        return path
    return ""


# ---------- 界面控件树 ----------
_NODE = re.compile(r'<node[^>]*?bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"[^>]*?/>')


def foreground_pkg():
    """当前前台窗口属于哪个包名（拿不到返回空串）。

    uiautomator dump 抓的是「当前前台窗口」的控件树 —— 如果前台是别的 App，
    dump 出来的就是那个 App 的界面，此时点任何坐标都没用（表现为
    「脚本跑完了，界面却一点没变」）。定位元素前必须先确认前台确实是本 App。
    """
    out = sh("dumpsys window | grep -E 'mCurrentFocus|mFocusedApp'")
    m = re.search(r"([A-Za-z0-9_.]+)/", out or "")
    return m.group(1) if m else ""


def dump(retries=3):
    """返回 [(text, desc, cls, bounds(x1,y1,x2,y2))]，bounds 为 None 表示解析失败。
    ColorOS 偶发 dump 到 launcher 或残缺树：节点过少就重试。"""
    xml = ""
    for _ in range(retries):
        adb("shell", "uiautomator dump /sdcard/_wn_ui.xml", timeout=60)
        xml, _, _ = adb("shell", "cat /sdcard/_wn_ui.xml", timeout=60)
        if xml.count("<node") > 5 and "com.android.launcher" not in xml[:2000]:
            break
        time.sleep(1.0)
    items = []
    for chunk in xml.split("</node>"):
        if "<node" not in chunk:
            continue
        def _g(k):
            m = re.search(k + r'="([^"]*)"', chunk)
            return m.group(1) if m else ""
        b = re.search(r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', chunk)
        items.append({
            "text": _g("text").replace("&#10;", "\n"),
            "desc": _g("content-desc"),
            "cls": _g("class"),
            "bounds": tuple(int(x) for x in b.groups()) if b else None,
            "click": "true" in _g("clickable"),
            "enabled": "true" in _g("enabled"),
        })
    return items


def find(text=None, contains=False, cls=None, cls_contains=False, nodes=None):
    nodes = nodes if nodes is not None else dump()
    res = []
    for n in nodes:
        if text is not None:
            ok = (text in n["text"]) if contains else (n["text"] == text)
            if not ok:
                continue
        if cls is not None:
            ok = (cls in n["cls"]) if cls_contains else (n["cls"] == cls)
            if not ok:
                continue
        res.append(n)
    return res


def tap_text(text, contains=True, cx=None, cy=None, wait=1.2, index=0):
    ns = find(text, contains=contains)
    if len(ns) <= index:
        return False
    n = ns[index]
    if not n["bounds"]:
        return False
    x1, y1, x2, y2 = n["bounds"]
    x, y = cx if cx else (x1 + x2) // 2, cy if cy else (y1 + y2) // 2
    adb("shell", "input tap %d %d" % (x, y))
    time.sleep(wait)
    return True


def tap_node(n, wait=1.2):
    if not n or not n["bounds"]:
        return False
    x1, y1, x2, y2 = n["bounds"]
    adb("shell", "input tap %d %d" % ((x1 + x2) // 2, (y1 + y2) // 2))
    time.sleep(wait)
    return True


def has_text(t):
    return len(find(t, contains=True)) > 0


def texts(nodes=None, limit=None):
    nodes = nodes if nodes is not None else dump()
    ts = [n["text"] for n in nodes if n["text"]]
    return ts if limit is None else ts[:limit]


# ---------- 控制接口 ----------
def api(path, data=None, timeout=15):
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(CTRL + path, data=body, method=("POST" if body else "GET"))
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with op.open(req, timeout=timeout) as r:
        raw = r.read()
    try:
        return json.loads(raw or b"{}")
    except Exception:
        return {"_raw": raw.decode("utf-8", "ignore")}


def api_try(path, data=None, timeout=15):
    try:
        return api(path, data, timeout)
    except Exception as e:
        return {"_err": str(e)}


def status():
    return api("/api/status")


def set_scene(key, wait=0.8):
    r = api("/api/preset", {"key": key})
    time.sleep(wait)
    return r


def vpn(on, wait=3.0):
    r = api("/api/vpn", {"enable": bool(on)})
    time.sleep(wait)
    return r


def listen(on, wait=2.0):
    r = api("/api/listen", {"on": bool(on)})
    time.sleep(wait)
    return r


def set_profile(**kw):
    return api("/api/profile", kw)


def is_vpn_up():
    s = status()
    return bool(s.get("vpn_active")), s


def ensure_enabled():
    """确认本 App 没被禁用，返回是否可用。

    包被禁用（enabled=3 / DISABLED_USER）时：
      * `am start` 报 "Activity class does not exist"
      * `monkey` 报 "No activities found to run"
      * `cmd package resolve-activity` 报 "No activity found"
    —— 全都看不出「包被禁用」这个真实原因，只会像「Activity 不存在」。
    所以每次开跑前先确认一遍 enabled 状态。
    """
    out = sh("dumpsys package %s | grep -m1 'User 0:'" % PKG) or ""
    m = re.search(r"enabled=(\d+)", out)
    if m and m.group(1) != "1":
        sh("pm enable %s" % PKG)
        time.sleep(1.0)
        return True
    return True


def app_restart(wait=4.0):
    """ColorOS 会把后台 App 切回桌面，且 uiautomator 可能 dump 到 launcher。
    强制重启后必须验证控件树里真的有本 App，失败重试。

    还有一个坑：设备上如果别的 App 抢了前台（弹窗、闹钟、悬浮时钟之类），
    uiautomator dump 抓到的就是那个 App 的界面 —— 元素定位全部落空，
    脚本却「正常跑完什么都没变」。所以这里额外校验前台窗口归属。
    """
    ensure_enabled()
    for attempt in range(3):
        sh("am force-stop %s" % PKG)
        time.sleep(1.0)
        sh("am start -n %s/.MainActivity >/dev/null 2>&1" % PKG)
        time.sleep(wait)
        # 前台被别的 App 抢走时，先把它压回后台再拉本 App
        fg = foreground_pkg()
        if fg and fg != PKG:
            sh("am force-stop %s" % fg)
            time.sleep(0.8)
            sh("am start -n %s/.MainActivity >/dev/null 2>&1" % PKG)
            time.sleep(wait)
        ns = dump()
        if any(PKG in (n.get("cls") or "") or "开始弱网测试" in n["text"] for n in ns):
            return True
        # 兜底：monkey 拉起
        sh("monkey -p %s -c android.intent.category.LAUNCHER 1 >/dev/null 2>&1" % PKG)
        time.sleep(wait)
        ns = dump()
        if any("开始弱网测试" in n["text"] for n in ns):
            return True
    return False


def in_foreground():
    """dump 是否是本 App（靠标志性文本判断，比 topResumedActivity 快）。"""
    ns = dump()
    marks = ("开始弱网测试", "停止弱网测试", "测试环境", "我的自定义", "弱网组合")
    return any(m in n["text"] for n in ns for m in marks)


def logcat_since(since_ms, *tags):
    """抓 logcat（默认带过滤），返回文本。"""
    cmd = "logcat -v time -t %d" % int(since_ms / 1000) if False else \
          "logcat -d -v time -T %d" % int(since_ms)
    return sh(cmd)


def clear_logcat():
    sh("logcat -c")


def pss_kb():
    out = sh("dumpsys meminfo %s" % PKG)
    m = re.search(r"TOTAL PSS:\s*(\d+)", out)
    return int(m.group(1)) if m else -1


def cpu_ticks():
    out = sh("cat /proc/%s/stat 2>/dev/null" % _pid())
    if not out or " " not in out:
        return None
    parts = out.rsplit(")", 1)[-1].split()
    # utime stime 位于 pid/stat 的第 14/15 个字段（0 基：12/13 after comm+state）
    try:
        return int(parts[11]) + int(parts[12])
    except Exception:
        return None


def _pid():
    out = sh("ps -A -o PID,NAME 2>/dev/null | grep %s || pidof %s" % (PKG, PKG))
    m = re.search(r"(\d{3,})", out)
    return m.group(1) if m else "0"


def setup_forward():
    out, _, _ = adb("forward", "tcp:18899", "tcp:8899")
    time.sleep(0.5)


def ensure_home():
    """回到主界面（连按返回键直到看见总开关）。"""
    for _ in range(4):
        if has_text("开始弱网测试") or has_text("停止弱网测试"):
            return True
        adb("shell", "input keyevent 4")
        time.sleep(0.6)
    return False
