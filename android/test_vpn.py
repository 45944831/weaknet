# -*- coding: utf-8 -*-
"""自动化点击 Android VPN 授权弹窗，并可选地打开一个 URL 产生真实流量"""
import re
import subprocess
import sys
import time

ADB = r"C:/Program Files (x86)/Android/android-sdk/platform-tools/adb.exe"


def sh(*args, **kw):
    return subprocess.run([ADB] + list(args), capture_output=True, timeout=kw.get("t", 60))


def wake():
    sh("shell", "input", "keyevent", "224")
    time.sleep(0.5)
    sh("shell", "wm", "dismiss-keyguard")


def dump_ui():
    sh("shell", "uiautomator", "dump", "/sdcard/wb_ui.xml")
    r = sh("shell", "cat", "/sdcard/wb_ui.xml")
    return r.stdout.decode("utf-8", "ignore")


def tap_text(xml, keywords):
    for node in re.findall(r"<node[^>]*>", xml):
        m_text = re.search(r'text="([^"]*)"', node)
        m_bounds = re.search(r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', node)
        if not m_bounds:
            continue
        text = m_text.group(1) if m_text else ""
        if any(k in text for k in keywords):
            x1, y1, x2, y2 = [int(v) for v in m_bounds.groups()]
            cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
            print("tap:", text, (cx, cy))
            sh("shell", "input", "tap", str(cx), str(cy))
            return True
    return False


def main():
    url = sys.argv[1] if len(sys.argv) > 1 else None
    wake()
    sh("shell", "am", "start", "-n", "com.weaknet.app/.MainActivity")
    time.sleep(2.5)
    xml = dump_ui()
    ok = tap_text(xml, ["确定", "OK", "允许", "Allow"])
    if not ok:
        print("未发现授权按钮（可能已授权或弹窗未出现）")
    time.sleep(2.0)
    # 若还有确认弹窗（部分 ROM 二次确认）
    xml2 = dump_ui()
    tap_text(xml2, ["确定", "OK", "允许", "Allow", "信任"])
    if url:
        sh("shell", "am", "start", "-a", "android.intent.action.VIEW", "-d", url)
        print("已打开:", url)


if __name__ == "__main__":
    main()
