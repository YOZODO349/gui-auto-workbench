# -*- coding: utf-8 -*-
"""界面令牌层：颜色 / 间距 / 字体的**唯一真源**。

## 为什么要有这一层

`gui.py` 里散着几十处裸颜色和裸间距，改一处配色要在全文件里搜替换，
漏一处就出现"两种黑"。令牌层把这些收拢到一处。

## 四条纪律

1. **改配色只改本文件**。`gui.py` 里不许出现裸色值（`PickOverlay` 的全屏遮罩除外 ——
   它是压在截图上的黑幕，属于"暗房"语汇，不进主题，也**不参与换肤**）。
2. **旧名保留为别名**。
   `tools/verify_crash.py` 引用 `gui.ERRC`、`tools/verify_smallwin.py` 引用 `gui.WARNC`
   —— 直接改名会断掉这两个验证脚本。
3. **间距一律 4 的倍数**（新加的间距必须守这条；搬移进来的历史值只做记录，见 §间距）。
4. **两套主题必须键齐**（见 §主题）：`LIGHT` / `DARK` 的键集合一模一样，
   换肤靠"**颜色→颜色**"的映射表走，缺一个键就会出现"换了一半"的界面。
   增删颜色令牌时**两边一起加**，`selftest` 有一条断言盯着这件事。

## 主题（2026-10-03 新增可切换）

- **light（默认，纸白 / 墨黑）**：画布浅、面板白，层级由三档明度差拉出来；
  品牌色收敛成墨黑 —— 全界面只有**一个**重色块，视线自然落在"下一步该干嘛"。
- **dark（石墨 / 朱红）**：原来那一版。深底铺三级（BG → PANEL → PANEL2），
  品牌色是朱红；语义色在这里必须**提亮**（`OKC/WARNC/ERRC` 与浅色版不是同一组值），
  否则压在深底上看不清。

换肤怎么落地的：`gui._set_theme()` 拿 `LIGHT`↔`DARK` 的**同名令牌**建两张映射表
（背景类 / 文字类分开，因为 `#ffffff` 既可能是面板底也可能是压在主色上的字），
再遍历控件树把"当前值命中旧表"的颜色改写成新值。**只动颜色，不动任何几何** ——
所以布局断言不受影响。

### 明度阶梯（由深到浅，供加控件时对号入座）
light：`FG 墨字 → ACCENT 墨黑块 → MUTED → LINE → SB_THUMB → PANEL2 → BG → PANEL 白`
dark ：`BG → LOG_BG → PANEL → PANEL2 → LINE → SB_THUMB → MUTED → FG`
"""
from __future__ import annotations

# --------------------------------------------------------------------------- #
# 主题：两套完整的颜色表（键必须完全一致）
# --------------------------------------------------------------------------- #
LIGHT = {
    # surface（背景，由浅到白）
    "BG": "#f6f6f4",            # 最底：主窗画布（暖白，比纯灰润）
    "PANEL": "#ffffff",         # 一级面板：顶栏 / 列表 / 日志正文
    "PANEL2": "#ebebe8",        # 二级面板：模式条 / 普通按钮（必须比画布深，否则按钮"消失"）
    "LINE": "#e4e4e0",          # 分隔线（发丝线）
    # ink（文字）
    "FG": "#1c1c1e",            # 主文字（近墨，不用纯黑，避免刺眼）
    "MUTED": "#8a8a90",         # 次要文字 / 说明
    # signal（语义）
    "ACCENT": "#232326",        # 品牌＝墨黑：主按钮 / 选中态（全界面唯一的重色块）
    "OKC": "#2f7d4f",
    "WARNC": "#a9761a",
    "ERRC": "#c0392b",
    "ACCENT_ACTIVE": "#000000",
    "OK_BG": "#eaf3ec",         # ok / danger 一律"极浅底 + 深字"，不做彩色实心
    "OK_FG": "#256b41",
    "OK_ACTIVE": "#d9eade",
    "DANGER_BG": "#f8e7e5",
    "DANGER_FG": "#a8342a",
    "DANGER_ACTIVE": "#f1d5d1",
    "ON_ACCENT_FG": "#ffffff",  # 压在 ACCENT 上的前景色
    "LOG_BG": "#fbfbfa",        # 日志正文底（比面板白半档：读字的地方给最干净的纸）
    # state（悬停 / 禁用）
    "BTN_HOVER": "#e2e2df",
    "BTN_DISABLED": "#f0f0ee",
    "ACCENT_HOVER": "#3a3a40",
    "OK_HOVER": "#dfeee3",
    "DANGER_HOVER": "#f3dcd8",
    # 原生控件收色
    "SB_TROUGH": "#ebebe8",
    "SB_THUMB": "#c9c9c5",
    "SB_THUMB_ACTIVE": "#a9a9a5",
    "PB_TROUGH": "#e9e9e6",
}

