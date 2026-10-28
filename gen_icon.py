# -*- coding: utf-8 -*-
"""生成 WeakNet 统一图标（EXE 与 APK 共用一套图形，改这里即可）。

产出：
  assets/icon.png                                  1024 主图
  assets/weaknet.ico                               10 档尺寸（EXE 文件图标）
  android/res/mipmap-*/ic_launcher.png              Android 各密度（旧版）
  android/res/drawable/ic_launcher_foreground.png   自适应图标前景（透明）
  android/res/drawable/ic_launcher_background.png   自适应图标背景
用法：  python gen_icon.py

设计：深蓝→靛的圆角方 + 四根白色信号格，**第四格中间缺了一块**。
- 保留原图标的“信号格”识别度，去掉泛滥的闪电（那是测速 App 的语言，不是弱网模拟）
- 缺一块 = 网络质量少了一档，一眼看出“弱”；不是“断开”（断开是另一回事，本工具也能模拟）
- 全部单色粗笔画，16px 任务栏/通知栏仍可辨

用法：
    python make_icon.py             # 生成 icon.png(1024) / weaknet.ico / Android 各密度
    python make_icon.py wave        # 换成备选方案（W 波形）
    python make_icon.py --preview   # 只出候选对比图
"""
import os
import struct
import sys

from PIL import Image, ImageDraw, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))      # 仓库根
ASSETS = os.path.join(HERE, "assets")
ANDROID_RES = os.path.join(HERE, "android", "res")

# 品牌色：蓝 → 靛 → 深靛，135° 斜向
C_TOP = (37, 99, 235)      # #2563EB
C_MID = (67, 56, 202)      # #4338CA
C_BOT = (49, 46, 129)      # #312E81
WHITE = (255, 255, 255, 255)

SS = 4          # 超采样倍数
RADIUS = 0.21   # 圆角半径（占边长比例）

# 信号格几何（0..1 归一化）
BW = 0.130      # 每格宽
STEP = 0.190    # 格间距（含自身宽度）
X0 = 0.150      # 第一格左边缘
BOT = 0.775     # 底边（四格对齐）
TOPS = (0.520, 0.405, 0.290)   # 前三格顶边，逐格升高
G4_TOP = 0.180  # 第四格若完整时的顶边（最高的一格）
G4_GAP = (0.395, 0.500)        # 第四格缺掉的一段（上沿, 下沿）


# ---------------------------------------------------------------- 背景

def _gradient(size):
    """135° 三停渐变。低分辨率算完再放大，边缘天然平滑。"""
    n = 192
    small = Image.new("RGB", (n, n))
    px = small.load()
    for y in range(n):
        for x in range(n):
            t = (x + y) / (2.0 * (n - 1))
            if t < 0.5:
                k, a, b = t / 0.5, C_TOP, C_MID
            else:
                k, a, b = (t - 0.5) / 0.5, C_MID, C_BOT
            px[x, y] = (int(a[0] + (b[0] - a[0]) * k),
                        int(a[1] + (b[1] - a[1]) * k),
                        int(a[2] + (b[2] - a[2]) * k))
    return small.resize((size, size), Image.BICUBIC)


def background(size, rounded=True):
    img = _gradient(size).convert("RGBA")
    # 左上角一层极淡高光，避免整块纯平
    hl = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(hl)
    r = int(size * 1.05)
    d.ellipse([-int(size * 0.45), -int(size * 0.55), r, int(size * 0.85)], fill=255)
    hl = hl.filter(ImageFilter.GaussianBlur(size * 0.12)).point(lambda v: int(v * 0.10))
    img = Image.composite(Image.new("RGBA", (size, size), WHITE), img, hl)
    if rounded:
        mask = Image.new("L", (size, size), 0)
        ImageDraw.Draw(mask).rounded_rectangle(
            [0, 0, size - 1, size - 1], radius=int(size * RADIUS), fill=255)
        img.putalpha(mask)
    return img


# ---------------------------------------------------------------- 图形

def _bar(d, size, x, top, bottom, fill=WHITE):
    h = BW * size / 2
    cx = x * size + h
    d.rounded_rectangle([cx - h, top * size, cx + h, bottom * size], radius=h, fill=fill)


def glyph_bars(d, size):
    """信号格 + 断格（主方案）。"""
    for x, top in zip([X0 + i * STEP for i in range(3)], TOPS):
        _bar(d, size, x, top, BOT)
    x4 = X0 + 3 * STEP
    _bar(d, size, x4, G4_GAP[1], BOT)      # 下半截
    _bar(d, size, x4, G4_TOP, G4_GAP[0])   # 上半截（中间缺一块）


