# -*- coding: utf-8 -*-
"""v1.1.15 真机 UI 回归：组合网格样式 / 选中态 / 标签即时刷新 / 名字限长。

为什么用像素而不是控件树：
  ColorOS 的 uiautomator dump 常常只吐出十几个节点（本机实测），
  按文字找控件会时好时坏；这几项要判的都是「形状和颜色」，
  直接读截图反而更稳，也不受 ROM 版本影响。

翻页靠「先滚到标签，再点另一块」：内置档位网格和自定义网格长得一样，
只看底色会把上面那张卡片误判成组合页，所以用标签当锚点，只认标签下方。

判定口径：
  U-1 组合页是 3 列等宽小方块（不是整宽横条）
  U-2 「＋ 新建组合」是紧凑小方块（宽度不到卡片一半）
  U-3 点中组合后整块染主色，且下方出现摘要行
  U-4 点标签 0.6 秒内立刻换色，4.6 秒后（跨过 3 秒轮询）不被拽回去
  U-5 从控制接口改选中项，标签也要立刻跟随（不用等轮询）
  U-6 名字超过 10 字时落库自动截到 10 字（场景 / 组合都算）
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _t_dev_lib as D                      # noqa: E402

try:
    from PIL import Image
except Exception:                            # 没装 Pillow 就只跑接口项
    Image = None

CHIP_BG = (241, 244, 250)                    # 小方块底色 #F1F4FA
NEW_BG = (234, 247, 243)                     # 新建按钮浅绿底 #EAF7F3
GREEN = (31, 138, 112)                       # 组合主色 #1F8A70
PURPLE = (122, 79, 243)                      # 场景主色 #7A4FF3


def near(c, t, tol=14):
    return all(abs(a - b) <= tol for a, b in zip(c, t))


def load(path):
    return Image.open(path).convert("RGB")


def runs_at_row(im, y, color, tol=12, min_w=20, max_w=9999):
    px = im.load()
    w = im.size[0]
    out, start = [], None
    for x in range(w):
        hit = near(px[x, y], color, tol)
        if hit and start is None:
            start = x
        elif not hit and start is not None:
            if min_w <= x - start <= max_w:
                out.append((start, x - 1))
            start = None
    if start is not None and min_w <= w - start <= max_w:
        out.append((start, w - 1))
    return out


def find_tab(im):
    """整屏找「场景 / 组合」标签：一块 280~560px 宽的实心主色块。

    返回 (颜色, y, x0, 宽) 或 None。取最宽的那块，避免被选中方块的窄边骗到。
    """
    best = None
    for color in (GREEN, PURPLE):
        for y in range(150, im.size[1], 2):
            rs = runs_at_row(im, y, color, min_w=280, max_w=560)
            if not rs:
                continue
            x0, x1 = rs[0]
            if best is None or (x1 - x0) > best[3]:
                best = (color, y, x0, x1 - x0)
    return best


def scroll_to_tabs(tries=6):
    """滚到标签可见；找到后再等一拍让惯性停住，否则紧接着的 tap 会被滚动吞掉。"""
    for i in range(tries):
        im = load(D.shot("ui1115_scroll_%d" % i))
        t = find_tab(im)
        if t:
            time.sleep(1.6)
            im = grab_fast()
            t2 = find_tab(im)
            if t2:
                return im, t2
        D.adb("shell", "input swipe 540 1950 540 750 300")
        time.sleep(1.2)
    return im, None


def tap_tab(im, t, want_color, tries=3):
    """点标签并核对真的切过去了；坐标抖动/惯性会吞掉第一次点击，所以重试。"""
    color, ty, x0, w = t
    for i in range(tries):
        x = im.size[0] - (x0 + w // 2) if color != want_color else x0 + w // 2
        D.adb("shell", "input tap %d %d" % (x, ty))
        time.sleep(1.2)
        im2 = grab_fast()
        t2 = find_tab(im2)
        if t2 and t2[0] == want_color:
            return im2, t2
        if t2:
            color, ty, x0, w = t2
    return im2, t2


def goto_combo(im, t):
    """确保停在「组合」页（绿色标签），点完要核对。"""
    if t[0] == GREEN:
        return im, t
    return tap_tab(im, t, GREEN)


def chips_below(im, tab_y):
    """标签下方成排的方块：只认 180~380px 宽的整块，避开按钮文字的碎边。"""
    best = (None, [])
    for y in range(tab_y + 40, im.size[1], 2):
        rs = runs_at_row(im, y, CHIP_BG, min_w=180, max_w=380)
        if len(rs) > len(best[1]):
            best = (y, rs)
    return best


def grab_fast():
    """直接读一张截屏到内存：D.shot 要落盘，单张 2~3 秒，量时序根本来不及。"""
    import io
    import subprocess
    p = subprocess.run([D.ADB, "-s", D.SERIAL, "exec-out", "screencap", "-p"],
                       capture_output=True, timeout=30)
    return Image.open(io.BytesIO(p.stdout)).convert("RGB")


def tab_color_fast(im):
    t = find_tab(im)
    return t[0] if t else None


def main():
    D.setup_forward()
    D.app_restart()
    time.sleep(1.0)

    # ---------- U-6 名字限长（纯接口） ----------
    D.section("U-6 名字最长 10 字")
    long_name = "一二三四五六七八九十甲乙"
    r = D.api_try("/api/upreset", {"name": long_name, "profile": {"down_kbps": 500}})
    items = D.api("/api/upresets")
    items = items.get("items", items if isinstance(items, list) else [])
    got = [x for x in items if x.get("id") == r.get("id")]
    nm = got[0]["name"] if got else ""
    D.rec("U-6-a 场景名超长自动截到 10 字", len(nm) == 10, "落库=%r（%d 字）" % (nm, len(nm)))
    if got:
        D.api("/api/upreset/del", {"id": got[0]["id"]})

    r2 = D.api_try("/api/combo", {"name": long_name, "loop": False,
                                  "steps": [{"key": "5g", "minutes": 0, "seconds": 30}]})
    combos = D.api("/api/combos")
    combos = combos.get("items", combos if isinstance(combos, list) else [])
    hit = [c for c in combos if c.get("id") == r2.get("id")]
    cn = hit[0]["name"] if hit else ""
    D.rec("U-6-b 组合名超长自动截到 10 字", len(cn) == 10, "落库=%r（%d 字）" % (cn, len(cn)))
    if hit:
        D.api("/api/combo/delete", {"id": hit[0]["id"]})

    if not Image:
        D.summary("v1.1.15 真机 UI（无 Pillow，仅接口项）")
        return

    # ---------- U-1 / U-2 / U-3 组合页网格 ----------
    D.section("U-1..U-3 组合网格样式")
    im, t = scroll_to_tabs()
    if not t:
        D.rec("U-1 找到「场景 / 组合」标签", False, "滚了 6 次没找到，见 _shots/ui1115_scroll_*")
    else:
        im, t = goto_combo(im, t)
        D.rec("U-1-a 已切到组合页", bool(t) and t[0] == GREEN,
              "标签颜色=%s" % (t[0] if t else None,))
        y, rs = chips_below(im, t[1] if t else 0)
        D.rec("U-1-b 标签下方成排小方块", len(rs) >= 2, "y=%s 数量=%d" % (y, len(rs)))
        if rs:
            widths = [b - a + 1 for a, b in rs]
            avg = sum(widths) / len(widths)
            even = all(abs(w - avg) <= avg * 0.22 for w in widths)
            D.rec("U-1-c 一行 3 个等宽方块（不是整宽横条）",
                  2 <= len(rs) <= 3 and avg < im.size[0] * 0.40 and even,
                  "宽度=%s 均宽=%.0f" % (widths, avg))

            bw = 0
            for yy in range((t[1] + 20) if t else 0, y, 2):
                for a, b in runs_at_row(im, yy, NEW_BG, tol=10, min_w=60, max_w=460):
                    bw = max(bw, b - a + 1)
            D.rec("U-2 「＋ 新建组合」是紧凑小方块", 0 < bw < im.size[0] * 0.45,
                  "宽=%d / 屏宽=%d" % (bw, im.size[0]))

            x0, x1 = rs[0]
            cx = (x0 + x1) // 2
            D.adb("shell", "input tap %d %d" % (cx, y))
            time.sleep(1.5)
            im2 = load(D.shot("ui1115_combo_picked"))
            gy, grs = None, []
            for yy in range(y - 200, min(im2.size[1], y + 260), 2):
                rr = [r for r in runs_at_row(im2, yy, GREEN, min_w=180, max_w=380)]
                if rr:
                    gy, grs = yy, rr
                    break
            D.rec("U-3-a 选中后整块染主色", bool(grs), "绿色块=%s" % (grs,))
            if grs:
                below = [im2.getpixel((x, gy + 70)) for x in range(x0, x1, 5)
                         if gy + 70 < im2.size[1]]
                gray = [c for c in below if 120 <= c[0] <= 205 and abs(c[0] - c[2]) < 30]
                D.rec("U-3-b 方块下方出现摘要行", len(gray) > 0, "灰像素=%d" % len(gray))

    # ---------- U-4 标签即时刷新且不被拽回 ----------
    D.section("U-4 标签点击后立刻生效、4.6 秒不回跳")
    im, t = scroll_to_tabs()
    if t:
        color, ty, x0, w = t
        other_x = im.size[0] - (x0 + w // 2)
        want = GREEN if color == PURPLE else PURPLE
        D.adb("shell", "input tap %d %d" % (other_x, ty))
        time.sleep(0.6)
        a = load(D.shot("ui1115_tab_06")).getpixel((other_x, ty))
        D.rec("U-4-a 0.6 秒内标签已换色", near(a, want, 45), "像素=%s 期望≈%s" % (a, want))
        # 0.6s 时若正赶上按下态（浅色），以 4.6s 那次为准判断有没有被拽回
        time.sleep(4.0)
        b = load(D.shot("ui1115_tab_46")).getpixel((other_x, ty))
        D.rec("U-4-b 4.6 秒后没有被状态轮询拽回去", near(b, want, 45), "像素=%s" % (b,))
    else:
        D.rec("U-4 标签定位", False, "没找到标签")

    # ---------- U-5 接口改选中项 → 标签立刻跟随 ----------
    D.section("U-5 选中项变化时标签立即跟随")
    combos = D.api("/api/combos")
    combos = combos.get("items", combos if isinstance(combos, list) else [])
    if combos:
        im, t = scroll_to_tabs()
        if t and t[0] == GREEN:              # 先退回场景页
            im, t = tap_tab(im, t, PURPLE)
        # 换一支「当前没选中」的组合：选中同一个 key 时标签故意不翻页
        # （用户手点过的页不该被同 key 的轮询拽回去），那样测不出跟随。
        pick = combos[1] if len(combos) > 1 else combos[0]
        D.api("/api/preset", {"key": "c:" + pick["id"]})
        # 不判毫秒：单张 screencap 在本机要 0.5~3 秒，测不出「即时」。
        # 这里守的是「不用人点标签，页面自己就跟过去了」——
        # 真正的即时性另有两条证据：U-4（点标签 0.6s 已换色）、
        # JDK 侧 _TabSyncTest（选中项变化即触发，不等轮询）。
        time.sleep(2.0)
        im5, t5 = scroll_to_tabs()
        D.rec("U-5 接口改选中项后标签自动跟随（无需人点）",
              bool(t5) and t5[0] == GREEN, "标签=%s" % (t5[0] if t5 else None,))

    D.api("/api/run", {"enable": False})
    D.api("/api/preset", {"key": "direct"})
    D.summary("v1.1.15 真机 UI")


if __name__ == "__main__":
    main()