DARK = {
    "BG": "#1b1c20",
    "PANEL": "#232428",
    "PANEL2": "#2b2c31",
    "LINE": "#3a3b41",
    "FG": "#e8e8ea",
    "MUTED": "#9a9aa2",
    "ACCENT": "#e0556b",
    "OKC": "#5fd08a",
    "WARNC": "#e8b25a",
    "ERRC": "#ef6b6b",
    "ACCENT_ACTIVE": "#c8485c",
    "OK_BG": "#2f5d45",
    "OK_FG": "#d8ffe8",
    "OK_ACTIVE": "#3d7a5a",
    "DANGER_BG": "#5d2f2f",
    "DANGER_FG": "#ffd8d8",
    "DANGER_ACTIVE": "#7a3d3d",
    "ON_ACCENT_FG": "#ffffff",
    "LOG_BG": "#141519",
    "BTN_HOVER": "#34363d",
    "BTN_DISABLED": "#25262b",
    "ACCENT_HOVER": "#e8697c",
    "OK_HOVER": "#37684f",
    "DANGER_HOVER": "#6d3838",
    "SB_TROUGH": "#2b2c31",
    "SB_THUMB": "#4a4c53",
    "SB_THUMB_ACTIVE": "#5f6169",
    "PB_TROUGH": "#1b1c20",
}

THEMES = {"light": LIGHT, "dark": DARK}
DEFAULT_THEME = "light"
THEME_CN = {"light": "浅色", "dark": "深色"}

# 换肤时的**角色分类** —— 同一个 `#ffffff` 既可能是面板底（背景类）也可能是
# 压在主色上的字（文字类），所以不能只建一张映射表。
# 两套主题的这两组键必须**同名同序**；`selftest` 有条断言盯着键集合是否一致。
BG_KEYS = ("BG", "PANEL", "PANEL2", "LINE", "ACCENT", "ACCENT_ACTIVE",
           "ACCENT_HOVER", "OK_BG", "OK_ACTIVE", "OK_HOVER",
           "DANGER_BG", "DANGER_ACTIVE", "DANGER_HOVER",
           "BTN_HOVER", "BTN_DISABLED", "LOG_BG",
           "SB_TROUGH", "SB_THUMB", "SB_THUMB_ACTIVE", "PB_TROUGH")
FG_KEYS = ("FG", "MUTED", "ACCENT", "OKC", "WARNC", "ERRC",
           "ON_ACCENT_FG", "OK_FG", "DANGER_FG")

THEME_NAME = DEFAULT_THEME


def apply(name: str) -> str:
    """把某个主题写进**本模块的全局变量**（并返回实际生效的主题名）。

    ⚠ 只改本模块不够 —— `gui.py` 用的是 `from src.tokens import *`，
    那是**导入时的值拷贝**。所以 gui 侧必须跟着调一次 `_reload_tokens()`
    （见 `gui._set_theme`）。这是换肤能生效的**前提**，漏了就是"改了颜色表界面没变"。
    """
    global THEME_NAME
    if name not in THEMES:
        name = DEFAULT_THEME
    THEME_NAME = name
    for k, v in THEMES[name].items():
        globals()[k] = v
    return name


