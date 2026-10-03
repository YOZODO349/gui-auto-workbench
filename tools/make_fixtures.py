# -*- coding: utf-8 -*-
"""造自测夹具：几页假界面 —— **不带任何记号**（新路线里画面一律由程序抓）。

用途：用户还没标够步骤之前，先把识别层与状态机整链验通
（负样本、缩放、整链、反向对照）。

⚠ 夹具配色**避开记号的绿/红掩码**（绿 ≈ 色相 45~85；红 ≈ 0~10 或 170~180）：
只有 1.6 远程素材路线才会把图喂给"提记号坐标"的工具，但顺手避开，
省得哪天夹具自己制造出"凭空多一个红圈"的假故障。

命令行导出（给人看）：
    .venv\\Scripts\\python.exe tools/make_fixtures.py
"""
from __future__ import annotations

import os
import sys

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

REF_W, REF_H = 1920, 1080

# 三页假界面：标题不同、按钮位置不同 —— 互斥性就靠这个
PAGE_SPECS = [
    dict(key="p1", title="HOME · 主界面", btn="开始任务", btn_pos=(960, 900)),
    dict(key="p2", title="TASKS · 任务列表", btn="领取奖励", btn_pos=(1690, 130)),
    dict(key="p3", title="SETTLE · 结算面板", btn="确认完成", btn_pos=(960, 880)),
]

_FONT_CACHE = {}


def _font(size: int):
    if size in _FONT_CACHE:
        return _FONT_CACHE[size]
    f = None
    for p in (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\msyhl.ttc",
              r"C:\Windows\Fonts\simhei.ttf"):
        if os.path.exists(p):
            try:
                f = ImageFont.truetype(p, size)
                break
            except Exception:
                continue
    if f is None:
        f = ImageFont.load_default()
    _FONT_CACHE[size] = f
    return f


def make_page(title: str, btn: str, btn_pos, size=(REF_W, REF_H),
              bg=(38, 42, 52), bar=(58, 66, 84), accent=(72, 104, 160),
              fg=(236, 238, 244), item=(48, 53, 66)):
    """画一页假界面（BGR，交给 OpenCV 用）。"""
    W, H = size
    img = Image.new("RGB", size, bg)
    d = ImageDraw.Draw(img)

    # 顶部标题条 —— 这就是"识别点"该点的那个独特元素
    d.rounded_rectangle([520, 40, 1400, 124], radius=10, fill=bar)
    d.text((560, 58), title, font=_font(40), fill=fg)

    # 左侧列表（装饰，增加真实感）
    for i in range(5):
        y = 240 + i * 70
        d.rounded_rectangle([120, y, 460, y + 48], radius=8, fill=item)
        d.text((150, y + 10), "条目 %d" % (i + 1), font=_font(24), fill=(172, 178, 192))

    # 主按钮
    x, y = btn_pos
    d.rounded_rectangle([x - 130, y - 34, x + 130, y + 34], radius=12, fill=accent)
    d.text((x - 104, y - 20), btn, font=_font(30), fill=(255, 255, 255))

    # 右下角版本号（每页都一样，不承担识别职能）
    d.text((W - 280, H - 62), "build 7 · fixture", font=_font(22), fill=(120, 126, 140))

    return cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)


def make_tone_page(text="限时活动", size=(900, 320), bg=(30, 30, 40),
                   color=(255, 90, 170)):
    """"背景会变、字色不变"的场景：粉字（H≈165，S 高）。"""
    img = Image.new("RGB", size, bg)
    d = ImageDraw.Draw(img)
    d.text((60, 118), text, font=_font(76), fill=color)
    return cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)


def make_noise(size=(900, 320), seed=7):
    """对抗样本：纯噪声 —— 色调匹配必须拒绝它。"""
    rng = np.random.default_rng(seed)
    return rng.integers(0, 256, size=(size[1], size[0], 3), dtype=np.uint8)


def make_solid(size=(900, 320), bgr=(170, 90, 255)):
    """对抗样本：纯色块 —— 同样必须拒绝（coverage 上界 + shape_iou 挡它）。"""
    return np.full((size[1], size[0], 3), bgr, dtype=np.uint8)


def scale_frame(frame, k: float):
    h, w = frame.shape[:2]
    return cv2.resize(frame, (int(round(w * k)), int(round(h * k))),
                      interpolation=cv2.INTER_AREA if k < 1 else cv2.INTER_CUBIC)


def build_all(bg_colors=None):
    """一次造齐自测要用的全部图。"""
    pages = {}
    for spec in PAGE_SPECS:
        pages[spec["key"]] = make_page(spec["title"], spec["btn"], spec["btn_pos"])

    # 同页不同背景（测抗背景变化；识别框只框标题条，所以背景变不该影响命中）
    variants = {}
    if bg_colors is None:
        bg_colors = [(38, 42, 52), (240, 236, 220), (24, 26, 34),
                     (206, 214, 226), (52, 40, 48), (28, 44, 40)]
    for i, bg in enumerate(bg_colors):
        variants["bg%d" % i] = make_page(PAGE_SPECS[0]["title"], PAGE_SPECS[0]["btn"],
                                         PAGE_SPECS[0]["btn_pos"], bg=bg)
    return {
        "pages": pages,
        "bg_variants": variants,
        "scaled": {k: scale_frame(v, 2560.0 / REF_W) for k, v in pages.items()},
        "tone": {
            "base": make_tone_page(bg=(30, 30, 40)),
            "bg_a": make_tone_page(bg=(240, 238, 230)),
            "bg_b": make_tone_page(bg=(20, 60, 90)),
            # ⚠ 背景色相**必须避开**目标字色（粉 H≈165）：同色相的背景下，
            # 掩码会把整块背景都当成笔画，四重证据必然不过 —— 这是色调匹配的固有边界。
            "bg_c": make_tone_page(bg=(28, 58, 104)),      # 深蓝 H≈103，避开了
        },
        "noise": make_noise(),
        "solid": make_solid(),
    }


def export(out_dir=None):
    from src import util
    out_dir = out_dir or os.path.join(ROOT, "samples", "fixtures")
    util.ensure_dir(out_dir)
    data = build_all()
    n = 0
    for k, v in data["pages"].items():
        util.imwrite_unicode(os.path.join(out_dir, "page_%s.png" % k), v)
        n += 1
    for k, v in data["bg_variants"].items():
        util.imwrite_unicode(os.path.join(out_dir, "page1_%s.png" % k), v)
        n += 1
    for k, v in data["scaled"].items():
        util.imwrite_unicode(os.path.join(out_dir, "page_%s_x1333.png" % k), v)
        n += 1
    for k, v in data["tone"].items():
        util.imwrite_unicode(os.path.join(out_dir, "tone_%s.png" % k), v)
        n += 1
    util.imwrite_unicode(os.path.join(out_dir, "adversarial_noise.png"), data["noise"])
    util.imwrite_unicode(os.path.join(out_dir, "adversarial_solid.png"), data["solid"])
    n += 2
    return out_dir, n


if __name__ == "__main__":
    d, n = export()
    print("夹具已导出 %d 张 → %s" % (n, d))