def glyph_wave(d, size):
    """备选：一个粗笔画 W（WeakNet 首字母），本身即波形。"""
    w = int(size * 0.108)
    seq = [(0.150, 0.300), (0.330, 0.715), (0.500, 0.360), (0.670, 0.715), (0.855, 0.300)]
    p = [(x * size, y * size) for x, y in seq]
    d.line(p, fill=WHITE, width=w, joint="curve")
    for x, y in (p[0], p[-1]):
        d.ellipse([x - w / 2, y - w / 2, x + w / 2, y + w / 2], fill=WHITE)


VARIANTS = {"bars": glyph_bars, "wave": glyph_wave}
DEFAULT = "bars"


# ---------------------------------------------------------------- 组装

def render(variant, size):
    s = size * SS
    img = background(s)
    layer = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    VARIANTS[variant](ImageDraw.Draw(layer), s)
    return Image.alpha_composite(img, layer).resize((size, size), Image.LANCZOS)


# ---------------------------------------------------------------- 输出

ICO_SIZES = [16, 20, 24, 32, 40, 48, 64, 96, 128, 256]
MIPMAPS = {"mdpi": 48, "hdpi": 72, "xhdpi": 96, "xxhdpi": 144, "xxxhdpi": 192}


def write_ico(path, variant):
    """手写 ICO：每档独立渲染后 LANCZOS 缩到目标尺寸，PNG 压缩条目（Vista+ 支持）。"""
    blobs = []
    tmp = os.path.join(ASSETS, "_ico_tmp.png")
    for s in ICO_SIZES:
        render(variant, s).save(tmp, "PNG", optimize=True)
        with open(tmp, "rb") as f:
            blobs.append((s, f.read()))
    os.remove(tmp)

    n = len(blobs)
    offset = 6 + 16 * n
    entries = data = b""
    for s, blob in blobs:
        dim = 0 if s >= 256 else s
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(blob), offset)
        offset += len(blob)
        data += blob
    with open(path, "wb") as f:
        f.write(struct.pack("<HHH", 0, 1, n) + entries + data)


def write_android(variant):
    for dpi, px in MIPMAPS.items():
        d = os.path.join(ANDROID_RES, "mipmap-" + dpi)
        os.makedirs(d, exist_ok=True)
        render(variant, px).save(os.path.join(d, "ic_launcher.png"), "PNG", optimize=True)

    # 自适应图标：108dp 画布，前景图形画在中央 70dp（可见区 72dp 圆内，留一点余量）
    fg_px = 432
    inner = int(fg_px * 70.0 / 108.0)
    tmp = Image.new("RGBA", (inner, inner), (0, 0, 0, 0))
    VARIANTS[variant](ImageDraw.Draw(tmp), inner)
    fg = Image.new("RGBA", (fg_px, fg_px), (0, 0, 0, 0))
    fg.paste(tmp, ((fg_px - inner) // 2, (fg_px - inner) // 2), tmp)
    fg.save(os.path.join(ANDROID_RES, "drawable", "ic_launcher_foreground.png"),
            "PNG", optimize=True)
    background(fg_px, rounded=False).save(
        os.path.join(ANDROID_RES, "drawable", "ic_launcher_background.png"),
        "PNG", optimize=True)


def preview():
    names = list(VARIANTS)
    cell, pad = 300, 20
    W = len(names) * (cell + pad) + pad
    H = 2 * (cell + pad) + pad + 16 * 8 + 40
    sheet = Image.new("RGB", (W, H), (245, 246, 250))
    d = ImageDraw.Draw(sheet)
    for i, n in enumerate(names):
        x = pad + i * (cell + pad)
        d.text((x, 6), n, fill=(20, 20, 30))
        big = render(n, 256)
        sheet.paste(big, (x, 24), big)
        d.text((x, 290), "16px(x8) / 24px(x8) / 32px(x4)", fill=(20, 20, 30))
        xx = x
        for s, k in ((16, 8), (24, 8), (32, 4)):
            sm = render(n, s).resize((s * k, s * k), Image.NEAREST)
            sheet.paste(sm, (xx, 310))
            xx += s * k + 8
    out = os.path.join(ASSETS, "_icon_preview.png")
    sheet.save(out, "PNG")
    print(out)


if __name__ == "__main__":
    if "--preview" in sys.argv:
        preview()
        sys.exit(0)
    v = DEFAULT
    for a in sys.argv[1:]:
        if a in VARIANTS:
            v = a
    render(v, 1024).save(os.path.join(ASSETS, "icon.png"), "PNG", optimize=True)
    write_ico(os.path.join(ASSETS, "weaknet.ico"), v)
    write_android(v)
    print("variant=%s -> icon.png / weaknet.ico / android res" % v)