def color_maps(frm: str, to: str):
    """建两张 `旧色 → 新色` 表：`(背景类, 文字类)`。用于遍历控件树换肤。

    ★ 值为 `#ffffff` 这种"两义色"必须靠**角色**消歧，不能只靠值 ——
      light 里 `PANEL` 白是背景、`ON_ACCENT_FG` 白是字；只建一张表必然串色。
    """
    a, b = THEMES[frm], THEMES[to]
    bg = {a[k].lower(): b[k] for k in BG_KEYS}
    fg = {a[k].lower(): b[k] for k in FG_KEYS}
    return bg, fg


apply(DEFAULT_THEME)        # 模块级名字先落到默认主题（`import *` 才有值可取）

# --------------------------------------------------------------------------- #
# 字体
# --------------------------------------------------------------------------- #
FAMILY = "Microsoft YaHei UI"
FAMILY_MONO = "Consolas"

FONT_UI = (FAMILY, 10)
FONT_B = (FAMILY, 10, "bold")
FONT_T = (FAMILY, 13, "bold")     # 标题
FONT_S = (FAMILY, 9)              # 小字 / 说明
FONT_LOG = (FAMILY_MONO, 11)

# 日志字号范围（可调）
LOG_SIZE_MIN = 9
LOG_SIZE_MAX = 22
LOG_SIZE_DEFAULT = 11

# --------------------------------------------------------------------------- #
# 间距（一律 4 的倍数）
# --------------------------------------------------------------------------- #
GAP_XS = 4
GAP_S = 6
GAP_M = 8
GAP_L = 12
GAP_XL = 14


# --------------------------------------------------------------------------- #
# 动效（2026-09-30 立）
# --------------------------------------------------------------------------- #
# ★ tkinter **没有**过渡动画。下面这几个时长是**我们自己的规矩**，
#   由 `gui._anim_opt` / `gui._anim_value` 用 after() 分帧实现 ——
#   效果是「颜色/数值平滑走过去」，而不是「瞬间跳过去」。
#   它们**只影响观感，不影响任何控件尺寸**，因此不触碰布局断言。
MOTION_FAST = 80        # 悬停进出：要快，慢了会显得"粘手"
MOTION_BASE = 130       # 状态切换（启用/禁用、模式切换）
MOTION_SLOW = 240       # 数值推进（进度条）：要缓，才看得出"在走"


# --------------------------------------------------------------------------- #
# 旧名兼容说明 —— **不要删**
# --------------------------------------------------------------------------- #
# `tools/verify_crash.py` 读 `gui.ERRC`；`tools/verify_smallwin.py` 读 `gui.WARNC`。
# `gui.py` 用 `from src.tokens import *` 引入，故 `gui.ERRC` / `gui.WARNC` 依然可读
# —— 改名前务必确认这两处验证脚本也跟着改，否则会断。
__all__ = [
    # 主题机制
    "LIGHT", "DARK", "THEMES", "THEME_CN", "DEFAULT_THEME", "THEME_NAME",
    "apply", "color_maps", "BG_KEYS", "FG_KEYS",
    # surface
    "BG", "PANEL", "PANEL2", "LINE",
    # ink
    "FG", "MUTED",
    # signal
    "ACCENT", "OKC", "WARNC", "ERRC",
    "ACCENT_ACTIVE", "OK_BG", "OK_FG", "OK_ACTIVE",
    "DANGER_BG", "DANGER_FG", "DANGER_ACTIVE", "ON_ACCENT_FG",
    "LOG_BG",
    # font
    "FAMILY", "FAMILY_MONO",
    "FONT_UI", "FONT_B", "FONT_T", "FONT_S", "FONT_LOG",
    "LOG_SIZE_MIN", "LOG_SIZE_MAX", "LOG_SIZE_DEFAULT",
    # space
    "GAP_XS", "GAP_S", "GAP_M", "GAP_L", "GAP_XL",
    # motion（时长 / 缓动由 gui 的分帧动画消费）
    "MOTION_FAST", "MOTION_BASE", "MOTION_SLOW",
    # state（悬停 / 禁用 / 原生控件收色）
    "BTN_HOVER", "BTN_DISABLED", "ACCENT_HOVER", "OK_HOVER", "DANGER_HOVER",
    "SB_TROUGH", "SB_THUMB", "SB_THUMB_ACTIVE", "PB_TROUGH",
]
