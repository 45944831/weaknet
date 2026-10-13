# -*- coding: utf-8 -*-
"""生成 WeakNet 统一图标：深蓝渐变圆角底 + 白色闪电 + 弱信号条。

产出：
  assets/icon.png                     512 主图（Windows 用）
  assets/weaknet.ico                  多尺寸 ico（EXE 图标）
  android/res/mipmap-*/ic_launcher.png           Android 各密度图标
  android/res/drawable/ic_launcher_foreground.png  自适应图标前景（透明）
  android/res/drawable/ic_launcher_background.png  自适应图标背景
用法：  python gen_icon.py
"""
import os
from PIL import Image, ImageDraw

BASE = os.path.dirname(os.path.abspath(__file__))
OUT_PNG = os.path.join(BASE, "assets")
RES = os.path.join(BASE, "android", "res")

C_TOP = (45, 127, 249)      # #2d7ff9
C_BOT = (22, 78, 196)       # #164ec4
WHITE = (255, 255, 255, 255)
WHITE_SOFT = (255, 255, 255, 110)

# 闪电（归一化坐标）
BOLT = [(0.56, 0.13), (0.27, 0.50), (0.45, 0.50), (0.37, 0.67),
        (0.67, 0.41), (0.48, 0.41), (0.61, 0.13)]
# 弱信号条：(中心x, 顶部y, 底部y, 半宽, alpha)
BARS = [(0.40, 0.795, 0.865), (0.50, 0.755, 0.865), (0.60, 0.715, 0.865)]
BAR_W = 0.048


def gradient(size, c1, c2):
    img = Image.new("RGBA", (size, size))
    d = ImageDraw.Draw(img)
    for y in range(size):
        t = y / max(1, size - 1)
        col = tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(3)) + (255,)
        d.line([(0, y), (size, y)], fill=col)
    return img


def rounded_mask(size, ratio=0.22):
    m = Image.new("L", (size, size), 0)
    ImageDraw.Draw(m).rounded_rectangle(
        [0, 0, size - 1, size - 1], radius=int(size * ratio), fill=255)
    return m


def draw_icon(size, radius_ratio=0.22, bar_alpha=(110, 170, 255), transparent_bg=False):
    """画完整图标。transparent_bg=True 时只画前景（自适应图标用）"""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    if not transparent_bg:
        bg = gradient(size, C_TOP, C_BOT)
        img.paste(bg, (0, 0), rounded_mask(size, radius_ratio))
    d = ImageDraw.Draw(img)

    px = lambda p: (p[0] * size, p[1] * size)
    d.polygon([px(p) for p in BOLT], fill=WHITE)

    for i, (cx, y0, y1) in enumerate(BARS):
        a = bar_alpha[i]
        x0 = (cx - BAR_W) * size
        x1 = (cx + BAR_W) * size
        d.rectangle([x0, y0 * size, x1, y1 * size],
                    fill=(255, 255, 255, a))
    return img


def ensure(p):
    os.makedirs(p, exist_ok=True)


def main():
    ensure(OUT_PNG)

    # 1) 主图 512
    icon = draw_icon(512)
    icon.save(os.path.join(OUT_PNG, "icon.png"))
    print("assets/icon.png")

    # 2) Windows ICO（多尺寸）
    sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    icon.save(os.path.join(OUT_PNG, "weaknet.ico"), sizes=sizes)
    print("assets/weaknet.ico")

    # 3) Android legacy mipmap
    mip = {"mdpi": 48, "hdpi": 72, "xhdpi": 96, "xxhdpi": 144, "xxxhdpi": 192}
    for k, s in mip.items():
        p = os.path.join(RES, "mipmap-" + k)
        ensure(p)
        draw_icon(s).save(os.path.join(p, "ic_launcher.png"))
    print("android/res/mipmap-*")

    # 4) Android 自适应图标（108dp canvas = 432px @xxhdpi，安全区 72dp = 288px）
    d = os.path.join(RES, "drawable")
    ensure(d)
    bg = gradient(432, C_TOP, C_BOT)
    bg.save(os.path.join(d, "ic_launcher_background.png"))
    fg = Image.new("RGBA", (432, 432), (0, 0, 0, 0))
    # 前景内容压到中心安全区（约 68%）后再放大一点，兼顾视觉重量
    inner = draw_icon(432, radius_ratio=0.0, transparent_bg=True)
    inner = inner.crop((0, 0, 432, 432))
    small = inner.resize((294, 294), Image.LANCZOS)
    fg.paste(small, ((432 - 294) // 2, (432 - 294) // 2), small)
    fg.save(os.path.join(d, "ic_launcher_foreground.png"))
    print("android/res/drawable/ic_launcher_{foreground,background}.png")

    # 5) adaptive-icon XML（API 26+）
    v26 = os.path.join(RES, "mipmap-anydpi-v26")
    ensure(v26)
    with open(os.path.join(v26, "ic_launcher.xml"), "w", encoding="utf-8") as f:
        f.write('<?xml version="1.0" encoding="utf-8"?>\n'
                '<adaptive-icon xmlns:android="http://schemas.android.com/apk/res/android">\n'
                '    <background android:drawable="@drawable/ic_launcher_background" />\n'
                '    <foreground android:drawable="@drawable/ic_launcher_foreground" />\n'
                '</adaptive-icon>\n')
    print("android/res/mipmap-anydpi-v26/ic_launcher.xml")


if __name__ == "__main__":
    main()
