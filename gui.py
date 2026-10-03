# -*- coding: utf-8 -*-
"""GUI 自动化工作台 —— 两模窗口。

- **编辑模式**：步骤列表里**任选一步** → 点【采集本步画面】（程序自己最小化→激活目标窗→
  抓帧→校验→铺遮罩）→ 在**静止的帧**上单击"识别点"与"操作点" → 当场复核 → 保存
- **执行模式**：数据不齐时**禁用**并指出缺哪一步；跑起来的流程见 `src/engine.py`

纪律（都是从坑里爬出来的，一条都不能省）：
- **画面一律由程序自己抓** —— 不许用户交截图、不许在图上画记号（用户实做反馈：
  手动截图会尺寸不一致、画记号会带噪点）
- 识别框由**程序**以识别点为中心自动裁，用户只给一个点
- UI 只在主线程更新；流程跑在后台线程，用 queue **显式类型**回传
- 每个后台线程入口重申 DPI 感知
- 点【开始】先把自己最小化，跑完/出错叫回来
- 取点遮罩：**先 withdraw 主窗再铺遮罩**；抢全局输入但**不抢焦点**；Esc 全局兜底
- 队列消息别靠元组长度区分类型（加个第三元素会当场炸包）
"""
from __future__ import annotations

import copy
import os
import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import font as tkfont          # ⚠ 必须显式导入：tk.font 不存在
from tkinter import messagebox, ttk

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src import (geometry, matcher, picker, safeui, scripts_repo, storage,
                 uiprefs, util, winio)                                     # noqa: E402
from src.engine import Runner                                            # noqa: E402
from src.host import ScreenHost, create_live_host                        # noqa: E402
# 界面令牌层（颜色/字体/间距的唯一真源）。
# 用 `*` 引入是为了让 `gui.ERRC` / `gui.WARNC` 这类**历史引用**依然可读 ——
# `tools/verify_crash.py` 与 `tools/verify_smallwin.py` 就靠这个。
from src.tokens import *                                                 # noqa: E402,F403
# ⚠ `import *` 是**导入那一刻的值拷贝** —— 换肤改了 tokens 里的全局变量，
#   这边是感知不到的。所以换肤必须走 `_reload_tokens()` 把真值搬回来。
from src import tokens as _tokens                                        # noqa: E402

HOTKEY_VK = 0x77                    # F8
SCREEN_SENTINEL = "__SCREEN__"      # 选窗口列表里"整个屏幕"的哨兵值

# 顶栏横向预算（P7）—— 顶栏是**最容易被撑爆**的一条（见 `_build` 的注释）。
# 目标名（`lb_win`）与状态文字（`lb_mode`）都是文本，谁都不能无限占地方：
#   · 状态文字至少留 TOP_STATUS_MIN_W，否则"执行模式未解锁：为什么"这句就白写了；
#   · 目标名至少留 TOP_TARGET_MIN_W（够显示「目标：」+ 两三个字），再长就截断加省略号。
TOP_STATUS_MIN_W = 96
TOP_TARGET_MIN_W = 72

# 填表对话框（`ScriptNameDialog`）的宽度下限与提示文案折行宽。
# ⚠ 高度**不在这里** —— 它按内容实测（见 `ScriptNameDialog._fit_to_content`）：
#   两种模式的提示文案行数不同，写死高度必然裁掉按钮或留一片空白。
DLG_W = 600
DLG_WRAP = DLG_W - 56

# 状态中文名映射 —— 后台做了而界面不认 = 没做（见 skill 6.2）
STATUS_CN = {
    "idle": "待命",
    "gate": "起跑门控 · 等待画面就绪",
    "detect": "判别页面",
    "run": "执行中",
    "verify": "校验中",
    "return_home": "收尾校验中",
    "done": "完成",
    "abort": "中止",
    "dry": "干跑（只识别不点击）",
    "capture": "采集画面",
    "edit": "编辑中",
}


# --------------------------------------------------------------------------- #
# 界面动效引擎（2026-09-30 新增）
#
# ★ tkinter **没有**圆角、阴影、字距，也**没有过渡动画** —— 这是它的硬边界。
#   所以"动效"在这里只有一条路可走：**自己发帧**。
#   本层就干这一件事：把颜色 / 数值**分帧补间**过去，做出"滑过去"而不是"跳过去"的观感。
#
# ⚠ 三条纪律（违反任何一条都会做出"看起来在动、其实在抖"的假动效）：
#   ① **绝不 while + update()** —— 那会把事件循环掐死，界面假死；
#      只用 `after()` 让出控制权，每帧 16ms（≈60fps）。
#   ② **同一控件同一属性同时只允许一个补间** —— 鼠标快速划过按钮时，
#      多个补间互相打架会让颜色"抖"（新的直接顶掉旧的）。
#   ③ **每帧都要兜底** —— 控件可能在动画途中被销毁（关窗），
#      回调里碰已死的控件会抛 TclError，必须吞掉并安静收工。
# --------------------------------------------------------------------------- #
_MOTION_FRAME_MS = 16                  # ≈60fps
SB_STYLE = "Dark.Vertical.TScrollbar"  # 滚动条统一样式名（见 `_scrollbar`）


def _hex_rgb(s):
    s = str(s or "").lstrip("#")
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    return int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)


def _rgb_hex(r, g, b):
    return "#%02x%02x%02x" % (int(round(r)), int(round(g)), int(round(b)))


def _lerp_hex(c1, c2, t):
    """两个 `#rrggbb` 之间线性插值：t=0 得 c1，t=1 得 c2。"""
    a, b = _hex_rgb(c1), _hex_rgb(c2)
    return _rgb_hex(*[a[i] + (b[i] - a[i]) * t for i in range(3)])


def _ease_out(t):
    """缓出曲线：起步快、落位稳，比线性更像"物理"。"""
    return 1.0 - (1.0 - t) * (1.0 - t)


def _motion_jobs(widget):
    jobs = getattr(widget, "_motion_jobs", None)
    if jobs is None:
        jobs = widget._motion_jobs = {}
    return jobs


def _motion_cancel(widget, opt):
    """顶掉该属性上正在跑的补间（防抖，见纪律②）。"""
    old = _motion_jobs(widget).get(opt)
    if old is not None:
        try:
            widget.after_cancel(old)
        except Exception:                                       # noqa: BLE001
            pass
    _motion_jobs(widget)[opt] = None


def _anim_opt(widget, opt, target, ms=MOTION_BASE):
    """把 `widget` 的某个**颜色**选项补间到 `target`。

    起点**不写死**、而是在调用瞬间读真实值 —— 这样"悬停到一半又移开"也能平滑接上，
    不会跳回某个想象中的"出厂色"。读不出颜色（如 ttk 的样式名）就直接落位。
    """
    _motion_cancel(widget, opt)
    try:
        start = str(widget.cget(opt))
        _hex_rgb(start)                                         # 不是颜色 → 抛
    except Exception:                                           # noqa: BLE001
        try:
            widget.configure(**{opt: target})
        except Exception:                                       # noqa: BLE001
            pass
        return
    if start.lower() == str(target).lower():
        return
    frames = max(1, int(ms) // _MOTION_FRAME_MS)

    def step(k):
        try:
            t = min(1.0, float(k) / frames)
            widget.configure(**{opt: _lerp_hex(start, target, _ease_out(t))})
        except Exception:                                       # noqa: BLE001
            return                                              # 控件没了，安静收工
        if k < frames:
            try:
                _motion_jobs(widget)[opt] = widget.after(
                    _MOTION_FRAME_MS, lambda: step(k + 1))
            except Exception:                                   # noqa: BLE001
                pass
        else:
            _motion_jobs(widget)[opt] = None

    try:
        _motion_jobs(widget)[opt] = widget.after(
            _MOTION_FRAME_MS, lambda: step(1))
    except Exception:                                           # noqa: BLE001
        pass


def _anim_value(widget, target, ms=MOTION_SLOW):
    """数值属性（进度条 `value`）补间 —— 让进度"走"过去，而不是"跳"过去。"""
    opt = "value"
    _motion_cancel(widget, opt)
    try:
        start = float(widget.cget(opt))
    except Exception:                                           # noqa: BLE001
        try:
            widget.configure(**{opt: target})
        except Exception:                                       # noqa: BLE001
            pass
        return
    target = float(target)
    if abs(target - start) < 1.0:
        try:
            widget.configure(**{opt: target})
        except Exception:                                       # noqa: BLE001
            pass
        return
    frames = max(1, int(ms) // _MOTION_FRAME_MS)

    def step(k):
        try:
            t = min(1.0, float(k) / frames)
            widget.configure(**{opt: start + (target - start) * _ease_out(t)})
        except Exception:                                       # noqa: BLE001
            return
        if k < frames:
            try:
                _motion_jobs(widget)[opt] = widget.after(
                    _MOTION_FRAME_MS, lambda: step(k + 1))
            except Exception:                                   # noqa: BLE001
                pass
        else:
            _motion_jobs(widget)[opt] = None

    try:
        _motion_jobs(widget)[opt] = widget.after(
            _MOTION_FRAME_MS, lambda: step(1))
    except Exception:                                           # noqa: BLE001
        pass


def _scrollbar(parent, **kw):
    """统一造滚动条 —— **必须用 ttk 的**。

    ★ 这里有一课，血的：经典 `tk.Scrollbar` 的颜色选项在 Windows 上**会被静默忽略**。
      实测（Tk 8.6.12）：`cget("bg")` 明明返回 `#4a4c53`、`cget("troughcolor")` 返回
      `#232428`，看着"颜色设上了"；**渲染出来却还是系统的白槽 + 灰块** ——
      因为 UxTheme 直接接管了绘制，选项只是存着不用。
      只验 `cget()` 会得出"已经改好了"的结论，那是标准的**假绿灯**。
      只有 `ttk.Scrollbar` 在 clam 主题下才真的听颜色。
    """
    return ttk.Scrollbar(parent, style=SB_STYLE, **kw)


def _setup_ttk(root):
    """把 ttk 控件收进暗色主题（下拉框 / 进度条）。

    ★ 为什么必须换主题：Windows 默认 ttk 主题（vista）**会忽略** `fieldbackground`
      这类背景配置 —— 不换的话，下拉框永远是一块白、进度条永远是浅灰，
      怎么配都压不下去。`clam` 是可主题化的经典主题，换过去之后这些属性才说了算。

    ⚠ **这是本次唯一可能牵动布局的一步** —— clam 会连带改掉 ttk 控件的内边距与
      边框度量。所以改完**必须重跑** `verify_smallwin.py` 的 148 项布局断言。
      本函数只设颜色、**不设 `padding`/`thickness`**，把度量变动压到最小。
    """
    try:
        st = ttk.Style(root)
        st.theme_use("clam")
    except tk.TclError:
        return

    st.configure("TCombobox",
                 fieldbackground=PANEL2, background=PANEL2,
                 foreground=FG, arrowcolor=MUTED,
                 bordercolor=LINE, lightcolor=PANEL2, darkcolor=PANEL2,
                 insertcolor=FG, selectbackground=PANEL2,
                 selectforeground=FG)
    st.map("TCombobox",
           fieldbackground=[("readonly", PANEL2), ("disabled", PANEL2)],
           background=[("readonly", PANEL2), ("disabled", PANEL2),
                       ("active", BTN_HOVER)],
           foreground=[("disabled", MUTED), ("readonly", FG)],
           arrowcolor=[("disabled", LINE), ("readonly", MUTED)],
           bordercolor=[("focus", ACCENT)])

    st.configure("Dark.Horizontal.TProgressbar",
                 troughcolor=PB_TROUGH, background=ACCENT,
                 bordercolor=PB_TROUGH, lightcolor=ACCENT, darkcolor=ACCENT)

    # 滚动条：经典 tk.Scrollbar 在 Windows 上颜色会被吞掉（见 `_scrollbar` 的注释），
    # 所以走 ttk + clam —— 这样槽与滑块才真的暗下来。
    st.configure(SB_STYLE,
                 background=SB_THUMB, troughcolor=SB_TROUGH,
                 bordercolor=SB_TROUGH, arrowcolor=MUTED,
                 lightcolor=SB_THUMB, darkcolor=SB_THUMB)
    st.map(SB_STYLE,
           background=[("active", SB_THUMB_ACTIVE)],
           arrowcolor=[("active", FG)])

    # 下拉展开后的那截列表是 Tk 的 Listbox（**不走 ttk**），只能用 option 数据库压色，
    # 否则「框是暗的、一展开白得刺眼」。
    for pat, val in (("*TCombobox*Listbox.background", PANEL2),
                     ("*TCombobox*Listbox.foreground", FG),
                     ("*TCombobox*Listbox.selectBackground", ACCENT),
                     ("*TCombobox*Listbox.selectForeground", ON_ACCENT_FG)):
        try:
            root.option_add(pat, val)
        except Exception:                                       # noqa: BLE001
            pass


# 按钮四种"身份"的底色与悬停色 —— 收在一处，改配色不用翻全文件
_KIND_BG = {"primary": ACCENT, "ok": OK_BG, "danger": DANGER_BG,
            "normal": PANEL2}
_KIND_HOVER = {"primary": ACCENT_HOVER, "ok": OK_HOVER, "danger": DANGER_HOVER,
               "normal": BTN_HOVER}


def _reload_tokens():
    """把 `src.tokens` 的**当前**主题值搬回本模块全局。

    ★ 非它不可的原因：`from src.tokens import *` 是导入时的**值拷贝**。
      换肤时 `tokens.apply()` 改的是 tokens 自己的全局变量，本模块里那些名字
      仍指着旧颜色；不搬一次，就会出现"色号表换了、界面没动"。
    ★ `_KIND_BG` / `_KIND_HOVER` 是**导入时算好的常量表**，也得跟着重建；
      否则按钮换肤后一悬停就跳回旧主题（悬停色记在这两张表里）。
    """
    for _n in _tokens.__all__:
        globals()[_n] = getattr(_tokens, _n)
    _KIND_BG.update({"primary": ACCENT, "ok": OK_BG, "danger": DANGER_BG,
                     "normal": PANEL2})
    _KIND_HOVER.update({"primary": ACCENT_HOVER, "ok": OK_HOVER,
                        "danger": DANGER_HOVER, "normal": BTN_HOVER})


# --------------------------------------------------------------------------- #
# 取点遮罩上那条提示的快捷键（可自定义，存在 config/ui.json）
#
# ★ 为什么是"切换"而不是"关闭"：遮罩是**必须**停下来点的（不点就没法继续），
#   所以它不能被"关掉"，只能被"藏起来" —— 用户要看的画面正好在提示底下时，
#   按一下藏、看完再按一下叫回来。这样既不挡画面，也不丢流程。
# --------------------------------------------------------------------------- #
HUD_HOTKEY_DEFAULT = "Escape"
# 遮罩（暗房）自己的亮边色：它压在任意亮度的截图上，**不跟主题走** ——
# 浅色主题下 ACCENT 是墨黑，压在深色画面上等于没有描边。
HUD_EDGE = "#e0556b"


def _hotkey_seq(name):
    """把设置里存的键名变成 Tk 事件序列：`Escape` → `<Escape>`。

    ⚠ 必须白名单化。Tk 的绑定串是**可执行的小语言**（`<Control-x>`、`<<Foo>>`…），
      把用户输入直接塞进去，轻则绑定失效（然后"按了没反应"查半天），
      重则一个字打错就让整个遮罩绑不上任何键。所以：只放行"字母数字 + `-`"，
      其余一律退回默认（`Esc`）。
    """
    t = str(name or "").strip()
    okay = bool(t) and len(t) <= 24 and all(c.isalnum() or c in "-_" for c in t)
    return "<%s>" % (t if okay else HUD_HOTKEY_DEFAULT)


def _hotkey_label(name):
    """给人看的键名：`Control-h` → `Ctrl+H`。"""
    t = str(name or HUD_HOTKEY_DEFAULT)
    return t.replace("Control", "Ctrl").replace("Shift", "Shift").replace("-", "+")


# 换肤要扫的颜色选项。**按角色分成两组**：同一个 `#ffffff` 既可能是"面板底"
# 也可能是"压在主色上的字"，只用一张映射表必然串色（见 `tokens.color_maps`）。
_WALK_BG_OPTS = ("bg", "background", "activebackground", "selectbackground",
                 "highlightbackground", "troughcolor", "selectcolor")
_WALK_FG_OPTS = ("fg", "foreground", "activeforeground", "selectforeground",
                 "disabledforeground", "insertbackground", "highlightcolor",
                 "arrowcolor")


def _apply_theme_colors(root, bgmap, fgmap):
    """遍历控件树，把**命中旧色表**的颜色改写成新值，返回改动处数。

    ★ 为什么走"颜色→颜色"映射，而不是重建界面：**重建会动几何**。
      本项目有 148 项布局断言（四档窗口尺寸 × 关键控件可见），
      重建一旦漏还原某个状态（选中 / 模式 / 折叠），就是一次静默回归。
      颜色映射只碰颜色选项 —— 尺寸、字体、层级一个不碰，天生安全。

    ★ 只对"当前值确实等于旧主题某色"的选项动手：`cget` 回来的可能是
      `SystemButtonFace` 这类系统色，映射表里没有 → 原样留着，不乱改。
    """
    changed = [0]

    def setf(w, opt, val):
        try:
            w.configure(**{opt: val})
            changed[0] += 1
        except Exception:                                       # noqa: BLE001
            pass

    def walk(w):
        try:
            if isinstance(w, PickOverlay):     # 暗房遮罩：故意不进主题，不换肤
                return
        except Exception:                                       # noqa: BLE001
            pass

        opts = set()
        try:
            opts = set(w.keys())
        except Exception:                                       # noqa: BLE001
            opts = set()
        for opt, mp in [(o, bgmap) for o in _WALK_BG_OPTS] + \
                       [(o, fgmap) for o in _WALK_FG_OPTS]:
            if opt not in opts:
                continue
            try:
                cur = str(w.cget(opt))
            except Exception:                                   # noqa: BLE001
                continue
            new = mp.get(cur.lower())
            if new and new.lower() != cur.lower():
                setf(w, opt, new)

        # 自绘的画布（进度条之类）：图形颜色也要跟
        if isinstance(w, tk.Canvas):
            for item in w.find_all():
                for opt, mp in (("fill", bgmap), ("outline", fgmap)):
                    try:
                        cur = str(w.itemcget(item, opt))
                    except Exception:                           # noqa: BLE001
                        continue
                    new = mp.get(cur.lower())
                    if new and new.lower() != cur.lower():
                        try:
                            w.itemconfigure(item, **{opt: new})
                            changed[0] += 1
                        except Exception:                       # noqa: BLE001
                            pass

        # 按钮把"身份色"记在自己身上（`_btn` / `_btn_set_enabled` 用），一并换新
        if hasattr(w, "_kind"):
            w._kind_bg = _KIND_BG.get(w._kind, PANEL2)
            w._hover_bg = _KIND_HOVER.get(w._kind, BTN_HOVER)
            w._hover_back = None

        for ch in w.winfo_children():
            walk(ch)

    def _cancel_motions(w):
        """先掐掉**在飞的补间**（`_anim_opt` 排的 after 帧）。

        ★ 血训（`verify_theme` 抓到的）：不掐就必然"换了一半" ——
          补间在开始时就把**目标色**记在闭包里了，换肤把控件改成新色之后，
          它下一帧又把控件**写回旧主题的颜色**。表现是"整个界面换了，
          偏偏某两个控件还是旧色"（实测：模式条那两个单选按钮）。
          这也解释了为什么只截一张图看不出问题 —— 得等补间跑完才现形。
        """
        jobs = getattr(w, "_motion_jobs", None)
        if jobs:
            for h in list(jobs.values()):
                try:
                    w.after_cancel(h)
                except Exception:                               # noqa: BLE001
                    pass
            jobs.clear()
        for ch in w.winfo_children():
            _cancel_motions(ch)

    _cancel_motions(root)
    walk(root)
    return changed[0]


def _wire_hover(b, hover_hex):
    """给按钮挂**悬停渐变**。

    ★ `tk.Button` 原生**没有 hover** —— `activebackground` 只在**按住**时生效，
      鼠标只是移上去是没有任何反馈的（全界面此前 0 个 `<Enter>` 绑定）。
      所以只能自己绑 `Enter`/`Leave`。

    ★ 回退色**不记"出厂色"、而记"进入前的真实底色"**：按钮的底色随时可能被
      代码改掉（启用/禁用切换），拿旧色回退会串色。
    """
    def on_enter(_e=None):
        if str(b.cget("state")) == "disabled":
            return                                              # 禁用就别"迎上来"
        b._hover_back = str(b.cget("bg"))
        _anim_opt(b, "bg", hover_hex, MOTION_FAST)

    def on_leave(_e=None):
        back = getattr(b, "_hover_back", None) or getattr(b, "_kind_bg", None)
        if back:
            _anim_opt(b, "bg", back, MOTION_FAST)

    b.bind("<Enter>", on_enter, add="+")
    b.bind("<Leave>", on_leave, add="+")


def _btn_set_enabled(b, on):
    """启用 / 禁用按钮 —— **连底色一起降级**。

    ★ 原来只改 `state`：tk 只把**文字**变灰，按钮底色还是那一片鲜红 / 翠绿，
      看起来照样"能点"。禁用就该看起来禁用 —— 底色是无几何影响的属性，改它安全。
    ★ 状态没变就**直接返回**：否则每次刷模式门控都会重启一遍补间，界面会闪。
    """
    want = "normal" if on else "disabled"
    try:
        if str(b.cget("state")) == want:
            return
        b.configure(state=want)
        _anim_opt(b, "bg", b._kind_bg if on else BTN_DISABLED, MOTION_FAST)
    except Exception:                                           # noqa: BLE001
        pass


def _btn(parent, text, cmd, kind="normal", width=None):
    b = tk.Button(parent, text=text, command=cmd, font=FONT_UI,
                  relief="flat", bd=0, padx=12, pady=6, cursor="hand2",
                  highlightthickness=0)
    if kind == "primary":
        b.configure(bg=ACCENT, fg=ON_ACCENT_FG, activebackground=ACCENT_ACTIVE,
                    activeforeground=ON_ACCENT_FG)
    elif kind == "ok":
        b.configure(bg=OK_BG, fg=OK_FG, activebackground=OK_ACTIVE,
                    activeforeground=ON_ACCENT_FG)
    elif kind == "danger":
        b.configure(bg=DANGER_BG, fg=DANGER_FG, activebackground=DANGER_ACTIVE,
                    activeforeground=ON_ACCENT_FG)
    else:
        b.configure(bg=PANEL2, fg=FG, activebackground=LINE, activeforeground=FG)
    # 禁用态文字色统一压成次要色（tk 默认的灰与暗底对比度太低，像"糊了"）
    b.configure(disabledforeground=MUTED)
    # 身份信息挂在控件上 —— `_btn_set_enabled` 与悬停回退都要用它
    b._kind = kind
    b._kind_bg = _KIND_BG.get(kind, PANEL2)
    b._hover_bg = _KIND_HOVER.get(kind, BTN_HOVER)
    _wire_hover(b, b._hover_bg)
    if width:
        b.configure(width=width)
    return b



def _clip(text, width):
    """按字符数截断（超出加 `…`），并压掉换行。

    ⚠ 别直接用 `text[:width]` 而不加省略号：列表里看不出"后面还有内容"，
      用户会以为名字本身就长这样（脚本仓库的表头对齐靠它统一列宽）。
    """
    t = str(text or "").replace("\n", " ").replace("\r", " ")
    return t if len(t) <= width else t[:max(1, width - 1)] + "…"


def _disp_w(text):
    """字符串的**显示宽度**：中文/全角算 2，其它算 1。

    ★ 为什么需要：等宽字体（Consolas）里中文是**全角**，占两个半角宽。
      `"%-20s" % "每日签到"` 按字符数补到 20，但显示宽是 8+16=24 ——
      直接按字符数对齐表格，中文行永远比表头长一截（实拍已验证）。
      脚本仓库的列表对齐全靠它。
    """
    return sum(2 if ord(ch) > 0x2E7F else 1 for ch in str(text or ""))


def _pad(text, width):
    """按**显示宽度**右补空格到指定宽（超长先截）。表格对齐专用。"""
    t = _clip(text, width)
    return t + " " * max(0, width - _disp_w(t))


# --------------------------------------------------------------------------- #
# 取点遮罩：在**冻结的帧**上单击两个点
# --------------------------------------------------------------------------- #
class PickOverlay(tk.Toplevel):
    """全屏取点遮罩。

    用户在这里做两件事：**单击识别点** → **单击操作点**。
    背景就是程序刚采集的那一帧（贴在客户区位置上），画面静止，点得准。
    """

    def __init__(self, app, frame, client_rect, step_label, same_as_anchor,
                 on_done, on_cancel):
        super().__init__(app)
        self.app = app
        self.frame_img = frame
        self.client_rect = client_rect            # 客户区在屏幕上的 (x, y, w, h)
        self.step_label = step_label
        self.same_as_anchor = same_as_anchor
        self.on_done = on_done
        self.on_cancel = on_cancel

        self.fh, self.fw = frame.shape[:2]
        self.stage = 0                            # 0=识别点 1=操作点
        self.rec_pt = None                        # 帧内坐标
        self.act_pt = None
        self._marks = []
        self._img_ref = None
        self.vw, self.vh = 0, 0                   # 虚拟屏尺寸（提示按它居中）
        self._hud_on = True                       # 中央提示当前是否显示
        # ⚠ 从盘上读回来**也要过一遍白名单**：ui.json 是用户能手改的文件，
        #   而 `hud_hotkey` 会被拼进 `bind_all` 的序列串 —— `<evil>` 这种写法
        #   轻则让遮罩绑不上任何键（"按了没反应"，最难查的那种），重则绑定失效后
        #   用户没有任何退出入口（主窗这时是藏着的）。入口收口比出口收口可靠。
        self.hud_hotkey = _hotkey_seq(uiprefs.load().get("hud_hotkey"))[1:-1]

        # 覆盖整个虚拟屏（多显示器也不漏）
        import ctypes
        u = ctypes.windll.user32
        self.vx = u.GetSystemMetrics(76)
        self.vy = u.GetSystemMetrics(77)
        vw = u.GetSystemMetrics(78)
        vh = u.GetSystemMetrics(79)
        self.vw, self.vh = vw, vh

        self.overrideredirect(True)
        self.geometry("%dx%d+%d+%d" % (vw, vh, self.vx, self.vy))
        self.configure(bg="#101014")
        self.attributes("-topmost", True)

        self.canvas = tk.Canvas(self, bg="#101014", highlightthickness=0,
                                width=vw, height=vh)
        self.canvas.pack(fill="both", expand=True)
        self._draw_frame()
        self._draw_hud()

        self.canvas.bind("<Button-1>", self._on_click)
        self.canvas.bind("<Button-3>", self._on_back)
        # 快捷键：藏起 / 叫回**正中央那条提示**（默认 Esc，可在设置栏改）
        self.bind_all(_hotkey_seq(self.hud_hotkey), self._toggle_hud)
        # ⚠ Esc 的"兜底"不能丢：主窗这时是藏着的，用户没有别的退出入口。
        #   快捷键**不是** Esc 时，Esc 继续当取消；是 Esc 时它已经用于开关提示，
        #   取消就走右键（HUD 上写明）—— 同一个键不能同时干两件事。
        if _hotkey_seq(self.hud_hotkey) != "<Escape>":
            self.bind_all("<Escape>", self._on_escape)

        # 抢全局输入独占，但**不抢焦点**（抢焦点会打断鼠标捕获）
        try:
            self.grab_set_global()
        except tk.TclError:
            try:
                self.grab_set()
            except tk.TclError:
                pass
        self.focus_set()
        # ⚠ 光 `self.focus_set()`（对着 Toplevel 调）**没用** —— Tk 的键盘事件只发给
        #   **持有焦点的那个控件**，而 Toplevel 不是控件的落点；`overrideredirect`
        #   的窗口又拿不到 OS 前台，于是键被前台那个程序（或系统）收走：
        #   表现就是"按快捷键没反应"。必须在**画布**上落焦（`takefocus` 一起打开）。
        self.canvas.configure(takefocus=True)
        self.canvas.focus_set()
        # ★ 关键：`overrideredirect(True)` 的窗口**拿不到键盘焦点** ——
        #   它没有标题栏，Windows 不会把它激活成前台窗口，键全被前台那个程序收走。
        #   于是"按快捷键没反应"（用户实拍）。`focus_set()` 只设 Tk 内部的焦点，
        #   管不到 OS 那一层，必须有一步**真的把窗口弄成前台**。
        self._claim_keyboard()
        self.after(120, self._claim_keyboard)      # WM 有时要一拍才认
        # ⚠ 再补一拍：`SetForegroundWindow` 会被系统的"前台锁"挡（本程序刚起来、
        #   前台还挂在别的窗口上时不放行），一次不成不代表永远不成 —— 关键路径上
        #   宁可多试两次，也不要让用户看到"按了没反应"。
        self.after(420, self._claim_keyboard)

    def _claim_keyboard(self):
        """把键盘焦点**真的**抢过来（前台窗口 + 焦点都指到自己）。

        ⚠ 这一条以前是空的 —— 而"按了没反应"最容易被误判成"绑定写错了"。
          判据不是"我调了 focus_set()"，而是**系统认为谁是前台**。
        """
        try:
            import ctypes
            u = ctypes.windll.user32
            hwnd = int(self.winfo_id())
            u.SetForegroundWindow(hwnd)
            u.SetFocus(hwnd)
            # ★ 两层都要：OS 那一层（谁是前台）+ Tk 那一层（焦点落在哪个控件）。
            #   只做前者 = 窗口是前台了但 Tk 不知道键给谁；只做后者 = 键压根没送进来。
            self.canvas.focus_set()
            got = self.focus_get()
            return (u.GetForegroundWindow() == hwnd
                    and got is not None and str(got).startswith(str(self.canvas)))
        except Exception:                                       # noqa: BLE001
            return False

    # --- 绘制 ---
    def _draw_frame(self):
        from PIL import Image, ImageTk
        cx, cy, cw, ch = self.client_rect
        img = Image.fromarray(self.frame_img[:, :, ::-1])       # BGR → RGB
        if (img.width, img.height) != (cw, ch):
            img = img.resize((cw, ch), Image.LANCZOS)
        self._img_ref = ImageTk.PhotoImage(img)
        self.canvas.create_image(cx - self.vx, cy - self.vy,
                                 anchor="nw", image=self._img_ref)
        self.canvas.create_rectangle(cx - self.vx - 1, cy - self.vy - 1,
                                     cx - self.vx + cw, cy - self.vy + ch,
                                     outline=ACCENT, width=2)

    def _draw_hud(self):
        """屏幕**正中央**那条提示。醒目：大字 + 深底 + 亮边。

        ★ 上一版是"顶上一条 40px 小字"（用户实拍反馈：一行白小字在左上角，
          根本看不清）。问题是三条：
            ① 位置：贴在屏幕边缘，视线在画面中间时压根注意不到；
            ② 层级：12pt 白字压在**任意亮度**的截图上，遇到浅背景就糊了；
            ③ 不可撤：它盖住画面也没法让开。
          所以改成：正中央 + 20pt 主标 + 深底亮边（不依赖背景亮度）+ **可开关**
          （默认 Esc，见 `_toggle_hud`；键可在设置栏改）。

        ⚠ 文案里**别写 `**`**（P7c）：Tk 不认 markdown，写在这儿会**原样显示**。
          以前是"先写 `**识别点**`、下一行再 replace"—— 显示干净但源码留星号，
          会让"弹窗文案不许有 `**`"那条断言必须为这种跨行写法开特例。
        """
        self.canvas.delete("hud")
        self._hud_on = True
        key = _hotkey_label(self.hud_hotkey)
        if self.stage == 0:
            head = "现在点：识别点"
            sub = "点在这一步最独特、最静止的元素上 —— 识别框由程序自动裁"
        else:
            head = "现在点：操作点"
            sub = "点在这一步要点击的位置（和识别点同位置时，勾一下就不用再点）"
        tail = ("按 %s 隐藏本条提示 · 再按一次显示"
                "　｜　右键回退 · 第一步时右键=取消" % key)
        # 文案里不留 `**`：上面这几句是**直接显示的字**，不是 markdown。
        f_head = (FAMILY, 20, "bold")
        f_sub = (FAMILY, 13)
        f_tail = (FAMILY, 12)
        try:
            m_head = tkfont.Font(font=f_head).measure(head)
            m_sub = tkfont.Font(font=f_sub).measure(sub)
            m_tail = tkfont.Font(font=f_tail).measure(tail)
        except Exception:                                       # noqa: BLE001
            m_head, m_sub, m_tail = (len(head) * 20, len(sub) * 13,
                                     len(tail) * 12)
        pad_x, pad_y = 40, 24
        bw = max(m_head, m_sub, m_tail) + pad_x * 2
        bh = pad_y * 2 + 36 + 30 + 12 + 22
        cx = (self.vw or self.winfo_screenwidth()) // 2
        cy = (self.vh or self.winfo_screenheight()) // 2
        x0, y0 = cx - bw // 2, cy - bh // 2
        # 深底 + 亮边：不依赖截图背景的明暗，浅画面上也读得清
        self.canvas.create_rectangle(x0, y0, x0 + bw, y0 + bh,
                                     fill="#0d0d12", outline=HUD_EDGE,
                                     width=2, tags="hud")
        y = y0 + pad_y + 4
        self.canvas.create_text(cx, y, text="%s　%s" % (self.step_label, head),
                                fill="#ffffff", font=f_head, tags="hud")
        y += 38
        self.canvas.create_text(cx, y, text=sub, fill="#c7c7d1",
                                font=f_sub, tags="hud")
        y += 30
        self.canvas.create_text(cx, y, text=tail, fill=HUD_EDGE,
                                font=f_tail, tags="hud")
        self.canvas.tag_raise("hud")
        self.canvas.delete("hud_badge")      # 提示回来了，底边那行"叫回来"小字就该走

    def _draw_badge(self):
        """提示藏起来之后，底边留一行小字 —— **别让用户没路可走**。

        ★ 一个"可以藏起来"的东西，必须同时说明"怎么叫回来、怎么退出"。
          藏干净了什么都不留，用户会以为程序卡死（这次就是这个教训的延伸）。
        """
        self.canvas.delete("hud_badge")
        key = _hotkey_label(self.hud_hotkey)
        txt = "提示已隐藏 · 按 %s 显示 · 右键回退（第一步时=取消）" % key
        cx = (self.vw or self.winfo_screenwidth()) // 2
        y = (self.vh or self.winfo_screenheight()) - 34
        self.canvas.create_text(cx, y, text=txt, fill=HUD_EDGE,
                                font=(FAMILY, 12), tags="hud_badge")
        self.canvas.tag_raise("hud_badge")

    def _toggle_hud(self, _ev=None):
        """快捷键：藏起 / 叫回中央提示（同键再按一次回来）。

        ★ 为什么是"开关"而不是"关掉"：遮罩是**必须**停下来点的（不点没法学下去），
          所以它不能被关掉、只能被藏起来 —— 画面正好被提示挡住时让开看，
          看完再叫回来，流程一点不丢。
        """
        if self._hud_on:
            self.canvas.delete("hud")
            self._hud_on = False
            self._draw_badge()
        else:
            self._draw_hud()
        return "break"

    def _hud_hit(self, x, y):
        """这个点是不是落在提示面板上（鼠标兜底用）。"""
        bb = self.canvas.bbox("hud")
        return bool(bb) and bb[0] <= x <= bb[2] and bb[1] <= y <= bb[3]

    def _mark(self, fx, fy, color, label):
        sx = self.client_rect[0] + fx - self.vx
        sy = self.client_rect[1] + fy - self.vy
        items = [
            self.canvas.create_line(sx - 14, sy, sx + 14, sy, fill=color, width=2),
            self.canvas.create_line(sx, sy - 14, sx, sy + 14, fill=color, width=2),
            self.canvas.create_oval(sx - 6, sy - 6, sx + 6, sy + 6, outline=color,
                                    width=2),
            self.canvas.create_text(sx + 16, sy - 16, text=label, fill=color,
                                    font=("Microsoft YaHei UI", 10, "bold"),
                                    anchor="w"),
        ]
        self._marks.append(items)
        self.canvas.tag_raise("hud")

    def _clear_marks(self):
        for items in self._marks:
            for it in items:
                self.canvas.delete(it)
        self._marks = []

    # --- 交互 ---
    def _to_frame_xy(self, ev):
        sx, sy = ev.x + self.vx, ev.y + self.vy
        return (sx - self.client_rect[0], sy - self.client_rect[1])

    def _on_click(self, ev):
        # 点在**提示面板上** = 收起它（鼠标兜底）。
        #   ⚠ 为什么要留这条路：键盘送达依赖 OS 前台焦点，那是**外部世界**的事
        #     （overrideredirect 窗口天生难拿焦点，见 `_claim_keyboard`）。
        #     只留一条"必须键盘能到"的路，就等于把可用性押在系统策略上。
        if self._hud_on and self._hud_hit(ev.x, ev.y):
            self._toggle_hud()
            return
        fx, fy = self._to_frame_xy(ev)
        if not (0 <= fx < self.fw and 0 <= fy < self.fh):
            self.canvas.delete("warn")
            self.canvas.create_text(ev.x, max(60, ev.y - 30),
                                    text="请点在画面范围内（红框以内）",
                                    fill=WARNC, font=FONT_B, tags="warn")
            return
        if self.stage == 0:
            self.rec_pt = (fx, fy)
            self._mark(fx, fy, OKC, "识别点")
            if self.same_as_anchor:
                self.act_pt = (fx, fy)
                self._finish()
                return
            self.stage = 1
            self._draw_hud()
        else:
            self.act_pt = (fx, fy)
            self._mark(fx, fy, ACCENT, "操作点")
            self._finish()

    def _on_back(self, _ev=None):
        """右键回退当前这一步（识别点/操作点）。"""
        if self.stage == 1:
            self.stage = 0
            self.rec_pt = None
            self._clear_marks()
            self._draw_hud()
        else:
            self._on_cancel()

    def _on_escape(self, _ev=None):
        self._on_cancel()

    def _finish(self):
        self._teardown()
        try:
            self.on_done(self.rec_pt, self.act_pt)
        except Exception as exc:                       # pragma: no cover
            messagebox.showerror("出错了", "复核环节出错：%s" % exc)

    def _on_cancel(self):
        self._teardown()
        try:
            self.on_cancel()
        except Exception:
            pass

    def _teardown(self):
        """收尾统一走一个函数：清引用 → 解除独占 → 销毁 → 恢复主窗。"""
        try:
            self.unbind_all(_hotkey_seq(self.hud_hotkey))
            self.unbind_all("<Escape>")
        except Exception:
            pass
        try:
            self.grab_release()
        except Exception:
            pass
        self._marks = []
        self._img_ref = None
        try:
            self.destroy()
        except Exception:
            pass
        self.app.overlay = None                        # 防重入锁归位
        self.app._restore_self()


# --------------------------------------------------------------------------- #
# 复核窗：选完当场回显三件事（边标边验，最值钱的一步）
# --------------------------------------------------------------------------- #
class ReviewDialog(tk.Toplevel):
    def __init__(self, app, payload, on_save, on_resize, on_repick_rec,
                 on_repick_act, on_cancel, on_ct_change=None):
        super().__init__(app)
        self.app = app
        self.on_ct_change = on_ct_change or (lambda _v: None)
        # ⚠ 必须**存成属性**：`_on_cancel` 是方法（三个出口共用），拿不到构造函数的闭包。
        #   漏存 → `self.on_cancel` 抛 AttributeError → 被"兜底 except"吞掉 →
        #   表现就是"窗关了、但数据没清"（比"完全没反应"更难查，因为界面看着动过）。
        self.on_cancel = on_cancel or (lambda: None)
        self.title("复核这一步 —— 边标边验")
        self.configure(bg=BG)
        self.geometry("+%d+%d" % (app.winfo_rootx() + 120, app.winfo_rooty() + 90))
        self.resizable(False, False)
        self.attributes("-topmost", True)
        self.grab_set()
        # ★ 所有"退出这一窗"的入口都走同一条路（按钮 / 标题栏 × / Esc）——
        #   以前只有按钮，且按钮调的 `on_cancel` 并不关窗（见 `_on_cancel` 的血训）。
        self.protocol("WM_DELETE_WINDOW", self._on_cancel)
        self.bind("<Escape>", lambda _e: self._on_cancel())

        self.payload = payload
        w = payload["frame_w"]
        h = payload["frame_h"]

        tk.Label(self, text="复核（对就保存，不对就重选）", bg=BG, fg=FG,
                 font=FONT_T).pack(anchor="w", padx=14, pady=(12, 6))

        # ① 落点 + 框，画在采集帧上
        body = tk.Frame(self, bg=BG)
        body.pack(fill="both", expand=True, padx=14)
        left = tk.Frame(body, bg=BG)
        left.pack(side="left")
        from PIL import Image, ImageDraw, ImageTk
        scale = min(1.0, 560.0 / max(1, w))
        prev = Image.fromarray(payload["frame"][:, :, ::-1]).convert("RGB")
        if scale < 1.0:
            prev = prev.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
        dr = ImageDraw.Draw(prev)
        bx, by, bw2, bh2 = payload["box_abs"]
        s = scale
        dr.rectangle([bx * s, by * s, (bx + bw2) * s, (by + bh2) * s],
                     outline=(95, 208, 138), width=2)
        for pt, col, lab in ((payload["rec_pt_abs"], (95, 208, 138), "识别点"),
                             (payload["act_pt_abs"], (224, 85, 107), "操作点")):
            x, y = pt[0] * s, pt[1] * s
            dr.line([x - 12, y, x + 12, y], fill=col, width=2)
            dr.line([x, y - 12, x, y + 12], fill=col, width=2)
        self._prev_ref = ImageTk.PhotoImage(prev)
        tk.Label(left, image=self._prev_ref, bg=BG, bd=0).pack()

        right = tk.Frame(body, bg=BG)
        right.pack(side="left", padx=(16, 0), anchor="n")

        tpl = payload["template"]
        ti = Image.fromarray(tpl[:, :, ::-1]).convert("RGB")
        k = max(1, int(220 / max(1, ti.width)))
        if k > 1:
            ti = ti.resize((ti.width * k, ti.height * k), Image.NEAREST)
        self._tpl_ref = ImageTk.PhotoImage(ti)
        tk.Label(right, text="② 自动裁出的模板（就是拿它认这一步）", bg=BG,
                 fg=MUTED, font=FONT_S).pack(anchor="w")
        tk.Label(right, image=self._tpl_ref, bg=PANEL, bd=1, relief="solid").pack(
            anchor="w", pady=(2, 10))

        score = payload["score"]
        neg = payload["neg_score"]
        th = payload["threshold"]
        fg_score = OKC if score >= 0.9 else (WARNC if score >= th else ERRC)
        tk.Label(right, text="③ 当场匹配得分：%.4f（阈值 %.2f）" % (score, th),
                 bg=BG, fg=fg_score, font=FONT_B).pack(anchor="w")
        tk.Label(right, text="读法：" + matcher.explain_score(score), bg=BG,
                 fg=MUTED, font=FONT_S).pack(anchor="w")
        neg_txt = "无（还没有别的步骤可比）" if neg is None else "%.4f" % neg
        neg_fg = MUTED if neg is None else (OKC if neg < th - 0.1 else WARNC)
        tk.Label(right, text="与其它步骤的最高相似分（越低越好）：%s" % neg_txt,
                 bg=BG, fg=neg_fg, font=FONT_UI).pack(anchor="w", pady=(8, 0))
        if neg is not None and neg >= th - 0.1:
            tk.Label(right, text="⚠ 离阈值太近 —— 换个更独特的元素重选，别靠调阈值硬撑",
                     bg=BG, fg=WARNC, font=FONT_S, wraplength=320,
                     justify="left").pack(anchor="w", pady=(2, 0))

        # ④ 操作类型：单击 / 双击。**默认按 pending 里的值回显** ——
        #    新建时是 "single"（= 老行为），重新复核已有步骤时显示它当前的类型。
        tk.Label(right, text="④ 这一步怎么点", bg=BG, fg=MUTED,
                 font=FONT_S).pack(anchor="w", pady=(10, 0))
        self._ct_var = tk.StringVar(value=payload.get("click_type", "single"))
        ct_row = tk.Frame(right, bg=BG)
        ct_row.pack(anchor="w")
        for txt, val in (("单击（默认）", "single"), ("双击", "double")):
            tk.Radiobutton(ct_row, text=txt, variable=self._ct_var, value=val,
                           command=self._on_ct_change, bg=BG, fg=FG,
                           selectcolor=BG, font=FONT_UI, activebackground=BG,
                           activeforeground=FG, bd=0, highlightthickness=0,
                           ).pack(side="left")

        tk.Label(right, text="① 坐标（相对值，换机器也能用）", bg=BG, fg=MUTED,
                 font=FONT_S).pack(anchor="w", pady=(10, 0))
        tk.Label(right, text="识别点  x=%.4f  y=%.4f" % tuple(payload["rec_rel"]),
                 bg=BG, fg=FG, font=FONT_LOG).pack(anchor="w")
        if payload["act_rel"]:
            tk.Label(right, text="操作点  x=%.4f  y=%.4f" % tuple(payload["act_rel"]),
                     bg=BG, fg=FG, font=FONT_LOG).pack(anchor="w")
        tk.Label(right, text="识别框  %d×%d px" % (bw2, bh2), bg=BG, fg=MUTED,
                 font=FONT_S).pack(anchor="w", pady=(4, 0))

        # ⚠ **操作点跑出识别框 = 很可能点空**，当场提示（不拦保存）。
        #   为什么必须提示：两点不一致**是允许的**（识别点管"认出哪一页"，
        #   操作点管"点哪里"），但跑出框多半意味着操作点没对准目标控件 ——
        #   那一下会点空，而单步流程又验证不出"点空"（画面没变本就允许），
        #   用户只会觉得"跑完了但没反应"，极难自查。
        if payload.get("act_rel") and not payload.get("same_as_anchor"):
            _ok, _why = picker.act_point_offset(
                payload["rec_pt_abs"], payload["act_pt_abs"], payload["box_abs"])
            if not _ok:
                tk.Label(right, text="⚠ " + _why, bg=BG, fg=WARNC,
                         font=FONT_S, wraplength=300, justify="left"
                         ).pack(anchor="w", pady=(6, 0))

        bar = tk.Frame(self, bg=BG)
        bar.pack(fill="x", padx=14, pady=14)
        _btn(bar, "保存这一步", on_save, "ok").pack(side="left")
        _btn(bar, "框大一点", lambda: on_resize(1.35)).pack(side="left", padx=6)
        _btn(bar, "框小一点", lambda: on_resize(1 / 1.35)).pack(side="left")
        _btn(bar, "重选识别点", on_repick_rec).pack(side="left", padx=6)
        if not payload.get("same_as_anchor"):
            _btn(bar, "重选操作点", on_repick_act).pack(side="left")
        _btn(bar, "取消", self._on_cancel, "danger").pack(side="right")

    def _on_ct_change(self):
        """选中即回写 —— 不依赖"点保存"才生效，取消复核也不会留下半截状态。"""
        try:
            self.on_ct_change(self._ct_var.get())
        except Exception:
            pass

    def _on_cancel(self):
        """取消 = **关掉这一窗** + 通知主窗收尾（数据不动）。

        ★ 血训（用户实拍：「右下角的取消键点了没反应」）：
          原来这个按钮直接绑 `on_cancel`（= `App._cancel_review`），
          而那个函数只清 `pending` / `_cap_ctx`、**不关窗** —— 于是点下去界面纹丝不动，
          用户只能判断"按钮坏了"。其实数据早就清干净了，是**窗没走**。
          教训：**"取消"是一个动作，不是一次回调** —— 关窗与通知必须绑死在一起，
          而且**所有出口共用同一条路**（按钮 / 标题栏 × / Esc），少绑一个就是同一个 bug
          换一个入口再犯一次（标题栏 × 只 destroy 不通知，会留下脏 `pending`，
          下一次主窗采集就会写进脚本的 steps）。
        """
        self.close()
        try:
            self.on_cancel()
        except Exception:                                       # noqa: BLE001
            pass

    def close(self):
        try:
            self.grab_release()
        except Exception:
            pass
        try:
            self.destroy()
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# 选窗口
# --------------------------------------------------------------------------- #
class WindowPicker(tk.Toplevel):
    def __init__(self, app, on_pick):
        super().__init__(app)
        self.title("选择目标窗口")
        self.configure(bg=BG)
        self.geometry("640x420+%d+%d" % (app.winfo_rootx() + 100, app.winfo_rooty() + 80))
        self.attributes("-topmost", True)
        self.grab_set()
        self.on_pick = on_pick

        tk.Label(self, text="选一个目标：整个屏幕，或某个具体窗口", bg=BG, fg=FG,
                 font=FONT_B).pack(anchor="w", padx=12, pady=(12, 4))
        tk.Label(self, text="★「整个屏幕」= 不认窗口，抓主屏、坐标就是屏幕坐标；"
                            "全屏游戏 / 无边框程序 / 一个流程跨好几个窗口 → 选它",
                 bg=BG, fg=OKC, font=FONT_S).pack(anchor="w", padx=12)
        tk.Label(self, text="⚠ 别选本工作台自己 —— 那会把自己的界面当成目标",
                 bg=BG, fg=WARNC, font=FONT_S).pack(anchor="w", padx=12)
        wrap = tk.Frame(self, bg=BG)
        wrap.pack(fill="both", expand=True, padx=12, pady=10)
        self.lb = tk.Listbox(wrap, bg=PANEL, fg=FG, font=FONT_LOG, bd=0,
                             selectbackground=ACCENT,
                             selectforeground=ON_ACCENT_FG,
                             highlightthickness=0)
        sb = _scrollbar(wrap, command=self.lb.yview)
        self.lb.configure(yscrollcommand=sb.set)
        self.lb.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.lb.bind("<Double-Button-1>", lambda _e: self._pick())

        self.items = []
        self._fill()

        bar = tk.Frame(self, bg=BG)
        bar.pack(fill="x", padx=12, pady=(0, 12))
        _btn(bar, "就是它", self._pick, "primary").pack(side="left")
        _btn(bar, "取消", self.destroy).pack(side="left", padx=6)
        _btn(bar, "重新扫描", self._fill).pack(side="right")

    def _fill(self):
        self.lb.delete(0, "end")
        self.items = []
        sw, sh = winio.primary_screen_size()
        self.items.append((SCREEN_SENTINEL, "整个屏幕", "screen", sw, sh))
        self.lb.insert("end", "★  整个屏幕（不指定窗口）   [screen]  %dx%d" % (sw, sh))
        self.items.append((None, "", "", 0, 0))
        self.lb.insert("end", "─" * 54)
        try:
            for hwnd, title, cls in winio.list_visible_windows():
                _x, _y, w, h = winio.window_rect(hwnd)
                self.items.append((hwnd, title, cls, w, h))
                self.lb.insert("end", "    %s   [%s]  %dx%d" % (title, cls, w, h))
        except Exception as exc:
            self.items.append((None, "", "", 0, 0))
            self.lb.insert("end", "枚举窗口失败：%s" % exc)

    def _pick(self):
        sel = self.lb.curselection()
        if not sel:
            return
        hwnd, title, cls, w, h = self.items[sel[0]]
        if hwnd is None:                      # 分隔行，不响应
            return
        try:
            self.grab_release()
        except Exception:
            pass
        self.destroy()
        self.on_pick(hwnd, title)


# --------------------------------------------------------------------------- #
# 脚本仓库：已编好的自动化脚本，集中存放
# --------------------------------------------------------------------------- #
class ScriptNameDialog(tk.Toplevel):
    """新建脚本 —— **只问名字和简介**，内容随后靠点选生成。

    ★ 为什么这里没有"写脚本内容"的框：
      用户明确要求"全程只需点选、填表和确认，**不需要手写任何脚本代码**"。
      脚本正文 = 步骤列表，每一步都是【采集本步画面】→ 在静止的帧上点两个点 →
      复核 → 保存 这条链路生成的 —— 任何一处都不需要打字写指令。
      给个文本框反而是在暗示"这里该写代码"，把人往错路上带。

    `mode`（P7 加的第二个用法，**只改文案，不改行为**）：
      · `"new"`  —— 新建**空**脚本，随后去标第 1 步
      · `"save"` —— 把**工作台当前标好的步骤**存成一条（【存入仓库】的回程路）
    两种用法问的都是同一件事（名称 + 简介），所以共用这一个对话框 ——
    另写一个只会让"名称不能为空"这类规矩有两份实现，早晚走偏。
    """

    def __init__(self, app, on_ok, mode="new"):
        super().__init__(app)
        self.on_ok = on_ok
        self.mode = mode
        if mode == "save":
            title, head = "存入脚本仓库", "把工作台当前的步骤存成一条脚本"
            hint = ("给它起个名字。存进去的是「工作台里已经标齐的那几步」—— "
                    "帧与模板会复制一份到这条脚本名下，工作台这边原样不动。")
            ok_text = "存进仓库"
        else:
            title, head = "新建脚本", "新建脚本"
            hint = ("先给个名字。建好之后在右边编辑区点【采集本步画面】标第 1 步 —— "
                    "一步一幅画面，点两下就成，不用写代码。")
            ok_text = "建好，开始标第 1 步"
        self.title(title)
        self.configure(bg=BG)
        self.resizable(False, False)
        self.attributes("-topmost", True)
        self.grab_set()

        tk.Label(self, text=head, bg=BG, fg=FG, font=FONT_T).pack(
            anchor="w", padx=16, pady=(14, 2))
        tk.Label(self, text=hint,
                 bg=BG, fg=MUTED, font=FONT_S, wraplength=DLG_WRAP,
                 justify="left").pack(anchor="w", padx=16)

        form = tk.Frame(self, bg=BG)
        form.pack(fill="x", padx=16, pady=(14, 0))
        tk.Label(form, text="脚本名称", bg=BG, fg=FG, font=FONT_B).pack(anchor="w")
        self.ent_name = tk.Entry(form, bg=PANEL, fg=FG, font=FONT_UI, bd=0,
                                 insertbackground=FG, highlightthickness=1,
                                 highlightbackground=LINE, highlightcolor=ACCENT)
        self.ent_name.pack(fill="x", ipady=5, pady=(3, 0))
        tk.Label(form, text="简介（列表里显示这一行，可不填）", bg=BG, fg=MUTED,
                 font=FONT_S).pack(anchor="w", pady=(10, 0))
        self.ent_desc = tk.Entry(form, bg=PANEL, fg=FG, font=FONT_UI, bd=0,
                                 insertbackground=FG, highlightthickness=1,
                                 highlightbackground=LINE, highlightcolor=ACCENT)
        self.ent_desc.pack(fill="x", ipady=5, pady=(3, 0))

        bar = tk.Frame(self, bg=BG)
        bar.pack(fill="x", padx=16, pady=(18, 14))
        self.btn_ok = _btn(bar, ok_text, self._ok, "ok")
        self.btn_ok.pack(side="left")
        _btn(bar, "取消", self.close).pack(side="left", padx=8)
        self.ent_name.focus_set()
        self.bind("<Return>", lambda _e: self._ok())
        self.bind("<Escape>", lambda _e: self.close())

        # ★ 尺寸**按内容实测**定，不写死像素（P7b —— 用户实拍"存入仓库的配置框太小"）。
        #   为什么必须实测：两种模式的提示文案长短不同 ——
        #   「新建脚本」一行、「存入仓库」三行。写死高度要么裁掉按钮、要么留一大片空白。
        #   实测（改前）：内容实需 520×360（new）/ 520×384（save），而窗口恒为 280 ——
        #   **两种模式都矮**，save 模式更是把简介框和【存进仓库】整个顶到窗底之外。
        #   顺带对字体 / DPI 变化免疫（写死像素的话，换台机器又不够）。
        #   ⚠ 必须在**所有子控件都 pack 完**之后量：先 update_idletasks 让 Tk 算好
        #     请求尺寸，再取 reqheight —— 提前量拿到的是半成品。
        self.update_idletasks()
        self._fit_to_content(app)

    def _fit_to_content(self, app):
        """把窗口调到**刚好装下内容**（宽度有下限，免得窄得难看）。"""
        w = max(DLG_W, self.winfo_reqwidth())
        h = max(320, self.winfo_reqheight())
        x = app.winfo_rootx() + 160
        y = app.winfo_rooty() + 110
        # 长高了就别再开出屏幕下边（旧框矮，以前不显这个问题）
        try:
            x = max(10, min(x, self.winfo_screenwidth() - w - 20))
            y = max(10, min(y, self.winfo_screenheight() - h - 60))
        except Exception:
            pass
        self.geometry("%dx%d+%d+%d" % (w, h, x, y))

    def _ok(self):
        name = self.ent_name.get().strip()
        if not name:
            # ⚠ 名称为空必须**拦住并说明**：仓库里出现一堆"未命名"就谁也认不出来了
            messagebox.showinfo("还差一步",
                                "给这个脚本起个名字吧 —— 不然仓库里认不出它。",
                                parent=self)
            self.ent_name.focus_set()
            return
        self.on_ok(name, self.ent_desc.get().strip())
        self.close()

    def close(self):
        try:
            self.grab_release()
        except Exception:
            pass
        try:
            self.destroy()
        except Exception:
            pass


class ScriptRepoWindow(tk.Toplevel):
    """**脚本仓库** = 左「存储区」+ 右「编辑区」双区。

    ## 为什么分成两区（以及为什么不再有"手写内容"的框）

    旧版是"列表 + 双击弹一个编辑框"，编辑框里一大片自由文本 —— 那等于**让用户手写
    脚本代码**，被明确否掉了。现在的分工是：

        左 存储区 —— 所有脚本的列表（搜索 / 排序 / 新建 / 删除）
        右 编辑区 —— 选中那条脚本的**查看与修改入口**（名称/简介 + 步骤列表）

    ★ **选中即加载**：在存储区点一下，内容立刻进编辑区；改完回写**同一条记录**
      （同一个 id），不是另存一份 —— "改了半天存到别处"是最难查的一种丢数据。

    ## 全程点选，没有一处要写脚本代码

      · 步骤怎么来 —— 点【采集本步画面】：程序自己抓帧 → 在静止的帧上点识别点 /
        操作点 → 复核 → 保存。**与主窗的【采集本步画面】是同一条链路**
        （见 `App.begin_script_capture` / `App._cap_ctx`），不另写一份，免得走偏。
      · 顺序怎么调 —— 上移 / 下移 / 插入 / 删除按钮。
      · 双击还是单击 —— 点一下单选。
      · 名称 / 简介 —— 填表 + 【保存修改】。

    ## 为什么做成独立窗口，而不是嵌进主窗第三栏

    主窗的布局被 `tools/verify_smallwin.py` 的 **143 项几何断言**盯着
    （最小尺寸 880×630、每个控件的可见性都逐尺寸量过）。
    往 `mid` 里再塞一栏，等于把这些断言全部推倒重来，且窄窗下必然挤压 ——
    用户实拍已经证实过"多塞一行就被挤没"。

    → 做成 **Toplevel**：主窗**一个控件、一个 pack 顺序都不动**，
      仓库爱多宽多宽，跟主窗的尺寸约束彻底解耦。
      这也是程序里既有的做法（`WindowPicker` / `ReviewDialog` 都是 Toplevel）。

    ## 数据流（单向，好排查）

        用户动作 → scripts_repo 改内存 → save_repo 落盘 → 刷新两区

    ★ **结构类改动立刻落盘**（步骤增删移位、采集保存）：仓库是用户资产，
      不能等"关窗口才存"。★ **名称/简介这类填表改动**走【保存修改】确认；
      切换选中或关窗时若还有没存的，会**自动带上并提示**，不默默丢。
    """

    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title("脚本仓库")
        self.configure(bg=BG)
        self.geometry("1240x880+%d+%d" % (app.winfo_rootx() + 40,
                                          app.winfo_rooty() + 40))
        self.minsize(1000, 620)
        self.attributes("-topmost", True)

        self.repo = scripts_repo.load_repo()
        self._shown = []            # 存储区：行号 → script dict
        self._cur_id = None         # 编辑区当前装着哪一条（**按 id 记**，见 `_cur`）
        self._dirty = False         # 名称/简介有没有改过还没存

        # ---- 标题条 ----
        head = tk.Frame(self, bg=PANEL)
        head.pack(fill="x")
        # ★【关闭】放**标题栏右上角**（先 pack，抢位优先）。
        #   用户实拍报"关闭按钮被挤的很小" —— 原位置在底部按钮排的最右端，
        #   实测只剩 24px（所需 74，窗口收窄到 1100 时塌成 1px，等于看不见）。
        #   挪到这里：① 谁也挤不到它；② 语义上当位（关窗属于窗口，不属于数据操作）。
        self.btn_close = _btn(head, "关闭", self.close)
        self.btn_close.pack(side="right", padx=(0, 12), pady=8)
        tk.Label(head, text="左＝存储区（点一条即载入右边）　右＝编辑区（改完回写原脚本）",
                 bg=PANEL, fg=OKC, font=FONT_S).pack(side="right", padx=(0, 10))
        tk.Label(head, text="脚本仓库", bg=PANEL, fg=FG, font=FONT_T).pack(
            side="left", padx=(14, 8), pady=10)
        self.lb_count = tk.Label(head, text="", bg=PANEL, fg=MUTED, font=FONT_S)
        self.lb_count.pack(side="left")

        # ⚠ 底部提示条要**先占住底边**（`side="bottom"`），再 pack 主体 ——
        #   顺序反了的话，`expand=True` 的主体会把提示条整个推出窗外
        #   （第一版就是这么干的，实拍里提示条不见了）。
        #   与主窗日志区"先 side=bottom 占位"是同一个道理。
        self.lb_hint = tk.Label(self, text="", bg=BG, fg=MUTED, font=FONT_S)
        self.lb_hint.pack(side="bottom", fill="x", padx=14, pady=(6, 8))

        body = tk.Frame(self, bg=BG)
        body.pack(fill="both", expand=True)
        # ★★ 双区骨架用 **grid 按权重分配**，不用"左栏死宽 620 + pack"。
        #   为什么（实测）：左栏写死 `width=620` + `pack_propagate(False)`，
        #   窗口收到 1000 宽时，620 的死宽 + 边距把右栏压到只剩 **344px** ——
        #   右栏按钮排所需 486px，于是整排被压扁，【关闭】更是只剩 24px。
        #   grid 两列都 `weight=1` 后，两栏**同步收缩**，谁也不欺负谁；
        #   `minsize` 保证任何一栏都不会窄到装不下自己的内容。
        body.columnconfigure(0, weight=11, minsize=420)   # 存储区
        body.columnconfigure(1, weight=9, minsize=360)    # 编辑区
        body.rowconfigure(0, weight=1)

        # ================= 左：存储区 =================
        left = tk.Frame(body, bg=BG)
        left.grid(row=0, column=0, sticky="nsew", padx=(12, 6), pady=(10, 0))
        left.pack_propagate(False)

        tk.Label(left, text="存储区 · 全部脚本", bg=BG, fg=FG, font=FONT_B).pack(
            anchor="w")
        tools = tk.Frame(left, bg=BG)
        tools.pack(fill="x", pady=(6, 0))
        tk.Label(tools, text="搜索", bg=BG, fg=MUTED, font=FONT_S).pack(side="left")
        self.var_kw = tk.StringVar(value="")
        self.ent_kw = tk.Entry(tools, textvariable=self.var_kw, bg=PANEL, fg=FG,
                               font=FONT_UI, bd=0, insertbackground=FG,
                               highlightthickness=1, highlightbackground=LINE,
                               highlightcolor=ACCENT, width=16)
        self.ent_kw.pack(side="left", padx=(6, 0), ipady=4)
        # 边打边筛（key 松开就刷新）—— 脚本数量不大，实时筛比按回车更顺手
        self.ent_kw.bind("<KeyRelease>", lambda _e: self._refresh())
        _btn(tools, "清除", self._clear_kw).pack(side="left", padx=(4, 0))
        tk.Label(tools, text="排序", bg=BG, fg=MUTED, font=FONT_S).pack(
            side="left", padx=(12, 0))
        self.var_sort = tk.StringVar(
            value=scripts_repo.SORT_LABEL_BY_KEY[scripts_repo.SORT_UPDATED_DESC])
        self.cmb_sort = ttk.Combobox(
            tools, textvariable=self.var_sort, state="readonly",
            values=[lab for lab, _k in scripts_repo.SORT_LABELS], width=12,
            font=FONT_UI)
        self.cmb_sort.pack(side="left", padx=(6, 0))
        self.cmb_sort.bind("<<ComboboxSelected>>", lambda _e: self._refresh())

        # 表头：**单独一行 Label**，用与列表行**完全相同**的 `_pad` 列宽拼出来 ——
        #   两边走同一个格式函数，中文全角宽度才对得上
        #   （第一版表头用 4 个 Label 靠 width 参数对齐，实拍列全歪了）。
        hd = tk.Label(left, text=_pad("名称", 14) + "  " + _pad("更新时间", 19)
                      + "  " + _pad("简介", 12),
                      bg=PANEL, fg=MUTED, font=FONT_LOG, anchor="w")
        hd.pack(fill="x", ipady=3, pady=(8, 0), padx=(6, 0))

        lbox = tk.Frame(left, bg=BG)
        lbox.pack(fill="both", expand=True)
        # ⚠ `exportselection=0` 是**必须的**（与主窗步骤列表同一个坑）：
        #   Tk 默认会把选中发布为 X11 PRIMARY selection ——
        #   一旦搜索框接管了 selection owner，列表选中就被自动清空，
        #   用户会看到"我明明选了脚本，一点搜索就没了"。
        self.lst = tk.Listbox(lbox, bg=PANEL, fg=FG, font=FONT_LOG, bd=0,
                              selectbackground=ACCENT,
                              selectforeground=ON_ACCENT_FG,
                              highlightthickness=0, activestyle="none",
                              exportselection=0)
        lsb = _scrollbar(lbox, command=self.lst.yview)
        self.lst.configure(yscrollcommand=lsb.set)
        self.lst.pack(side="left", fill="both", expand=True)
        lsb.pack(side="right", fill="y")
        self.lst.bind("<<ListboxSelect>>", self._on_pick_script)

        lbar = tk.Frame(left, bg=BG)
        lbar.pack(fill="x", pady=(8, 0))
        self.btn_new2 = _btn(lbar, "＋ 新建脚本", self._new, "primary")
        self.btn_new2.pack(side="left")
        self.btn_del = _btn(lbar, "删除", self._delete_script, "danger")
        self.btn_del.pack(side="left", padx=6)
        self.btn_refresh = _btn(lbar, "重新读盘", self._reload)
        self.btn_refresh.pack(side="left")

        # ================= 右：编辑区 =================
        right = tk.Frame(body, bg=BG)
        right.grid(row=0, column=1, sticky="nsew", padx=(6, 12), pady=(10, 0))

        tk.Label(right, text="编辑区 · 选中脚本的查看与修改入口", bg=BG, fg=FG,
                 font=FONT_B).pack(anchor="w")

        card = tk.Frame(right, bg=PANEL2)
        card.pack(fill="x", pady=(6, 0))
        r1 = tk.Frame(card, bg=PANEL2)
        r1.pack(fill="x", padx=10, pady=(8, 0))
        tk.Label(r1, text="名称", bg=PANEL2, fg=MUTED, font=FONT_S,
                 width=6, anchor="w").pack(side="left")
        self.ent_name = tk.Entry(r1, bg=PANEL, fg=FG, font=FONT_UI, bd=0,
                                 insertbackground=FG, highlightthickness=1,
                                 highlightbackground=LINE, highlightcolor=ACCENT)
        self.ent_name.pack(side="left", fill="x", expand=True, ipady=4)
        r2 = tk.Frame(card, bg=PANEL2)
        r2.pack(fill="x", padx=10, pady=(6, 0))
        tk.Label(r2, text="简介", bg=PANEL2, fg=MUTED, font=FONT_S,
                 width=6, anchor="w").pack(side="left")
        self.ent_desc = tk.Entry(r2, bg=PANEL, fg=FG, font=FONT_UI, bd=0,
                                 insertbackground=FG, highlightthickness=1,
                                 highlightbackground=LINE, highlightcolor=ACCENT)
        self.ent_desc.pack(side="left", fill="x", expand=True, ipady=4)
        # ⚠ 两个输入框都要绑：改动只在**内存**里，靠【保存修改】或切换选中时带上
        for _e in (self.ent_name, self.ent_desc):
            _e.bind("<KeyRelease>", self._on_form_edit)
        r3 = tk.Frame(card, bg=PANEL2)
        r3.pack(fill="x", padx=10, pady=(6, 8))
        self.lb_time = tk.Label(r3, text="", bg=PANEL2, fg=MUTED, font=FONT_S)
        self.lb_time.pack(side="left")
        self.lb_dirty = tk.Label(r3, text="", bg=PANEL2, fg=WARNC, font=FONT_S)
        self.lb_dirty.pack(side="left", padx=(10, 0))
        self.lb_note = tk.Label(r3, text="", bg=PANEL2, fg=MUTED, font=FONT_S,
                                anchor="e")
        self.lb_note.pack(side="right")

        # ★★ 底部按钮排**先 `side="bottom"` 占位**，再 pack 上面的内容。
        #   为什么必须这样（实测数据）：右栏控件固定高度累加 = 753px，
        #   而窗口 1100x700 时右栏只有 579px —— 按 pack 顺序从后往前砍，
        #   排在最后的 bar 首当其冲被压到 y=651（跑到可视区外），
        #   里面的按钮全塌成 1px（1000x700 / 1000x620 实测）。
        #   Tk 的规矩是"先 pack 先保位"，所以让底部条先占住底边，
        #   再由中间的步骤列表 `expand=True` 去伸缩 —— 窗口矮只会让它变矮，
        #   底部按钮永远完整。
        #   （与主窗日志区、本窗提示条是同一个道理，见上面 lb_hint 的注释。）
        #
        # ★ `grid` 2×2 而不是 `pack(side="left")` 一字排开：
        #   五个按钮一字排开共需 **634px**，而右栏在默认 1240 窗口下只有 584px，
        #   本来就装不下 —— 排最后的【关闭】实测只剩 24px（见下）。
        #   这一排**只放"对这条脚本做什么"**（保存 / 另存 / 载入 / 删除）；
        #   "对这一步做什么"（采集本步画面）归**步骤区**，两组语义不混排。
        bar = tk.Frame(right, bg=BG)
        bar.pack(side="bottom", fill="x", pady=(10, 0))
        for _c in range(2):
            bar.columnconfigure(_c, weight=1, uniform="btns")
        self.btn_save = _btn(bar, "保存修改", self._save_edit, "ok")
        self.btn_save.grid(row=0, column=0, sticky="ew", padx=(0, 4), pady=(0, 6))
        self.btn_save_as = _btn(bar, "另存为新脚本", self._dup)
        self.btn_save_as.grid(row=0, column=1, sticky="ew", padx=(4, 0),
                              pady=(0, 6))
        self.btn_to_bench = _btn(bar, "载入到工作台", self._load_to_workbench)
        self.btn_to_bench.grid(row=1, column=0, sticky="ew", padx=(0, 4))
        self.btn_del2 = _btn(bar, "删除脚本", self._delete_script, "danger")
        self.btn_del2.grid(row=1, column=1, sticky="ew", padx=(4, 0))

        # ---- 步骤列表（**脚本正文就在这里**，全部点选生成） ----
        s_head = tk.Frame(right, bg=BG)
        s_head.pack(fill="x", pady=(10, 0))
        self.lb_steps_n = tk.Label(s_head, text="步骤（0 步）", bg=BG, fg=FG,
                                   font=FONT_B)
        self.lb_steps_n.pack(side="left")
        tk.Label(s_head, text="每步都由【采集本步画面】点出来 —— 不用写代码",
                 bg=BG, fg=MUTED, font=FONT_S).pack(side="left", padx=(10, 0))

        # ★ 步骤列表**可伸缩**（`expand=True`）：窗口矮时优先让它变矮 ——
        #   它是唯一"变矮不影响可用性"的区域（有滚动条，少显示几行无所谓）。
        sbox = tk.Frame(right, bg=BG)
        sbox.pack(fill="both", expand=True, pady=(4, 0))
        self.steps_lst = tk.Listbox(sbox, height=4, bg=PANEL, fg=FG,
                                    font=FONT_LOG, bd=0, selectbackground=ACCENT,
                                    selectforeground=ON_ACCENT_FG,
                                    highlightthickness=0, activestyle="none",
                                    exportselection=0)
        ssb = _scrollbar(sbox, command=self.steps_lst.yview)
        self.steps_lst.configure(yscrollcommand=ssb.set)
        self.steps_lst.pack(side="left", fill="both", expand=True)
        ssb.pack(side="right", fill="y")
        self.steps_lst.bind("<<ListboxSelect>>", lambda _e: self._show_step_detail())

        # ★ 步骤级操作（含**采集本步画面**）归这一条 —— 与脚本级那排分开。
        #   同样 `side="bottom"` 先占位，免得窄窗被挤出。
        #   用 `grid` **两行**：第一行是"改这一步"的主操作（采集 + 增删），
        #   第二行是"调顺序"。为什么不一行六列等宽 —— 实测窄窗下
        #   6 列 × 【采集本步画面】所需 154px = 924px 超出右栏可用宽度，
        #   采集按钮被压 63px（本门断言实测抓到）。
        sbar = tk.Frame(right, bg=BG)
        sbar.pack(side="bottom", fill="x", pady=(6, 0))
        for _c in range(4):
            sbar.columnconfigure(_c, weight=1, uniform="sbtns")
        # 【采集本步画面】是这一排里最长的（6 字），给它**额外权重**才不会被压
        #   （实测：4 列等宽在 1000 宽窗口下它被压 16px）。
        #   加权后它分到的宽度明显多于其余三列，文字不再贴边。
        sbar.columnconfigure(0, weight=2, uniform="")
        self.btn_capture = _btn(sbar, "采集本步画面", self._capture_step, "primary")
        self.btn_capture.grid(row=0, column=0, sticky="ew", padx=(0, 3))
        self.btn_add = _btn(sbar, "新增一步", lambda: self._add_step())
        self.btn_add.grid(row=0, column=1, sticky="ew", padx=3)
        self.btn_ins = _btn(sbar, "插入一步", self._insert_step)
        self.btn_ins.grid(row=0, column=2, sticky="ew", padx=3)
        self.btn_del_step = _btn(sbar, "删除本步", self._del_step, "danger")
        self.btn_del_step.grid(row=0, column=3, sticky="ew", padx=(3, 0))
        self.btn_up = _btn(sbar, "上移", lambda: self._move(-1))
        self.btn_up.grid(row=1, column=0, sticky="ew", padx=(0, 3), pady=(6, 0))
        self.btn_down = _btn(sbar, "下移", lambda: self._move(1))
        self.btn_down.grid(row=1, column=1, sticky="ew", padx=3, pady=(6, 0))

        # 操作类型：**点一下就改**，与复核弹窗里那组单选写同一份数据
        ct = tk.Frame(right, bg=PANEL2)
        ct.pack(fill="x", pady=(8, 0))
        tk.Label(ct, text="这一步怎么点", bg=PANEL2, fg=FG, font=FONT_B).pack(
            side="left", padx=(10, 6), pady=5)
        self._ct_var = tk.StringVar(value="single")
        for _txt, _val in (("单击（默认）", "single"), ("双击", "double")):
            tk.Radiobutton(ct, text=_txt, variable=self._ct_var, value=_val,
                           command=self._on_ct_change, bg=PANEL2, fg=FG,
                           selectcolor=BG, font=FONT_UI, activebackground=PANEL2,
                           activeforeground=FG, bd=0, highlightthickness=0,
                           ).pack(side="left")
        self.lb_ct_note = tk.Label(ct, text="", bg=PANEL2, fg=MUTED, font=FONT_S)
        self.lb_ct_note.pack(side="left", padx=(10, 0))

        # 详情：**只读**。看得到每一步的落点/状态，但改不了数值 ——
        #   数值一律由采集链路生成，手写相对坐标没有意义且必然出错。
        self.detail = tk.Text(right, height=5, bg=PANEL, fg=FG, font=FONT_LOG,
                              bd=0, highlightthickness=0, wrap="word")
        self.detail.pack(fill="x", pady=(8, 0))
        self.detail.configure(state="disabled")

        self._refresh()
        self.lst.focus_set()
        self.bind("<Escape>", lambda _e: self.close())
        self.protocol("WM_DELETE_WINDOW", self.close)

    # ---------------------------------------------------------------- #
    # 取当前对象
    # ---------------------------------------------------------------- #
    def _cur(self):
        """编辑区当前那条。**每次按 id 现查** —— 不缓存 dict。

        ★ 为什么缓存 dict 会出事：另存为 / 重新读盘 / 删除都会换掉 repo 里的对象，
          手里那份就成了"过期副本"，往里写等于写到空气里（用户看到"改了没生效"）。
        """
        if not self._cur_id:
            return None
        return scripts_repo.find(self.repo, self._cur_id)

    def _cur_sort(self):
        lab = self.var_sort.get()
        for l, k in scripts_repo.SORT_LABELS:
            if l == lab:
                return k
        return scripts_repo.SORT_UPDATED_DESC

    def _selected_step_index(self):
        sel = self.steps_lst.curselection()
        if not sel:
            return None
        s = self._cur()
        if s is None or sel[0] >= len(scripts_repo.steps_of(s)):
            return None
        return sel[0]

    # ---------------------------------------------------------------- #
    # 存储区
    # ---------------------------------------------------------------- #
    def _refresh(self):
        """按当前关键词 + 排序重建存储区列表。

        ⚠ 刷新后**恢复选中**（按 id 找，不按行号）：
          行号会因为筛选/排序变化而改变，按 id 找才稳。
          （主窗的 `_refresh_steps` 也是这个道理，见那里的注释。）
        """
        kw = self.var_kw.get()
        items = scripts_repo.query(self.repo, kw, self._cur_sort())
        self._shown = []
        self.lst.delete(0, "end")
        for s in items:
            self._shown.append(s)
            # ⚠ 列对齐**必须走 `_pad`**（按显示宽度补齐）——
            #   `%-20s` 按字符数算，中文占两个半角宽，行会越排越歪（实拍验证过）
            self.lst.insert("end", (
                _pad(s.get("name") or "", 14) + "  "
                + _pad(s.get("updated_at") or "—", 19) + "  "
                + scripts_repo.brief(s, 12)))
        if not items:
            self.lst.insert("end", ("    （仓库是空的，点【＋ 新建脚本】建一个）"
                                    if not (kw or "").strip()
                                    else "    （没有名称匹配「%s」的脚本）" % kw))

        if self._cur_id:
            for row, s in enumerate(self._shown):
                if s.get("id") == self._cur_id:
                    self.lst.selection_set(row)
                    self.lst.see(row)
                    break
        n_all = scripts_repo.count(self.repo)
        if (kw or "").strip():
            self.lb_count.configure(text="共 %d 个脚本，筛出 %d 个" % (n_all, len(items)))
        else:
            self.lb_count.configure(text="共 %d 个脚本" % n_all)
        self.lb_hint.configure(
            text="数据文件：config/scripts.json（独立存放，改这里不会影响主窗的步骤配置）")
        self._sync_buttons()

    def _on_pick_script(self, _event=None):
        """存储区点一下 → **内容自动载入编辑区**（需求：选中即加载）。"""
        sel = self.lst.curselection()
        if not sel or sel[0] >= len(self._shown):
            return
        s = self._shown[sel[0]]
        if s.get("id") == self._cur_id:
            return
        self._flush_if_dirty(silent=True)      # 上一条还有没存的改动 → 先带上
        self._load(s)

    def _load(self, script):
        """把一条脚本装进编辑区。"""
        self._cur_id = script.get("id")
        for _e in (self.ent_name, self.ent_desc):
            _e.configure(state="normal")       # 先放开再写：可能刚被置灰过
        self.ent_name.delete(0, "end")
        self.ent_name.insert(0, script.get("name") or "")
        self.ent_desc.delete(0, "end")
        self.ent_desc.insert(0, script.get("desc") or "")
        self._dirty = False
        self.lb_time.configure(
            text="创建 %s　更新 %s" % (script.get("created_at") or "—",
                                       script.get("updated_at") or "—"))
        old = (script.get("content") or "").strip()
        if old:
            # 老版本"手写正文"字段：**只读保留**，绝不丢用户数据
            self.lb_note.configure(
                text="旧格式正文（只读，已保留）：%s%s"
                     % (old[:28].replace("\n", " "), "…" if len(old) > 28 else ""))
        else:
            self.lb_note.configure(text="")
        self._refresh_steps(select=0)

    def _clear_edit(self):
        """清空编辑区（没有选中任何脚本时）。"""
        self._cur_id = None
        for _e in (self.ent_name, self.ent_desc):
            _e.configure(state="normal")
            _e.delete(0, "end")
        self._dirty = False
        self.lb_time.configure(text="")
        self.lb_note.configure(text="")
        self.steps_lst.delete(0, "end")
        self.lb_steps_n.configure(text="步骤（0 步）")
        self._show_step_detail()
        self._sync_buttons()

    def _clear_kw(self):
        self.var_kw.set("")
        self._refresh()
        self.ent_kw.focus_set()

    def _reload(self):
        """从盘上重读（多开窗口 / 手改过文件时用）。"""
        self.repo = scripts_repo.load_repo()
        self._cur_id = None
        self._refresh()
        self._clear_edit()
        self.app._log("脚本仓库：已重新读盘，共 %d 个脚本"
                      % scripts_repo.count(self.repo), "info")

    def _sync_buttons(self):
        has_script = self._cur() is not None
        has_step = self._selected_step_index() is not None
        st = "normal" if has_script else "disabled"
        st2 = "normal" if has_step else "disabled"
        for w in (self.btn_del, self.btn_del2, self.btn_save, self.btn_save_as,
                  self.btn_capture, self.btn_add, self.btn_to_bench):
            w.configure(state=st)
        for w in (self.btn_ins, self.btn_up, self.btn_down, self.btn_del_step):
            w.configure(state=st2)
        self.ent_name.configure(state="normal" if has_script else "disabled")
        self.ent_desc.configure(state="normal" if has_script else "disabled")
        if not has_script:
            self.lb_dirty.configure(text="")

    # ---------------------------------------------------------------- #
    # 编辑区 · 步骤
    # ---------------------------------------------------------------- #
    def _refresh_steps(self, select=None):
        s = self._cur()
        self.steps_lst.delete(0, "end")
        steps = scripts_repo.steps_of(s) if s else []
        for i, stx in enumerate(steps):
            miss = storage.step_missing(stx)
            mark = "✓" if not miss else "缺" + "".join(
                {"识别点": "点", "识别框": "框", "模板": "模", "操作点": "操"}.get(m, "?")
                for m in miss)
            self.steps_lst.insert("end", "%2d. %-14s %s"
                                  % (i + 1, stx.get("name", ""), mark))
        self.lb_steps_n.configure(text="步骤（%d 步）" % len(steps))
        if steps:
            row = len(steps) - 1 if select is None else max(0, min(len(steps) - 1, select))
            self.steps_lst.selection_clear(0, "end")
            self.steps_lst.selection_set(row)
            self.steps_lst.see(row)
        self._show_step_detail()
        self._sync_buttons()
        return len(steps)

    def _show_step_detail(self):
        self.detail.configure(state="normal")
        self.detail.delete("1.0", "end")
        s = self._cur()
        i = self._selected_step_index()
        if s is None:
            self.detail.insert("end", "← 在左边存储区点一条脚本，或点【＋ 新建脚本】。\n")
            self.detail.configure(state="disabled")
            self.lb_ct_note.configure(text="")
            return
        steps = scripts_repo.steps_of(s)
        if not steps:
            self.detail.insert("end", "这条脚本还没有步骤 —— 点【采集本步画面】标第 1 步。\n")
            self.detail.configure(state="disabled")
            self.lb_ct_note.configure(text="")
            return
        if i is None:
            self.detail.insert("end", "← 在上面点一步，看它标到了哪儿。\n")
            self.detail.configure(state="disabled")
            self.lb_ct_note.configure(text="")
            return
        stx = steps[i]
        a = stx.get("anchor") or {}
        act = stx.get("action") or {}
        ct = picker.norm_click_type(act.get("click_type"))
        self._ct_var.set(ct)
        self.lb_ct_note.configure(text="第 %d 步" % (i + 1))
        lines = ["第 %d 步　%s　（id=%s）" % (i + 1, stx.get("name", ""), stx.get("id"))]
        if a.get("point"):
            lines.append("识别点  相对 x=%.4f  y=%.4f" % tuple(a["point"]))
            box = a.get("box") or [0, 0, 0, 0]
            lines.append("识别框  相对 w=%.4f  h=%.4f（程序自动裁的）" % (box[2], box[3]))
        else:
            lines.append("识别点  还没标 —— 点【采集本步画面】")
        if act.get("same_as_anchor"):
            lines.append("操作点  同识别点")
        elif act.get("point"):
            lines.append("操作点  相对 x=%.4f  y=%.4f　落点半径 %d px"
                         % (act["point"][0], act["point"][1], act.get("radius", 6)))
        else:
            lines.append("操作点  还没标")
        lines.append("操作类型  %s" % ("双击" if ct == picker.CLICK_DOUBLE else "单击"))
        miss = storage.step_missing(stx)
        lines.append("状态  " + ("✅ 数据齐了" if not miss else "⚠ 还缺：" + "、".join(miss)))
        self.detail.insert("end", "\n".join(lines) + "\n")
        self.detail.configure(state="disabled")

    def _on_ct_change(self):
        """操作类型：点一下即改、即存（与复核弹窗里那组单选同一份数据）。"""
        s = self._cur()
        i = self._selected_step_index()
        if s is None or i is None:
            return
        steps = scripts_repo.steps_of(s)
        act = steps[i].setdefault("action", {})
        want = picker.norm_click_type(self._ct_var.get())
        if picker.norm_click_type(act.get("click_type")) == want:
            return
        act["click_type"] = want
        self._commit("脚本仓库：第 %d 步的操作类型改为「%s」"
                     % (i + 1, "双击" if want == picker.CLICK_DOUBLE else "单击"))
        self._show_step_detail()

    # ---------------------------------------------------------------- #
    # 落盘
    # ---------------------------------------------------------------- #
    def _commit(self, note=None, tag="ok"):
        """落盘 + 刷新两区 —— **所有改动都走这一个出口**（见类注释的数据流）。"""
        ok = scripts_repo.save_repo(self.repo)
        self._refresh()
        cur = self._cur()
        if cur is not None:
            self._refresh_steps(select=self._selected_step_index())
        if note:
            self.app._log(note, tag if ok else "err")
        if not ok:
            messagebox.showerror("保存失败",
                                 "写 config/scripts.json 失败。改动只在内存里，"
                                 "关掉窗口就会丢 —— 请检查 config 目录是否可写。",
                                 parent=self)
        return ok

    def _on_form_edit(self, _event=None):
        """名称/简介被改动 → 标脏（**不立刻落盘**：填表类改动要用户点确认）。"""
        s = self._cur()
        if s is None:
            return
        dirty = (self.ent_name.get().strip() != (s.get("name") or "")
                 or self.ent_desc.get().strip() != (s.get("desc") or ""))
        self._dirty = dirty
        self.lb_dirty.configure(text="有未保存的修改" if dirty else "")

    def _flush_if_dirty(self, silent=False):
        """切换选中 / 关窗前把填表改动带上 —— 不默默丢，也不弹窗拦人。"""
        if not self._dirty:
            return False
        s = self._cur()
        if s is None:
            return False
        name = self.ent_name.get().strip()
        desc = self.ent_desc.get().strip()
        scripts_repo.update(self.repo, s["id"], name or s.get("name"), desc)
        self._dirty = False
        self.lb_dirty.configure(text="")
        if not silent:
            self.app._log("脚本仓库：已保存「%s」的名称/简介" % (name or s.get("name")), "ok")
        return True

    def _save_edit(self):
        s = self._cur()
        if s is None:
            messagebox.showinfo("先选一个", "在左边存储区点一下要改的那条脚本。",
                                parent=self)
            return
        name = self.ent_name.get().strip()
        if not name:
            messagebox.showinfo("还差一步",
                                "名字不能是空的 —— 不然仓库里认不出它。", parent=self)
            self.ent_name.focus_set()
            return
        self._dirty = True                       # 走同一条落盘路径，省得两处逻辑分叉
        self._flush_if_dirty()
        it = scripts_repo.find(self.repo, s["id"])
        self._commit("脚本仓库：已保存「%s」的修改" % it["name"])

    # ---------------------------------------------------------------- #
    # 结构类改动（**点选即生效、立刻落盘**）
    # ---------------------------------------------------------------- #
    def _add_step(self):
        s = self._cur()
        if s is None:
            messagebox.showinfo("先选一个", "在左边存储区点一下要加步骤的那条脚本。",
                                parent=self)
            return
        scripts_repo.add_step(s)
        self._commit("脚本仓库：「%s」新增第 %d 步 —— 点【采集本步画面】标它"
                     % (s.get("name"), len(scripts_repo.steps_of(s))))

    def _insert_step(self):
        s = self._cur()
        i = self._selected_step_index()
        if s is None or i is None:
            messagebox.showinfo("先选一步", "在步骤列表里点一下要插在哪一步后面。",
                                parent=self)
            return
        scripts_repo.add_step(s, i + 1)
        self._commit("脚本仓库：已在第 %d 步之后插入一步（编号已重排）" % (i + 1))

    def _del_step(self):
        s = self._cur()
        i = self._selected_step_index()
        if s is None or i is None:
            messagebox.showinfo("先选一步", "在步骤列表里点一下要删的那一步。",
                                parent=self)
            return
        steps = scripts_repo.steps_of(s)
        if not messagebox.askyesno("确认", "删掉第 %d 步「%s」？"
                                   % (i + 1, steps[i].get("name", "")), parent=self):
            return
        steps.pop(i)
        scripts_repo.renumber_steps(s)
        self._commit("脚本仓库：已删除「%s」的第 %d 步" % (s.get("name"), i + 1), "warn")

    def _move(self, d):
        s = self._cur()
        i = self._selected_step_index()
        if s is None or i is None:
            return
        steps = scripts_repo.steps_of(s)
        j = i + d
        if not (0 <= j < len(steps)):
            return
        steps[i], steps[j] = steps[j], steps[i]      # 只换顺序，数据不动
        scripts_repo.renumber_steps(s)
        self._commit("脚本仓库：第 %d 步挪到第 %d 位" % (i + 1, j + 1))
        self._refresh_steps(select=j)

    # ---------------------------------------------------------------- #
    # 采集（**走原本那条交互链路**：程序抓帧 → 点识别点/操作点 → 复核 → 保存）
    # ---------------------------------------------------------------- #
    def _capture_step(self):
        s = self._cur()
        if s is None:
            messagebox.showinfo("先选一个", "在左边存储区点一下要标的那条脚本。",
                                parent=self)
            return
        idx = self._selected_step_index()
        if idx is None:
            if not scripts_repo.steps_of(s):
                # 一条步骤都没有 → 直接建第 1 步并标它（省一次点击，语义也清楚）
                scripts_repo.add_step(s)
                idx = 0
                self._refresh_steps(select=0)
            else:
                messagebox.showinfo("先选一步",
                                    "在步骤列表里点一下要标的那一步（或点【新增一步】）。",
                                    parent=self)
                return
        self.app.begin_script_capture(s, idx,
                                      on_saved=self._on_step_saved, win=self)

    def _on_step_saved(self, idx):
        """采集链路把这一步写进脚本了 → **回写原文件**并刷新编辑区。"""
        s = self._cur()
        name = (s or {}).get("name") or "脚本"
        self._commit("脚本仓库：已保存「%s」的第 %d 步" % (name, idx + 1))
        self._refresh_steps(select=idx)

    # ---------------------------------------------------------------- #
    # 新建 / 另存 / 载入 / 删除
    # ---------------------------------------------------------------- #
    def _new(self):
        def _on_ok(name, desc):
            # 脚本自带的参数：从工作台**拷一份**（参考分辨率留给第一张采集帧去量）
            cfg = {
                "reference": {"w": 0, "h": 0},
                "match": copy.deepcopy(self.app.profile.get("match") or {}),
                "execution": {"click_radius": int(
                    (self.app.profile.get("execution") or {}).get("click_radius", 6))},
            }
            item = scripts_repo.create(self.repo, name, desc, cfg=cfg)
            # 建好就带第 1 步 —— 新建脚本本来就是为了标步骤，省一次点击
            scripts_repo.add_step(item)
            self._commit("脚本仓库：已新建「%s」（%s）—— 点【采集本步画面】标第 1 步"
                         % (item["name"], item["id"]))
            self._load(item)
            self._refresh()
            self.steps_lst.selection_clear(0, "end")
            self.steps_lst.selection_set(0)

        ScriptNameDialog(self, _on_ok)

    def _dup(self):
        s = self._cur()
        if s is None:
            messagebox.showinfo("先选一个", "在左边存储区点一下要另存的那条脚本。",
                                parent=self)
            return
        self._flush_if_dirty(silent=True)
        it = scripts_repo.duplicate(self.repo, s["id"])
        if it is None:
            messagebox.showwarning("这条脚本不在了", "它可能已被删除。列表已刷新。",
                                   parent=self)
            self._refresh()
            return
        self._commit("脚本仓库：已另存为「%s」（%s，素材也复制了一份）"
                     % (it["name"], it["id"]))
        self._load(it)
        self._refresh()

    def _load_to_workbench(self):
        """把这条脚本**取出来接着用**：载入工作台的步骤列表（那才是能跑的地方）。"""
        s = self._cur()
        if s is None:
            messagebox.showinfo("先选一个", "在左边存储区点一下要载入的那条脚本。",
                                parent=self)
            return
        steps = scripts_repo.steps_of(s)
        if not steps:
            messagebox.showinfo("这条还是空的", "它还没有任何步骤 —— 先标几步再载入。",
                                parent=self)
            return
        self._flush_if_dirty(silent=True)
        app = self.app
        n_now = len(app.profile.get("steps", []))
        if not messagebox.askyesno(
                "载入到工作台",
                "把脚本「%s」的 %d 步载入工作台？\n\n"
                "这会替换工作台当前的 %d 步（config/profile.json），"
                "台面上没存的东西会被覆盖。" % (s.get("name"), len(steps), n_now),
                parent=self):
            return
        app.profile["steps"] = copy.deepcopy(steps)
        # 参考分辨率一并带走：步骤里的相对值是按这套分辨率标的，不带会裁错框
        cfg = scripts_repo.cfg_of(s)
        app.profile["reference"] = copy.deepcopy(cfg.get("reference") or {"w": 0, "h": 0})
        storage.save_profile(app.profile)
        app.templates = storage.load_all_templates(app.profile)
        app._refresh_steps()
        app._apply_mode_gate()
        app._log("已从脚本仓库载入「%s」（%d 步）到工作台 —— 切到执行模式即可跑"
                 % (s.get("name"), len(steps)), "ok")

    def _delete_script(self):
        s = self._cur()
        if s is None:
            messagebox.showinfo("先选一个", "在左边存储区点一下要删的那条脚本。",
                                parent=self)
            return False
        return self._delete_by_id(s["id"])

    def _delete_by_id(self, sid):
        it = scripts_repo.find(self.repo, sid)
        if it is None:
            self._refresh()
            return True
        if not messagebox.askyesno(
                "确认删除",
                "删掉脚本「%s」？\n\n这条记录会从仓库里移除，且不可撤销。"
                % it.get("name"), parent=self):
            return False
        scripts_repo.delete(self.repo, sid)
        self._cur_id = None
        self._commit("脚本仓库：已删除「%s」" % it.get("name"), "warn")
        self._clear_edit()
        return True

    def close(self):
        self._flush_if_dirty()          # 关窗前把没存的带上，别默默丢
        try:
            self.destroy()
        except Exception:
            pass

# --------------------------------------------------------------------------- #
class SettingsWindow(tk.Toplevel):
    """设置栏 —— 界面口味（目前是主题）都收在这一个窗口里。

    为什么要单开一栏、而不是把开关塞进主窗：
      · 顶栏横向是**有预算**的（880 宽下五个控件已排到 906px），多一个按钮就得
        从"目标名 / 状态文字"身上抠 —— 那是信息，不是装饰；
      · 设置是**低频动作**，低频动作不该占常驻版面。

    尺寸照 `ScriptNameDialog._fit_to_content` 的规矩**按内容实测**：写死几何
    在改了提示文案之后必然裁掉按钮。
    """

    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title("设置")
        self.configure(bg=PANEL)
        self.resizable(False, False)
        self.transient(app)

        head = tk.Frame(self, bg=PANEL)
        head.pack(fill="x")
        tk.Label(head, text="设置", bg=PANEL, fg=FG, font=FONT_T).pack(
            side="left", padx=14, pady=10)
        self.btn_close = _btn(head, "关闭", self.close)
        self.btn_close.pack(side="right", padx=12, pady=8)

        tk.Frame(self, bg=LINE, height=1).pack(fill="x")

        body = tk.Frame(self, bg=BG)
        body.pack(fill="both", expand=True, padx=10, pady=10)

        box = tk.Frame(body, bg=PANEL)
        box.pack(fill="x")
        tk.Label(box, text="主题", bg=PANEL, fg=FG, font=FONT_B,
                 anchor="w").pack(anchor="w", padx=12, pady=(10, 4))
        row = tk.Frame(box, bg=PANEL)
        row.pack(anchor="w", padx=12)
        self.var_theme = tk.StringVar(value=THEME_NAME)
        for key in ("light", "dark"):
            rb = tk.Radiobutton(
                row, text="%s（%s）" % (THEME_CN[key], key),
                variable=self.var_theme, value=key,
                command=lambda k=key: self._pick(k),
                bg=PANEL, fg=FG, selectcolor=BG, font=FONT_UI,
                activebackground=PANEL, activeforeground=FG,
                bd=0, highlightthickness=0)
            rb.pack(side="left", padx=(0, 16))
        tk.Label(box, text="换肤当场生效，只改颜色、不动布局；下次启动照旧。",
                 bg=PANEL, fg=MUTED, font=FONT_S, anchor="w",
                 wraplength=340, justify="left").pack(
                     anchor="w", padx=12, pady=(8, 10))

        cur_hk = uiprefs.load().get("hud_hotkey") or HUD_HOTKEY_DEFAULT
        box2 = tk.Frame(body, bg=PANEL)
        box2.pack(fill="x", pady=(8, 0))
        tk.Label(box2, text="取点提示的隐藏快捷键", bg=PANEL, fg=FG, font=FONT_B,
                 anchor="w").pack(anchor="w", padx=12, pady=(10, 2))
        tk.Label(box2, text="取点遮罩铺满全屏时，屏幕正中央那条提示由它开关"
                            "（按一次隐藏、再按一次显示）。点下面的框，直接按你要的那个键即可。",
                 bg=PANEL, fg=MUTED, font=FONT_S, anchor="w",
                 wraplength=340, justify="left").pack(anchor="w", padx=12)
        row2 = tk.Frame(box2, bg=PANEL)
        row2.pack(anchor="w", padx=12, pady=(6, 10))
        self.var_hotkey = tk.StringVar(value=_hotkey_label(cur_hk))
        self.ent_hotkey = tk.Entry(row2, textvariable=self.var_hotkey, width=12,
                                   font=FONT_UI, bg=PANEL2, fg=FG, bd=0,
                                   readonlybackground=PANEL2, justify="center",
                                   highlightthickness=1,
                                   highlightbackground=LINE,
                                   insertbackground=FG, state="readonly")
        self.ent_hotkey.pack(side="left", ipady=4)
        self.ent_hotkey.bind("<KeyPress>", self._capture_key)
        self.btn_hk_default = _btn(row2, "恢复默认", self._reset_hotkey)
        self.btn_hk_default.pack(side="left", padx=(8, 0))

        tk.Label(body, text="偏好存在 config/ui.json（与工作台的 profile.json 分开存，"
                            "改这里不会动工作台数据）",
                 bg=BG, fg=MUTED, font=FONT_S, anchor="w",
                 wraplength=360, justify="left").pack(anchor="w", pady=(8, 0))

        self._fit_to_content(app)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.bind("<Escape>", lambda _e: self.close())

    def _fit_to_content(self, app):
        self.update_idletasks()
        w = max(400, self.winfo_reqwidth())
        h = max(220, self.winfo_reqheight())
        try:
            x = app.winfo_rootx() + max(0, (app.winfo_width() - w) // 2)
            y = app.winfo_rooty() + 90
            sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
            x = min(max(0, x), max(0, sw - w))
            y = min(max(0, y), max(0, sh - h))
        except Exception:                                       # noqa: BLE001
            x = y = 120
        self.geometry("%dx%d+%d+%d" % (w, h, x, y))

    @staticmethod
    def _key_name(ev):
        """把一次按键翻译成"键名"（`F2` / `Control-h`）；只按修饰键则返回 None。"""
        ks = str(getattr(ev, "keysym", "") or "")
        if not ks or ks in ("Shift_L", "Shift_R", "Control_L", "Control_R",
                            "Alt_L", "Alt_R", "Super_L", "Super_R",
                            "Caps_Lock", "Num_Lock"):
            return None                     # 光按修饰键不算一个快捷键
        mods = []
        st = int(getattr(ev, "state", 0) or 0)
        if st & 0x4:                        # Tk: Control
            mods.append("Control")
        if st & 0x1:                        # Tk: Shift
            mods.append("Shift")
        if st & 0x8:                        # Tk: Mod1 = Alt
            mods.append("Alt")
        base = ks.lower() if len(ks) == 1 else ks
        return "-".join(mods + [base])

    def _capture_key(self, ev):
        """在输入框里按什么键，就把它设成开关键（不用手打键名）。"""
        name = self._key_name(ev)
        if not name:
            return "break"
        got = self.app._set_hud_hotkey(name)
        self.var_hotkey.set(_hotkey_label(got))
        return "break"          # 别让这次按键继续传播（Esc 会顺带把本窗关掉）

    def _reset_hotkey(self):
        got = self.app._set_hud_hotkey(HUD_HOTKEY_DEFAULT)
        self.var_hotkey.set(_hotkey_label(got))

    def _pick(self, key):
        """点一下单选 → 立刻换肤（本窗口自己也在被换的那棵树里，会一起变色）。"""
        self.app._set_theme(key)
        try:
            self.var_theme.set(THEME_NAME)
        except Exception:                                       # noqa: BLE001
            pass

    def close(self):
        try:
            self.app.win_settings = None
            self.destroy()
        except Exception:                                       # noqa: BLE001
            pass


class App(tk.Tk):
    def __init__(self):
        winio.set_dpi_aware()                      # 建窗口之前先声明
        super().__init__()
        # 主题：**先定色、再建控件** —— 所有控件的颜色在建的那一刻就从模块全局取。
        # 之后换肤只做"颜色改写"，不重建界面（见 `_apply_theme_colors`）。
        _tokens.apply(uiprefs.load().get("theme", DEFAULT_THEME))
        _reload_tokens()
        # ttk 主题必须**在建任何 ttk 控件之前**设好（下拉框 / 进度条的暗色在这条线上）
        _setup_ttk(self)
        self.profile = storage.load_profile()
        self.templates = storage.load_all_templates(self.profile)
        self.q = queue.Queue()
        self.stop_event = threading.Event()
        self.worker = None
        self.overlay = None                        # 兼作防重入锁
        self.pending = None                        # 取点/复核中途的暂存
        self.win_settings = None                   # 设置栏（单开窗口，最多一个）
        self._hidden_tops = []                     # 采帧前被收起的自家窗口（见 _hide_own_tops）
        self.target_hwnd = None
        self.mode = tk.StringVar(value="edit")
        # ⚠ `dry_var` 的语义是「是否**真实点击**」（见复选框文案"真实点击（不勾 = 干跑）"），
        #   所以**默认必须是 False = 不真实点击 = 干跑**。
        #   老代码写的是 `True`（默认勾上"真实点击"）：用户点【开始】以为只是试试，
        #   程序却立刻真去操作鼠标 —— 既危险，又让人以为"怎么自己动了/报错了"。
        #   默认干跑，要动鼠标得用户自己动手勾。
        self.dry_var = tk.BooleanVar(value=False)
        self.same_pt_var = tk.BooleanVar(value=False)
        self._log_font_size = 11
        self._capture_frame = None
        self._capture_client_rect = None
        self._running = False
        self._runlog_f = None                      # 执行日志文件句柄（每次【开始】重置）
        self._runlog_path = None
        # **执行互斥闸门**：任何时刻只允许一次执行（连点【开始】/ 双入口并发都挡住）。
        # 用非阻塞锁而非 bool 标志：bool 有「读-判-写」之间的竞态窗口，锁没有。
        # 释放点**唯一收口**在 `_on_running_change`（见该函数注释），避免漏放导致永久锁死。
        self._exec_lock = threading.Lock()
        self._step_view = []                       # 列表显示顺序 → steps 下标
        self._last_step_i = None                   # 最后一次**有效**选中的 steps 下标

        self.title("GUI 自动化工作台")
        self.configure(bg=BG)
        self.geometry("1180x760")
        # minsize 用**实测扫描**定的，不是拍的（tools/probe_minheight.py）：
        #   · 高度：<620 时日志区整条不见；620~660 日志体(文本)被挤掉只剩标题；
        #     ≥670 才全部可见。取 630 —— 左栏八颗按钮全部在位、日志标题条
        #     （含「收起/清空」）在位；日志**正文**是可折叠内容，矮窗下自然让位，
        #     点【收起】还更省地方，不算缺件。
        #   · 宽度：800 起就全可见（横向有截断保护），880 是舒适余量。
        # ⚠ 别改回 (1020, 660)：那个值虚高 —— 用户窗口比它矮，它既没拦住窗口变小，
        #   又让布局来不及收缩，控件被推出可视区（用户实拍已证实「下拉不见」）。
        self.minsize(880, 630)
        self._build()
        self._refresh_steps()
        self._apply_mode_gate()
        self._sync_target_label()
        self._print_boot()

        # **异常黑匣子**：任何 Tk 回调里逃逸的异常都落盘 logs/crash.txt + 界面标红。
        # 必须放在 _build 之后 —— 回显要用的 log/conclusion/status 均已建好。
        safeui.install_crash_handler(
            self, log_fn=self._log, status_fn=self._crash_status,
            mode_fn=lambda: self.mode.get())

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(80, self._drain)
        # 首帧兜底：布局未就绪时 _refresh_steps 里的量测会拿不到宽度，稍后再刷一遍
        self.after(50, self._refresh_steps)

    def _crash_status(self):
        """崩溃时把底栏状态 + 结论标红 —— 用户一眼就知道「出事了、证据在哪」。"""
        try:
            self._status("abort", "界面异常（见 logs/crash.txt）")
            self.conclusion.configure(
                text="⚠ 界面回调异常，已写入 logs/crash.txt", fg=ERRC)
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    # 界面
    # ------------------------------------------------------------------ #
    def _build(self):
        # 顶栏 —— ⚠ 这里最容易横向溢出：状态文字（"执行模式未解锁：第1步缺…"）
        # 一长就把右侧「选择目标窗口」顶出窗口。所以：
        #   ① 状态标签**限宽 + 超长省略**，并且允许被压缩（不再无限撑）；
        #   ② 右侧固定的按钮/标签**先 pack**，保证它们永远占位（Tk 里先 pack 先保位）。
        top = tk.Frame(self, bg=PANEL)
        top.pack(fill="x")

        # ⚠ 新增控件一律**从右侧最外侧往内排**（`side="right"` 且**先 pack**）：
        #   Tk 的 pack 是"先到先占位" —— 先 pack 的右侧控件永远保位，
        #   后来的内容再长也只会挤中间那两条**可伸缩的文本标签**（目标名 / 状态文字）。
        #   ⚠ 代价说清楚（P7 实测纠偏）：新按钮**会**把它左边那批控件
        #     （选择目标窗口 / 目标名）往左推，不是"一个都不动"。保证的是
        #     "按钮永远不被挤出去" —— 所以文本标签必须自己能截断
        #     （`_on_top_resize` / `_on_mode_resize`），否则推到极限就轮到它们消失。
        #   顺序：脚本仓库（最外）→ 存入仓库 → 选择目标窗口 → 目标名 → 标题 → 状态（可伸缩）。
        self.btn_repo = _btn(top, "脚本仓库", self._open_repo)
        self.btn_repo.pack(side="right", padx=(6, 14), pady=8)

        # 【存入仓库】—— 把当前工作台标好的步骤收成一条脚本资产（P7 补的回程路）。
        # ⚠ 位置：紧挨【脚本仓库】左侧。两者是一对（都关于"脚本资产"），
        #   且都在右侧外圈，**先 pack 先保位** —— 中间那条可伸缩的状态标签
        #   替它们让位，任何窗口宽度下都挤不掉（`verify_smallwin.py` 一起守）。
        self.btn_to_repo = _btn(top, "存入仓库", self._save_bench_to_repo)
        self.btn_to_repo.pack(side="right", padx=(6, 0), pady=8)

        self.btn_window = _btn(top, "选择目标窗口", self._choose_window)
        self.btn_window.pack(side="right", padx=(6, 14), pady=8)
        self.lb_win = tk.Label(top, text="未选择目标窗口", bg=PANEL, fg=MUTED,
                               font=FONT_UI)
        self.lb_win.pack(side="right")
        # ⚠ 目标名**可能很长**（窗口标题不设限），而它是 `side="right"` 的
        #   **定宽请求**控件、又排在状态文字之前 —— 长标题会把状态文字整个顶出去。
        #   实测（P7 加【存入仓库】那一轮）：880 宽下 lb_win 请求 286px，
        #   状态文字被挤到只剩 1px，等于看不见。给它一个**随顶栏剩余空间浮动的
        #   像素上限**，见 `_on_top_resize` / `_fit_target_label`。
        self._top = top
        self._win_full = "未选择目标窗口"
        top.bind("<Configure>", self._on_top_resize)

        tk.Label(top, text="GUI 自动化工作台", bg=PANEL, fg=FG,
                 font=FONT_T).pack(side="left", padx=(14, 8), pady=10)

        # 状态：限宽 + 截断，绝不允许把右侧顶走
        self.lb_mode = tk.Label(top, text="", bg=PANEL, fg=ACCENT, font=FONT_B,
                                anchor="w", width=1)
        self.lb_mode.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self._mode_full = ""            # 完整文案（截断前的原文）
        self._mode_font = None          # 量文字宽度用，首次用到再建
        self.lb_mode.bind("<Configure>", self._on_mode_resize)

        # 模式条
        mbar = tk.Frame(self, bg=PANEL2)
        mbar.pack(fill="x")
        self.rb_edit = tk.Radiobutton(mbar, text="① 编辑模式（标步骤）",
                                      variable=self.mode, value="edit",
                                      command=self._on_mode_change, bg=PANEL2,
                                      fg=FG, selectcolor=BG, font=FONT_B,
                                      activebackground=PANEL2,
                                      activeforeground=FG, bd=0,
                                      highlightthickness=0)
        self.rb_edit.pack(side="left", padx=(12, 4), pady=6)
        self.rb_run = tk.Radiobutton(mbar, text="② 执行模式（跑流程）",
                                     variable=self.mode, value="run",
                                     command=self._on_mode_change, bg=PANEL2,
                                     fg=FG, selectcolor=BG, font=FONT_B,
                                     activebackground=PANEL2,
                                     activeforeground=FG, bd=0,
                                     highlightthickness=0)
        self.rb_run.pack(side="left", padx=4, pady=6)
        self.lb_hint_mode = tk.Label(mbar, text="　两个模式互斥：编辑时不许跑，跑时不许改",
                                     bg=PANEL2, fg=MUTED, font=FONT_S)
        self.lb_hint_mode.pack(side="left")
        # 【设置】放**模式条右端**，不放顶栏 —— 顶栏横向是有预算的
        #   （实测 880 宽下五个控件已排到 906px，见 MEMORY 第 10 条 / `_on_top_resize`），
        #   再塞一个按钮就得从"目标名 / 状态文字"身上抠，得不偿失。
        #   模式条这条本来就空着大半（两个单选 + 一句提示 ≈ 460px），且"设置"与
        #   "模式"同属全局开关，语义也顺。侧边栏按钮一律**先 pack 先保位**。
        self.btn_settings = _btn(mbar, "设置 ⚙", self._open_settings)
        self.btn_settings.pack(side="right", padx=(0, 12), pady=4)

        # 主体
        self.mid = tk.Frame(self, bg=BG)
        self.mid.pack(fill="both", expand=True, padx=10, pady=(8, 4))
        mid = self.mid                   # 局部别名：下面沿用 mid 这个名字

        left = tk.Frame(mid, bg=BG, width=300)
        left.pack(side="left", fill="y")
        left.pack_propagate(False)          # 固定 300 宽，不被内容撑开
        # ⚠ 左栏是**固定 300px**（`pack_propagate(False)`），而字号随 DPI 缩放 ——
        #   150% 缩放下这行文案要 324px，会被 Label 默认的 center 锚点
        #   **从两头各裁掉约一个字**（实拍：显示成「骤（点一下选中它…」，丢了开头的「步」）。
        #   所以两条一起上：
        #     ① 文案收短 —— 150% 下 270px，留 30px 余量；
        #     ② 自锚 `anchor="w"` —— 万一更高 DPI 又超了，只吃掉尾巴
        #        （信息量最低的那截），而不是吃掉开头。
        #   根本教训：**固定像素宽度 + 随缩放变化的字号 = 迟早溢出**，
        #   而且现有布局断言抓不到（控件实得 300px > 1，过的了"没被压扁"的检查，
        #   但它的**文字**溢出了控件）—— 检查控件尺寸 ≠ 检查文字尺寸。
        tk.Label(left, text="步骤（点一下选中，随时可重标）", bg=BG, fg=MUTED,
                 font=FONT_S, anchor="w").pack(anchor="w", pady=(0, 4))
        # 列表：**吃掉左栏的剩余高度**（expand=True），但给按钮区留固定位置。
        # 这样窗口变矮时，先压缩列表，而不是把下面的按钮/日志挤出去。
        box = tk.Frame(left, bg=BG)
        box.pack(fill="both", expand=True)
        # ⚠ `exportselection=0` 是**必须的**，别删（这是"改完返回还是旧值"的元凶之一）：
        #   Tk 的 Listbox 默认 `exportselection=1`，意思是"我的选中要对外发布为
        #   X11 的 PRIMARY selection"。结果：**一旦别的控件（下拉、输入框）接管了
        #   selection owner，Listbox 的选中就被自动清空** —— `curselection()` 变空。
        #   于是 `_selected_index()` 返回 None → 下拉被重置成「单击」且置灰，
        #   用户看到的正是"我明明选了双击，回来还是单击"。
        #   关掉它：选中只属于这个 Listbox，谁抢都不丢。
        self.lst = tk.Listbox(box, width=34, height=6, bg=PANEL, fg=FG,
                              font=FONT_LOG, bd=0, selectbackground=ACCENT,
                              selectforeground=ON_ACCENT_FG,
                              highlightthickness=0,
                              activestyle="none", exportselection=0)
        sb = _scrollbar(box, command=self.lst.yview)
        self.lst.configure(yscrollcommand=sb.set)
        self.lst.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.lst.bind("<<ListboxSelect>>", lambda _e: self._show_detail())

        # 按钮区：**两列网格**排布（4 行压成 3 行），给列表多让出高度
        lbtns = tk.Frame(left, bg=BG)
        lbtns.pack(side="bottom", fill="x", pady=(6, 0))
        self.lbtns = lbtns                # 留引用：布局验证脚本要往这里注入病因
        self.btn_capture = _btn(lbtns, "采集本步画面", self._capture_for_step,
                                "primary")
        self.btn_capture.grid(row=0, column=0, columnspan=2, sticky="ew",
                              pady=(0, 4))
        self.btn_add = _btn(lbtns, "新增一步", self._add_step)
        self.btn_add.grid(row=1, column=0, sticky="ew", padx=(0, 3), pady=(0, 4))
        self.btn_ins = _btn(lbtns, "插入一步", self._insert_step)
        self.btn_ins.grid(row=1, column=1, sticky="ew", padx=(3, 0), pady=(0, 4))
        self.btn_up = _btn(lbtns, "上移", lambda: self._move(-1))
        self.btn_up.grid(row=2, column=0, sticky="ew", padx=(0, 3), pady=(0, 4))
        self.btn_down = _btn(lbtns, "下移", lambda: self._move(1))
        self.btn_down.grid(row=2, column=1, sticky="ew", padx=(3, 0), pady=(0, 4))
        self.btn_copy = _btn(lbtns, "复制上一步", self._dup_prev)
        self.btn_copy.grid(row=3, column=0, sticky="ew", padx=(0, 3))
        self.btn_del = _btn(lbtns, "删除", self._del_step, "danger")
        self.btn_del.grid(row=3, column=1, sticky="ew", padx=(3, 0))
        lbtns.columnconfigure(0, weight=1)
        lbtns.columnconfigure(1, weight=1)
        self.lb_hint_step = tk.Label(lbtns, text="删除 / 插入后编号自动重排",
                                     bg=BG, fg=MUTED, font=FONT_S)
        self.lb_hint_step.grid(row=4, column=0, columnspan=2, sticky="w",
                               pady=(4, 0))

        # ⚠ 操作类型选择器**不放左栏**（放这儿会被挤出去，详见 6.12 第 5 条）。
        #   左栏是 width=300 + pack_propagate(False) 的固定宽容器，高度由
        #   `mid` 自上而下算出来；按钮区本身要 ~193px，窗口一矮就没它的位置。
        #   搬到右栏（见下方 opt 行）—— 那里空间充裕，且与「真实点击」等
        #   "这一步怎么执行"的开关同侧，语义也更顺。

        # 右栏
        right = tk.Frame(mid, bg=BG)
        right.pack(side="left", fill="both", expand=True, padx=(12, 0))

        # 操作类型直改条：**选中某步就能改它怎么点**，不用重新取点。
        # 与复核弹窗里那组单选写**同一份数据**（都是 action.click_type），
        # 这里改 = 立刻落盘 —— 是需求"支持再次修改"的常驻入口。
        #
        # ⚠ 放右栏第一行（side="top" 先抢位），不放左栏：
        #   左栏高度吃紧，这类"新加的一行"最容易被挤没（用户实拍已证实）。
        #   放这儿还有个好处 —— 它就在详情框上方，改完立刻能看到详情里
        #   的「操作类型」跟着变，改没改成功一眼可见。
        ct_bar = tk.Frame(right, bg=PANEL2)
        ct_bar.pack(side="top", fill="x", pady=(0, 6))
        self._ct_bar = ct_bar            # 留引用：布局验证脚本要量它的可见性
        tk.Label(ct_bar, text="这一步怎么点", bg=PANEL2, fg=FG,
                 font=FONT_B).pack(side="left", padx=(10, 4), pady=5)
        # ⚠ 初值必须是**中文标签**，不能是内部存储值。
        #   老代码写 `value="single"` —— 于是"还没选中任何步骤"时，右栏直接显示
        #   英文 `single`，而下拉选项是「单击 / 双击」。既露了内部字段，又和选项对不上，
        #   用户还会以为"这里坏了"。初始态与 `_CT_LABEL.get(None, "单击")` 保持一致。
        self.click_type_var = tk.StringVar(value="单击")
        self.cmb_click_type = ttk.Combobox(
            ct_bar, textvariable=self.click_type_var, state="disabled",
            values=("单击", "双击"), width=8, font=FONT_UI)
        self.cmb_click_type.pack(side="left", pady=5)
        self.cmb_click_type.bind("<<ComboboxSelected>>", self._on_click_type_pick)
        self.lb_ct_note = tk.Label(ct_bar, text="", bg=PANEL2, fg=MUTED,
                                   font=FONT_S)
        self.lb_ct_note.pack(side="left", padx=(8, 0))

        self.detail = tk.Text(right, height=6, bg=PANEL, fg=FG, font=FONT_LOG,
                              bd=0, highlightthickness=0, wrap="word")
        self.detail.pack(fill="x")
        self.detail.configure(state="disabled")

        opt = tk.Frame(right, bg=BG)
        opt.pack(fill="x", pady=(8, 0))
        tk.Checkbutton(opt, text="操作点 = 识别点（少点一下）",
                       variable=self.same_pt_var, bg=BG, fg=FG, selectcolor=BG,
                       font=FONT_UI, activebackground=BG, activeforeground=FG,
                       bd=0, highlightthickness=0).pack(side="left")
        tk.Checkbutton(opt, text="真实点击（不勾 = 干跑，只识别不点）",
                       variable=self.dry_var, bg=BG, fg=WARNC, selectcolor=BG,
                       font=FONT_UI, activebackground=BG, activeforeground=FG,
                       bd=0, highlightthickness=0).pack(side="left", padx=(18, 0))

        # 运行控制按钮：**两行**排布，窄窗口也不会把「日志字大」挤没
        runbar = tk.Frame(right, bg=BG)
        runbar.pack(fill="x", pady=(8, 0))
        self.btn_start = _btn(runbar, "开始", self._start, "ok", width=8)
        self.btn_start.pack(side="left")
        self.btn_stop = _btn(runbar, "停止", self._stop, "danger", width=8)
        self.btn_stop.pack(side="left", padx=(6, 0))
        self.btn_probe = _btn(runbar, "看一眼当前画面", self._probe)
        self.btn_probe.pack(side="left", padx=(6, 0))

        fbar = tk.Frame(right, bg=BG)
        fbar.pack(fill="x", pady=(6, 0))
        tk.Label(fbar, text="日志字号", bg=BG, fg=MUTED, font=FONT_S).pack(
            side="left")
        self.btn_log_small = _btn(fbar, "小", lambda: self._log_font(-1))
        self.btn_log_small.pack(side="left", padx=(6, 0))
        self.btn_log_big = _btn(fbar, "大", lambda: self._log_font(1))
        self.btn_log_big.pack(side="left", padx=(4, 0))

        # 状态栏（底栏）：与下面的日志区**视觉分开** —— 状态是"现在在干什么"，
        # 日志是"刚才发生了什么"，混在一起会看串。
        # ⚠ 只加底色区分，**不动控件层级、不动任何 pady/padx 数值**
        # （63 项小窗布局断言卡着这些几何值，动了就破坏"零变化"约束）。
        prog = tk.Frame(right, bg=PANEL2)
        prog.pack(fill="x", pady=(10, 0))
        self.lb_status = tk.Label(prog, text="待命", bg=PANEL2, fg=FG, font=FONT_B,
                                  anchor="w")
        self.lb_status.pack(side="left", fill="x", expand=True)
        self.lb_step = tk.Label(prog, text="第 0/0 步", bg=PANEL2, fg=MUTED,
                                font=FONT_UI, anchor="e")
        self.lb_step.pack(side="right", padx=(8, 0))
        self.pb = ttk.Progressbar(right, mode="determinate", maximum=100,
                                  style="Dark.Horizontal.TProgressbar")
        self.pb.pack(fill="x", pady=(4, 0))

        self.conclusion = tk.Label(right, text="", bg=BG, fg=MUTED, font=FONT_B,
                                   anchor="w", justify="left", wraplength=520)
        self.conclusion.pack(fill="x", pady=(8, 0))
        # 换行宽度绑到右栏实际宽度 —— 窗口变窄时结论不再横向溢出
        right.bind("<Configure>", self._fit_wraplength)

        # 日志 —— **可折叠**。折叠起来只留一条标题，展开时占固定高度。
        # 关键：日志区用 side="bottom" 先占位，绝不会被上面的内容挤出窗口。
        logf = tk.Frame(self, bg=PANEL)
        logf.pack(side="bottom", fill="x", padx=10, pady=(0, 10))
        self._logf = logf                # 供布局验证脚本引用
        head = tk.Frame(logf, bg=PANEL)
        head.pack(fill="x")
        tk.Label(head, text="运行日志", bg=PANEL, fg=MUTED, font=FONT_S).pack(
            side="left", padx=8, pady=4)
        self.btn_log_toggle = _btn(head, "收起 ▾", self._toggle_log,
                                   width=None)
        self.btn_log_toggle.pack(side="right", padx=8, pady=3)
        self.btn_clear = _btn(head, "清空", self._clear_log)
        self.btn_clear.pack(side="right", pady=3)

        self._log_body = tk.Frame(logf, bg=PANEL)
        self._log_body.pack(fill="x")
        self._log_open = True
        self.log = tk.Text(self._log_body, height=8, bg=LOG_BG, fg=FG,
                           font=FONT_LOG, bd=0, highlightthickness=0,
                           wrap="word")
        lsb = _scrollbar(self._log_body, command=self.log.yview)
        self.log.configure(yscrollcommand=lsb.set, state="disabled")
        self.log.pack(side="left", fill="both", expand=True)
        # 滚动条**先不 pack** —— 未满一屏时不占宽度（见 `_log_sb_recheck`）。
        # 旧代码在这里恒 pack，于是空日志旁边也杵着一条滑轨，白占 12px。
        self._log_sb = lsb
        self._log_sb_shown = False        # 当前是否已 pack（去抖用）
        self._log_sb_job = None           # after 句柄（合并高频复判）
        for tag, col in (("info", FG), ("ok", OKC), ("warn", WARNC),
                         ("err", ERRC), ("muted", MUTED)):
            self.log.tag_configure(tag, foreground=col)

        # **按钮回调解耦**：给所有交互按钮的 command 再包一层 guarded。
        # 为什么不用 _btn() 工厂统一包？—— 工厂 73-90 是**纯样式**函数，
        # 往里塞行为会污染所有按钮语义（含 PickOverlay 里的按钮）。
        # 所以只在这里、只针对主窗交互按钮显式包裹。幂等哨兵保证不会重复包。
        self._guard_buttons()

    def _guard_buttons(self):
        """把主窗交互按钮的 command 逐个包 `safeui.guarded`。

        与 `install_crash_handler` 是**双保险**：handler 管「Tk 自己捕获到并交给我们」的异常，
        这里管「连 Tk 都没机会记」的极端情况。两者都落同一条 crash.txt 通道。

        ⚠ **不要**从 `btn.cget("command")` 取回调再包 —— `cget` 返回的是 Tcl 命令名
        **字符串**（形如 `2045456653120'..._add_step'`），不是 Python 可调用对象，
        包它会得到一个「调用即 TypeError」的假包裹（异常还被吞掉，按钮静默失效）。
        所以这里用**显式映射**取原始绑定方法。
        """
        m = {
            "btn_start": self._start,
            "btn_stop": self._stop,
            "btn_probe": self._probe,
            "btn_capture": self._capture_for_step,
            "btn_add": self._add_step,
            "btn_ins": self._insert_step,
            "btn_del": self._del_step,
            "btn_copy": self._dup_prev,
            "btn_up": lambda: self._move(-1),
            "btn_down": lambda: self._move(1),
            "btn_log_toggle": self._toggle_log,
            "btn_clear": self._clear_log,
            "btn_repo": self._open_repo,
            "btn_to_repo": self._save_bench_to_repo,
            "btn_window": self._choose_window,
        }
        for n, raw in m.items():
            b = getattr(self, n, None)
            if b is None:
                continue
            try:
                b.configure(command=safeui.guarded(raw, context=n,
                                                   log_fn=self._log))
            except Exception as exc:               # noqa: BLE001
                # 包裹失败不能反过来让界面起不来 —— 退回未包裹（有类级 handler 兜底）
                self._log("按钮 %s 安全包裹失败（已退回未包裹）：%s" % (n, exc),
                          "warn")

    # ------------------------------------------------------------------ #
    # 小工具
    # ------------------------------------------------------------------ #
    # ------------------------------------------------------------------ #
    # 设置栏 / 换肤
    # ------------------------------------------------------------------ #
    def _set_hud_hotkey(self, name):
        """改「取点提示」的开关快捷键：存 config/ui.json，**下一次取点生效**。

        为什么不做"当场热更"：遮罩是模态的、铺满全屏，改的时候它要么没开
        （那就没得更新），要么正开着（用户在遮罩里，改不到设置栏）——
        下一次取点读一次配置，语义最简单也最不容易错。
        """
        got = _hotkey_seq(name)[1:-1]        # 过一遍白名单，非法值自动退回默认
        d = uiprefs.load()
        d["hud_hotkey"] = got
        uiprefs.save(d)
        self._log("取点提示快捷键已改为：%s（下次取点生效）" % _hotkey_label(got))
        return got

    def _open_settings(self):
        """【设置 ⚙】—— 单开一栏（独立窗口）。界面口味都收在这里，不占主窗版面。"""
        try:
            if self.win_settings is not None and self.win_settings.winfo_exists():
                self.win_settings.lift()
                self.win_settings.focus_force()
                return
        except Exception:                                       # noqa: BLE001
            pass
        self.win_settings = SettingsWindow(self)

    def _set_theme(self, name):
        """换肤：**只改颜色，不重建界面** —— 改完当场生效，并记住到下次启动。

        ★ 为什么不重建：重建会动几何。本项目 148 项布局断言守着"四档窗口尺寸下
          关键控件全可见"，重建一旦漏还原状态（选中 / 模式 / 日志折叠）就是静默回归。
          颜色映射只碰颜色选项，尺寸字体层级一个不碰 —— 天生安全。
        """
        old = THEME_NAME
        name = _tokens.apply(name)
        bgmap, fgmap = _tokens.color_maps(old, name)
        _reload_tokens()                # 先把新值搬进本模块全局（`import *` 只是拷贝）
        _setup_ttk(self)                # ttk（下拉 / 进度条 / 滚动条）走同一条线
        n = _apply_theme_colors(self, bgmap, fgmap)
        # 日志是 Text + tag 着色，不走控件选项，得单独刷一遍
        for tag, col in (("info", FG), ("ok", OKC), ("warn", WARNC),
                         ("err", ERRC), ("muted", MUTED)):
            try:
                self.log.tag_configure(tag, foreground=col)
            except Exception:                                   # noqa: BLE001
                pass
        uiprefs.save({"theme": name})
        if name != old:
            self._log("主题已切换：%s（已记住，下次启动照旧；共改写 %d 处颜色）"
                      % (THEME_CN.get(name, name), n))
        return n

    def _toggle_log(self):
        """收起 / 展开日志区。折叠时只留标题行 —— 给主区域让出高度。"""
        self._log_open = not self._log_open
        if self._log_open:
            self._log_body.pack(fill="x")
            self.btn_log_toggle.configure(text="收起 ▾")
        else:
            self._log_body.pack_forget()
            self.btn_log_toggle.configure(text="展开 ▴")

    def _set_mode_text(self, full, color=None):
        """设置顶栏状态文字。

        ⚠ 必须走这里，不要直接 configure —— 要同时记下**完整文案**，
        再交给 `_on_mode_resize()` 按当前宽度截断。
        直接写死文本的话，窄窗口下这段字会把右侧按钮顶出可视区。
        """
        self._mode_full = full or ""
        if color is not None:
            self.lb_mode.configure(fg=color)
        self._on_mode_resize()

    def _sync_mode_nav(self):
        """把「当前在哪个模式」**画出来**。

        ★ 原来两个模式项**同样是亮白**，用户只能靠前头那颗小圆点猜自己在哪一档 ——
          而圆点在暗底上几乎看不见。导航不标出"你在哪"，就等于没有导航。
        ★ 选中 = 品牌色，未选中 = 次要色；切换时**渐变色**过去（`_anim_opt`）——
          这也是这一版加的动效之一：让"换挡"这个过程看得见。
        ⚠ 只改 `fg`，不碰任何 pady/padx —— 布局断言钉着几何值。
        """
        cur = self.mode.get()
        for rb, val in ((self.rb_edit, "edit"), (self.rb_run, "run")):
            try:
                _anim_opt(rb, "fg", ACCENT if cur == val else MUTED, MOTION_BASE)
            except Exception:                                   # noqa: BLE001
                pass

    def _on_mode_resize(self, _ev=None):
        """状态文字太长就截断加省略号 —— 绝不让它把右侧按钮顶出窗口。"""
        full = getattr(self, "_mode_full", "") or ""
        avail = self.lb_mode.winfo_width()
        if avail <= 1:
            return                                   # 还没真正布局，别瞎量
        # 字体对象缓存：resize 会高频触发，每次 new 一个 Font 太浪费
        font = getattr(self, "_mode_font", None)
        if font is None:
            font = self._mode_font = tkfont.Font(font=FONT_B)
        if font.measure(full) <= avail:
            self.lb_mode.configure(text=full)
            return
        ell = "…"
        lo, hi = 0, len(full)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if font.measure(full[:mid] + ell) <= avail:
                lo = mid
            else:
                hi = mid - 1
        self.lb_mode.configure(text=(full[:lo] + ell) if lo > 0 else ell)

    def _on_top_resize(self, _ev=None):
        """顶栏一变宽，就重算目标名能占多少像素。

        ★ 为什么要算：顶栏里 `side="right"` 的控件是**先 pack 先保位**，
          而目标名夹在按钮与状态文字中间。它是文本、又可长可短（窗口标题不设限）,
          一旦它把地方吃满，排在它后面的状态文字就整个消失 ——
          而"（执行模式未解锁：第 1 步缺识别点…）"恰恰是要给用户看的那句。

        ★ 上限为什么**只依赖别的控件**（不含 lb_win 自己）：
          若按自身实际宽度算，会跟 pack 的分配互相反馈 ——
          裁短 → 请求变小 → 可用空间变大 → 又变长 → 再裁短，来回震荡。
          外层算出来的值只跟窗口宽、别的控件请求宽有关，一次收敛。
        """
        top = getattr(self, "_top", None)
        lb = getattr(self, "lb_win", None)
        if top is None or lb is None:
            return
        try:
            if not top.winfo_exists() or top.winfo_width() <= 1:
                return                        # 还没真正布局，别瞎量
            others = 0
            for w in top.winfo_children():
                if w is lb:
                    continue
                others += w.winfo_reqwidth()
            cap = (top.winfo_width() - others - TOP_STATUS_MIN_W
                   - 24)                      # 24 = 各控件的 padx 零头
        except Exception:
            return
        self._fit_target_label(max(TOP_TARGET_MIN_W, cap))

    def _fit_target_label(self, cap_px=None):
        """把目标名按 `cap_px` 像素截断加省略号（`cap_px=None` = 不裁）。

        ⚠ 与 `_on_mode_resize` 的差别（别照着抄错）：那边按**自己实际分到的宽度**裁，
          因为它 `expand=True`、宽度由剩余空间决定，裁完不影响布局；
          这边是**定宽请求**控件，所以按外部算出来的上限裁（理由见 `_on_top_resize`）。
        """
        full = getattr(self, "_win_full", "") or ""
        if cap_px is None:
            try:
                self.lb_win.configure(text=full)
            except Exception:
                pass
            return
        font = getattr(self, "_win_font", None)
        if font is None:
            font = self._win_font = tkfont.Font(font=FONT_UI)
        try:
            if font.measure(full) <= cap_px:
                self.lb_win.configure(text=full)
                return
            ell = "…"
            lo, hi = 0, len(full)
            while lo < hi:
                mid = (lo + hi + 1) // 2
                if font.measure(full[:mid] + ell) <= cap_px:
                    lo = mid
                else:
                    hi = mid - 1
            self.lb_win.configure(text=(full[:lo] + ell) if lo > 0 else ell)
        except Exception:
            pass

    def _fit_wraplength(self, _ev=None):
        """把结论标签的换行宽度绑到右栏实际宽度 —— 窄窗口不再横向溢出。"""
        try:
            w = self.conclusion.master.winfo_width()
            self.conclusion.configure(wraplength=max(200, w - 20))
        except Exception:
            pass

    def _print_boot(self):
        self._log("画面一律由程序自己抓 —— 你只需要【选一步】+【点两个点】，"
                  "不用截图、不用画记号。", "ok")
        self._log("步骤：① 选目标 —— 可选某个窗口，也可直接选「整个屏幕」"
                  "（全屏游戏/无边框程序/跨窗口流程用后者）；"
                  "② 在列表里选中要标的那一步　③ 把目标程序操作到那一步该有的画面　"
                  "④ 点【采集本步画面】　⑤ 在静止画面上单击识别点、再单击操作点", "info")
        self._log("识别框由程序自动裁（你不用拖框），裁完当场给你看模板和匹配得分。", "muted")
        # ⚠ 界面文案里**不许写 markdown**（红线 6）：日志框不认语法，
        #   `**` 会原样显示成星号。这里老代码漏了，实拍确认"默认**干跑**"照原样打了出来。
        self._log("默认干跑（只识别不点击）；确认无误后再勾「真实点击」。", "warn")

    def _log(self, text, tag="info"):
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n", tag)
        self.log.see("end")
        self.log.configure(state="disabled")
        self._log_sb_schedule()
        self._runlog_write(text, tag)

    # ------------------------------------------------------------------ #
    # 执行日志落盘（黑匣子的"执行版"）
    # ------------------------------------------------------------------ #
    def _runlog_open(self, dry):
        """点【开始】时开一份执行日志文件 —— 跑完能对着它复盘。

        为什么要有：`logs/crash.txt` 只记**界面回调**的异常；执行过程
        （识别得分、点击坐标、验证结果、中止原因）全在内存里滚过就没了。
        用户报"跑出错"时，没有这份文件就只能靠来回问，慢且不准。
        每次执行**独立一份**，带上时间戳，不覆盖旧记录。
        """
        try:
            import time as _t
            d = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
            os.makedirs(d, exist_ok=True)
            name = "run_%s.txt" % _t.strftime("%Y%m%d_%H%M%S")
            self._runlog_path = os.path.join(d, name)
            self._runlog_f = open(self._runlog_path, "w", encoding="utf-8")
            self._runlog_write("=" * 60)
            self._runlog_write("执行开始　%s　模式=%s"
                               % (_t.strftime("%Y-%m-%d %H:%M:%S"),
                                  "干跑" if dry else "真实点击"))
            self._runlog_write("目标=%s　步骤数=%d"
                               % (storage.target_desc(self.profile),
                                  len(self.profile.get("steps", []))))
            for i, st in enumerate(self.profile.get("steps", [])):
                a = st.get("action") or {}
                # ⚠ 写**收敛后的实际生效值**，不是原始值：
                #   原始值可能是 None / 脏值，而引擎那边会收敛成 single 再执行。
                #   日志里写 None 会让人以为"没生效"（旧日志就出现过这种误导）。
                self._runlog_write(
                    "  第%d步 %s  click_type=%s(原始=%r)  anchor=%s"
                    % (i + 1, st.get("id"),
                       picker.norm_click_type(a.get("click_type")),
                       a.get("click_type"),
                       bool((st.get("anchor") or {}).get("point"))))
            self._runlog_write("=" * 60)
            return self._runlog_path
        except Exception:
            self._runlog_path = None
            self._runlog_f = None
            return None

    def _runlog_write(self, text, tag="info"):
        f = getattr(self, "_runlog_f", None)
        if f is None:
            return
        try:
            f.write("[%s] %s\n" % (tag, text))
            f.flush()
        except Exception:
            pass

    def _runlog_close(self):
        f = getattr(self, "_runlog_f", None)
        if f is not None:
            try:
                self._runlog_write("=" * 60)
                self._runlog_write("执行结束")
                f.close()
            except Exception:
                pass
        self._runlog_f = None

    # ------------------------------------------------------------------ #
    # 日志滚动条：**延迟复判**
    # ------------------------------------------------------------------ #
    def _log_sb_schedule(self):
        """安排一次延迟复判（合并高频调用 —— 每插入一行都算一次太浪费）。"""
        if self._log_sb_job is not None:
            return
        try:
            self._log_sb_job = self.after(120, self._log_sb_recheck)
        except Exception:
            self._log_sb_job = None

    def _log_sb_recheck(self):
        """日志满一屏才显示滚动条 —— **必须带去抖**。

        ⚠ 为什么不能直接「超了就 pack、没超就 forget」：
        滚动条 pack/forget 会改变日志框宽度 → 触发重新换行 → 行数变化 →
        又反过来决定要不要显示滚动条 —— 形成**抖动**（滚动条疯狂闪）。
        所以只在**显示状态真的翻转**时才动 pack 状态。
        """
        self._log_sb_job = None
        try:
            # 首尾可见行号都拿到 = 内容确实溢出了（yview 返回 (首, 尾) 的 0~1 比例）
            first, last = self.log.yview()
            need = (last - first) < 0.999999
        except Exception:
            return
        if need == self._log_sb_shown:
            return                       # 状态没变 —— 什么都不做（这就是去抖）
        try:
            if need:
                self._log_sb.pack(side="right", fill="y")
            else:
                self._log_sb.pack_forget()
            self._log_sb_shown = need
        except Exception:
            pass

    def _log_font(self, d):
        self._log_font_size = max(LOG_SIZE_MIN, min(LOG_SIZE_MAX,
                                                    self._log_font_size + d))
        # 行距随字号等比缩放，否则大字号会挤成一坨
        self.log.configure(font=(FAMILY_MONO, self._log_font_size),
                           spacing1=2, spacing3=self._log_font_size // 3)
        self._log_sb_schedule()          # 字号变了 → 行高变了 → 溢出与否要重判

    def _clear_log(self):
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")
        self._log_sb_schedule()          # 清空后滚动条应自行消失
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def _status(self, key_or_text, cn=None):
        text = cn or STATUS_CN.get(key_or_text, key_or_text)
        self.lb_status.configure(text=text)

    def _set_step_label(self, i=None, n=None):
        n = n if n is not None else len(self.profile["steps"])
        self.lb_step.configure(text="第 %d/%d 步" % ((i or 0), n))
        # 进度条**平滑推进**（tkinter 的 Progressbar 只会跳变，见 `_anim_value`）。
        # ⚠ 目标值没变就**什么都不做** —— 否则每次刷新都重启一遍补间，它会一直"抖"。
        target = int(100.0 * (i or 0) / n) if n else 0
        if getattr(self, "_pb_target", None) != target:
            self._pb_target = target
            _anim_value(self.pb, target)

    # ------------------------------------------------------------------ #
    # 采帧前"清场"：把自家窗口全收起来
    #
    # ★ 口径（MEMORY 第 13 条补充）：**抓屏时一个自己的顶层窗口都不许露在外面**。
    #   判据不是"弹窗能不能用"（操作界面时弹窗是正常手段），而是"它现在会不会被拍进帧"。
    #   露一个 → 模板里就多一块自己的界面（噪点），重则那一步永远认不出来。
    # ★ 为什么改成**遍历**而不是逐窗硬编码：`withdraw()` 只藏调用它的那一个 Toplevel，
    #   子窗口不跟着藏。以前只写了「主窗 + 脚本仓库窗」两处，于是每加一个新窗口
    #   （设置栏 / 复核框…）都留一个洞 —— 这类"记得加一行"的约定迟早会漏。
    # ------------------------------------------------------------------ #
    def _hide_own_tops(self):
        """收起所有自家顶层窗口（取点遮罩除外），返回被收起的列表。"""
        hidden = []
        for w in self.winfo_children():
            if not isinstance(w, tk.Toplevel) or isinstance(w, PickOverlay):
                continue
            try:
                if not w.winfo_exists() or w.state() == "withdrawn":
                    continue
                w.withdraw()
                hidden.append(w)
            except Exception:                                   # noqa: BLE001
                pass
        self._hidden_tops.extend(hidden)
        return hidden

    def _restore_own_tops(self):
        """把上一次清场收起来的窗口还回来（不还用户会以为程序把窗口弄丢了）。"""
        for w in list(getattr(self, "_hidden_tops", [])):
            try:
                if w.winfo_exists():
                    w.deiconify()
            except Exception:                                   # noqa: BLE001
                pass
        self._hidden_tops = []

    def _restore_self(self):
        try:
            self.deiconify()
            self.lift()
            self.attributes("-topmost", True)
            self.after(300, lambda: self.attributes("-topmost", False))
            self.focus_force()
        except Exception:
            pass
        self._restore_own_tops()
        # ⚠ **采集期间被一起藏起来的窗口也要还回来**（脚本仓库就是这么个窗口）：
        #   `withdraw()` 只藏**调用它的那个** Toplevel，子窗口不会跟着藏 ——
        #   所以仓库窗口得自己 withdraw。相应地，收回主窗时必须把它一起叫回来，
        #   否则用户采完一步会发现"仓库窗口不见了"，还以为程序崩了。
        ctx = getattr(self, "_cap_ctx", None)
        if ctx and ctx.get("on_restore"):
            try:
                ctx["on_restore"]()
            except Exception:
                pass

    def _selected_index(self):
        """当前选中的 steps 下标（严格读 Listbox，不回退）。"""
        sel = self.lst.curselection()
        if not sel:
            return None
        row = sel[0]
        if 0 <= row < len(self._step_view):
            return self._step_view[row]
        return None

    def _ct_target_index(self):
        """**类型条该作用于哪一步** —— 比 `_selected_index()` 宽松一档。

        ⚠ 为什么类型条不能直接用 `_selected_index()`（这就是用户报的那个 bug）：
          `curselection()` 会因为焦点转移/点了空白/别处抢 selection 而变空，
          而**列表高亮看起来还在**。此时：
            · `_sync_click_type_box(None)` 会把下拉置灰（用户以为改不了）；
            · 用户真去改了下拉，`_on_click_type_pick` 拿到 None **静默 return**，
              改动压根没落盘 —— 用户却以为改成了。
          所以这里回退到 `_last_step_i`（最后一次**有效**选中）：
          谁也没选中、但用户刚点过第 N 步 → 就按第 N 步来。
          列表真的空/那步已被删，才返回 None。
        """
        i = self._selected_index()
        if i is not None:
            self._last_step_i = i
            return i
        last = getattr(self, "_last_step_i", None)
        if last is not None and 0 <= last < len(self.profile.get("steps", [])):
            return last
        return None

    def _row_of_step(self, step_index):
        """**steps 下标 → 列表行号**（`_selected_index` 的逆运算）。

        为什么必须有：列表的行序是可变的（上移/下移/删除会重排），
        想"重新选中某一步"时**不能拿 steps 下标直接当行号用** ——
        两者一旦错位，选中的就是别的步骤，用户看到的是"我的修改没生效"。
        找不到返回 None（那一步可能已被删掉），调用方据此跳过选中。
        """
        try:
            return self._step_view.index(step_index)
        except ValueError:
            return None

    # ------------------------------------------------------------------ #
    # 步骤列表
    # ------------------------------------------------------------------ #
    def _refresh_steps(self):
        """重建步骤列表，并**恢复刷新前的选中**。

        ⚠ 为什么必须自己恢复选中（这是"改完没回显"的第三个坑）：
          `delete(0,"end")` 会把 `curselection()` 清空，重建出来的列表
          **没有任何行被选中** → 紧接着的 `_show_detail()` 走"未选中"分支，
          把详情框写成"← 在左边选一步"。而 `selection_set()` **不触发**
          `<<ListboxSelect>>`，不会自动补一次渲染。
          调用方（`_after_struct_change` / `_save_step`）虽会自己 `selection_set`，
          但**兜底定时器 `after(50, self._refresh_steps)` 不会** ——
          用户要是正好在那 50ms 内改完类型，选中与详情就被这一次刷新抹掉了。
          与其靠每个调用方自觉，不如**在这一层收口**：谁刷新，谁负责把选中还回去。
        """
        prev = self._selected_index()          # 刷新前选中的是哪个 steps 下标
        self.lst.delete(0, "end")
        self._step_view = []
        for i, st in enumerate(self.profile.get("steps", [])):
            miss = storage.step_missing(st)
            mark = "✓" if not miss else "缺" + "".join(
                {"识别点": "点", "识别框": "框", "模板": "模", "操作点": "操"}.get(m, "?")
                for m in miss)
            self.lst.insert("end", "%2d. %-14s %s" % (i + 1, st.get("name", ""), mark))
            self._step_view.append(i)
        # 把选中还回去（那一步若已被删就放弃，交给 `_after_struct_change` 兜）
        if prev is not None:
            row = self._row_of_step(prev)
            if row is not None:
                self.lst.selection_set(row)
                self.lst.see(row)
        self._apply_mode_gate()
        self._show_detail()

    def _new_step_dict(self, idx: int) -> dict:
        """造一个空步骤。名字先给占位，随后统一由 renumber_steps 重排。"""
        sid = storage.next_step_id(self.profile)
        return {
            "id": sid, "name": "第 %d 步" % idx, "frame": "%s.png" % sid,
            "anchor": {}, "action": {"point": None, "radius": 6,
                                     "same_as_anchor": False, "click": True},
        }

    def _after_struct_change(self, select_row=None, note=None):
        """增删/移位/插入后**统一收尾**：重排编号 → 落盘 → 刷列表 → 定位选中。

        收口成一个函数的原因：这几步的顺序有讲究（先重排再落盘，否则存的是旧名；
        刷新后必须重新 selection_set，因为 Listbox 已被清空重建）。
        """
        storage.renumber_steps(self.profile)
        storage.save_profile(self.profile)
        self._refresh_steps()
        n = len(self._step_view)
        if n:
            row = n - 1 if select_row is None else max(0, min(n - 1, select_row))
            self.lst.selection_clear(0, "end")
            self.lst.selection_set(row)
            self.lst.see(row)
        self._show_detail()
        if note:
            self._log(note[0], note[1])

    def _add_step(self):
        """在末尾追加一步。"""
        self.profile["steps"].append(
            self._new_step_dict(len(self.profile["steps"]) + 1))
        self._after_struct_change(
            note=("已在末尾新增一步。点【采集本步画面】开始标它。", "ok"))

    def _insert_step(self):
        """在**选中步骤之后**插入一步（没选中就插到末尾）。

        和"新增"的区别只在落点：插入让用户能把漏掉的一步补进链条中间，
        而不必先加到最后再一路【上移】挪过去。
        """
        i = self._selected_index()
        at = len(self.profile["steps"]) if i is None else i + 1
        self.profile["steps"].insert(at, self._new_step_dict(at + 1))
        self._after_struct_change(
            select_row=at,
            note=("已在第 %d 步之后插入一步（编号已自动重排）。" % (at if at else 0)
                  if i is not None else "已插入一步（编号已自动重排）。", "ok"))

    def _del_step(self):
        i = self._selected_index()
        if i is None:
            messagebox.showinfo("先选一步", "在左边列表里选中要删的那一步")
            return
        st = self.profile["steps"][i]
        if not messagebox.askyesno("确认", "删掉第 %d 步「%s」？" % (i + 1, st.get("name", ""))):
            return
        self.profile["steps"].pop(i)
        # 删完优先选它原来的位置；删的是最后一条则选新的最后一条
        n = len(self.profile["steps"])
        self._after_struct_change(
            select_row=(i if i < n else n - 1) if n else None,
            note=("已删除第 %d 步（编号已自动重排）" % (i + 1), "warn"))

    def _move(self, d):
        i = self._selected_index()
        if i is None:
            return
        j = i + d
        steps = self.profile["steps"]
        if not (0 <= j < len(steps)):
            return
        steps[i], steps[j] = steps[j], steps[i]        # 顺序变了，编号/数据不动
        self._after_struct_change(
            select_row=j,
            note=("已把第 %d 步挪到第 %d 位（编号已自动重排）" % (i + 1, j + 1), "info"))

    def _dup_prev(self):
        i = self._selected_index()
        if i is None:
            i = len(self.profile["steps"]) - 1
        if i < 0:
            return
        import copy
        src = self.profile["steps"][i]
        sid = storage.next_step_id(self.profile)
        new = copy.deepcopy(src)
        new["id"] = sid
        new["name"] = "%s 的副本" % src.get("name", "")
        new["frame"] = "%s.png" % sid
        if new.get("anchor"):
            new["anchor"]["template"] = "%s.png" % sid
        self.profile["steps"].insert(i + 1, new)        # 数据复制，模板稍后重标
        self._after_struct_change(
            select_row=i + 1,
            note=("复制出 %s（模板还是旧的，建议点【采集本步画面】重标一遍）" % sid, "warn"))

    def _sync_click_type_box(self, i):
        """让类型条反映"当前选中步骤"的类型；没选中就说清原因，别偷偷改成单击。"""
        if i is None:
            # ⚠ 老代码在这里 `set("单击")` —— **这是把用户带偏的关键一笔**：
            #   用户"选了双击、回来看到单击"，真相是他的选中丢了、这里把显示
            #   重置成了默认的「单击」。显示默认值 ≠ 数据是单击，两回事。
            #   正确做法：**保留原值不动**，把控件置灰并说明原因。
            self.cmb_click_type.configure(state="disabled")
            self.lb_ct_note.configure(text="先在左边点一下要改的那一步")
            return
        act = (self.profile["steps"][i].get("action") or {})
        ct = picker.norm_click_type(act.get("click_type"))
        self.click_type_var.set(self._CT_LABEL.get(ct, "单击"))
        # 执行模式下不改配置（两模互斥），所以只在编辑模式放开
        self.cmb_click_type.configure(
            state="readonly" if self.mode.get() == "edit" else "disabled")
        self.lb_ct_note.configure(
            text=("第 %d 步" % (i + 1)) if self.mode.get() == "edit"
            else "执行模式下不可改")

    def _show_detail(self):
        self.detail.configure(state="normal")
        self.detail.delete("1.0", "end")
        i = self._selected_index()
        # ⚠ 类型条用宽松版目标（`_ct_target_index`），详情正文用严格版 ——
        #   两者有意不同：详情要如实反映"你选中了没有"，而类型条要尽量保留
        #   "用户当前在改哪一步"，不能因为一次失焦就把他的操作对象丢掉。
        self._sync_click_type_box(self._ct_target_index())
        if i is None:
            self.detail.insert("end", "← 在左边选一步，或点【新增一步】开始。\n")
            self.detail.configure(state="disabled")
            return
        st = self.profile["steps"][i]
        a = st.get("anchor") or {}
        act = st.get("action") or {}
        lines = ["第 %d 步　%s　（id=%s）" % (i + 1, st.get("name", ""), st.get("id"))]
        if a.get("point"):
            lines.append("识别点  相对 x=%.4f  y=%.4f" % tuple(a["point"]))
            box = a.get("box") or [0, 0, 0, 0]
            lines.append("识别框  相对 w=%.4f  h=%.4f（程序自动裁的）" % (box[2], box[3]))
            lines.append("匹配方式  %s　阈值 %.2f" % (
                a.get("method", "template"), self.profile["match"]["threshold"]))
        else:
            lines.append("识别点  还没标")
        if act.get("same_as_anchor"):
            lines.append("操作点  同识别点")
        elif act.get("point"):
            lines.append("操作点  相对 x=%.4f  y=%.4f　落点半径 %d px"
                         % (act["point"][0], act["point"][1], act.get("radius", 6)))
        else:
            lines.append("操作点  还没标")
        ct = picker.norm_click_type(act.get("click_type"))
        lines.append("操作类型  %s%s"
                     % (self._CT_LABEL.get(ct, "单击"),
                        "（旧数据默认，与从前行为一致）"
                        if act.get("click_type") is None else ""))
        miss = storage.step_missing(st)
        lines.append("状态  " + ("✅ 数据齐了" if not miss else "⚠ 还缺：" + "、".join(miss)))
        ref = self.profile.get("reference", {})
        lines.append("参考分辨率  %dx%d（程序从采集帧量的）" % (ref.get("w", 0), ref.get("h", 0)))
        self.detail.insert("end", "\n".join(lines) + "\n")
        self.detail.configure(state="disabled")

    # ------------------------------------------------------------------ #
    # 模式
    # ------------------------------------------------------------------ #
    def _apply_mode_gate(self):
        ready, why = storage.profile_ready(self.profile)
        # 目标没选也算不齐（**全屏模式除外** —— 那一档不需要认窗口）
        if not storage.is_screen_mode(self.profile) and \
                not (self.profile.get("window", {}).get("title_keywords") or []):
            ready, why = False, "还没选目标窗口（或改用「整个屏幕」）"
        if self.mode.get() == "run" and not ready:
            self.mode.set("edit")
            self._log("执行模式已切回编辑模式：%s" % why, "warn")
        self.rb_run.configure(state="normal" if ready else "disabled")
        for w in (self.btn_start, self.btn_probe):
            _btn_set_enabled(w, bool(ready and self.mode.get() == "run"))
        # 【停止】只在真的跑起来时才可点。这里必须也管一次 ——
        # `_on_running_change` 只在"运行状态变化"时被调用，**启动那一刻不会**，
        # 少了这一行它一开机就是亮的（点它毫无反馈，用户以为"停不掉"）。
        _btn_set_enabled(self.btn_stop, bool(self._running))
        for w in (self.btn_capture, self.btn_add, self.btn_ins, self.btn_del,
                  self.btn_up, self.btn_down, self.btn_copy, self.btn_window):
            _btn_set_enabled(w, self.mode.get() == "edit")
        # ⚠ 【脚本仓库】**两个模式下都必须可用** —— 它不参与执行流程，
        #   是纯粹的资产存取。跟着模式门控会让用户在跑流程时连脚本都翻不了，
        #   那是把"模式互斥"这条规矩用错了地方。
        #   （模式互斥的本意是"编辑时不许跑、跑时不许改步骤"，与仓库无关。）
        self.btn_repo.configure(state="normal")
        # 【存入仓库】同理，两个模式下都可用：它**只读**工作台的步骤、只写仓库，
        # 既不改台面、也不参与执行 —— 拿模式门控把它锁住，只会让用户在跑流程时
        # 眼睁睁看着刚标好的一步存不进去。
        self.btn_to_repo.configure(state="normal")
        if ready:
            self._set_mode_text("", ACCENT)
        else:
            self._set_mode_text("（执行模式未解锁：%s）" % why, WARNC)
        self._sync_mode_nav()
        self._show_detail()

    def _on_mode_change(self):
        if self.mode.get() == "run":
            ready, why = storage.profile_ready(self.profile)
            if not ready:
                self.mode.set("edit")
                self._log("还不能执行：%s" % why, "warn")
            else:
                self._status("idle", "待命（执行模式）")
                self._log("切到执行模式。默认干跑，勾「真实点击」才会真的动鼠标。", "info")
        else:
            self._status("edit", "编辑中")
        self._apply_mode_gate()

    # ------------------------------------------------------------------ #
    # 脚本仓库（独立模块，见 `ScriptRepoWindow`）
    # ------------------------------------------------------------------ #
    def _open_repo(self):
        """打开脚本仓库窗口。

        ★ **只开一个**（单例）：用户连点两次【脚本仓库】不该弹出两个窗口 ——
          两个窗口各持一份 `repo` 内存副本，谁后写盘谁覆盖，用户会看到
          "我在这边删了、那边还在"的鬼现象。
          做法：记住当前窗口引用，已存在就 `lift()` 抬到前面，而不是新建。
        """
        w = getattr(self, "_repo_win", None)
        if w is not None:
            try:
                if w.winfo_exists():
                    w.deiconify()
                    w.lift()
                    w.focus_set()
                    return
            except Exception:
                pass                          # 窗口已被销毁 → 照常新建
        self._repo_win = ScriptRepoWindow(self)
        self._log("已打开脚本仓库（数据文件 config/scripts.json；"
                  "与主窗步骤配置分开存放，互不影响）", "info")

    # ------------------------------------------------------------------ #
    # 工作台 → 仓库（P7 补的回程路）
    # ------------------------------------------------------------------ #
    def _save_bench_to_repo(self):
        """把**工作台当前标好的步骤**收成一条脚本，存进脚本仓库。

        ## 为什么要有这条路（P7）
        此前只有单程：【载入到工作台】能把仓库取出来跑，却没有任何入口把工作台
        标好的东西**存回去**。用户在台面上标完一步，想让 `scripts.json` 里先有
        这条资产，只能去仓库窗【＋ 新建脚本】**在那边重标一遍** —— 台上那份白干。

        ## 与【载入到工作台】严格对称
            载入：仓库脚本 → `profile["steps"]`（**替换**台面，并带上 reference）
            存入：`profile["steps"]` → 仓库**新增一条**（并把 reference 一起带上）
        两边都**只读对方、只写自己**：本方法全程**不写 `profile.json`**
        （`verify_script_repo.py` 会拿"跑完按字节还原"加哈希兜底来验这一点）。

        ## 三条守则
        1. **一步都没有 → 拦住**，说清"先去标一步"。存一条空脚本，用户会以为
           存成功了，其实里面什么都没有。
        2. **没标齐的步骤不收**（`storage.step_missing`）—— 与执行模式**同一套口径**：
           连识别点/操作点都没有的步骤，收进仓库只会让那里多一条废脚本。
           但**绝不静默丢**：先问一句、把缺什么逐个点名，用户点了"是"才收标齐的那些。
        3. **id 重编 + 素材复制**交给 `scripts_repo.import_steps` —— 沿用台面的
           `s01` 会让这条脚本与台面草稿共用同一张帧/模板（改一个动两个）。

        ## 仓库窗开着时的小心
        `ScriptRepoWindow` 打开时持一份 `repo` 内存副本，之后每次改动都
        `save_repo(self.repo)`。若这里另读一份、加完落盘，**它那份就成了旧的** ——
        它下一次提交会把这条新增整条抹掉（用户看到"存进去了，过一会儿又没了"）。
        所以走 `_repo_for_write()`：窗开着就写在**它那份**上，写完让它当场刷新。
        """
        steps = list(self.profile.get("steps") or [])
        if not steps:
            messagebox.showinfo(
                "还没有步骤",
                "工作台里一步都还没有。\n\n"
                "先在左边点【新增一步】→ 点【采集本步画面】标一步（点两下就成），"
                "再回来把它存进仓库。", parent=self)
            return
        unfit = storage.unfit_steps(self.profile)
        bad = {i for i, _m in unfit}
        keep = [st for i, st in enumerate(steps, 1) if i not in bad]
        if not keep:
            detail = "；".join("第%d步缺%s" % (i, "、".join(m)) for i, m in unfit)
            messagebox.showinfo(
                "一步都没标齐",
                "仓库里的每条脚本都得是「能认出来的步骤」（识别点 + 操作点），"
                "而现在：\n\n  %s\n\n先去把它们标齐，再回来存。" % detail, parent=self)
            return
        if unfit:
            detail = "；".join("第%d步缺%s" % (i, "、".join(m)) for i, m in unfit)
            if not messagebox.askyesno(
                    "有步骤还没标齐",
                    "这 %d 步还没标齐，「不会被收进仓库」：\n\n  %s\n\n"
                    "只把标齐的那 %d 步存成一条脚本？"
                    % (len(unfit), detail, len(keep)), parent=self):
                return
        self._ask_name_to_save(keep)

    def _ask_name_to_save(self, steps):
        """问名字 → 收编 → 落盘 → 回显。**只写仓库，不碰工作台**。"""
        def _on_ok(name, desc):
            # 脚本自带的参数：从工作台**拷一份** —— 与【载入到工作台】对称。
            # ⚠ 不带 `reference`，换台机器载入时就会按错分辨率裁框（框会系统性偏大/偏小）。
            cfg = {
                "reference": copy.deepcopy(
                    self.profile.get("reference") or {"w": 0, "h": 0}),
                "match": copy.deepcopy(self.profile.get("match") or {}),
                "execution": {"click_radius": int(
                    (self.profile.get("execution") or {}).get("click_radius", 6))},
            }
            repo, win = self._repo_for_write()
            item = scripts_repo.import_steps(repo, name, desc, steps, cfg=cfg)
            ok = scripts_repo.save_repo(repo)
            if win is not None:
                # 仓库窗开着：让它当场把这条显示出来（不然用户得关窗重开才看得见）
                win._refresh()
            self._log("已存入脚本仓库：「%s」（%s，%d 步，帧与模板已复制一份）"
                      % (item["name"], item["id"], len(scripts_repo.steps_of(item))),
                      "ok" if ok else "err")
            if ok:
                self._log("　点右上角【脚本仓库】就能看到它；工作台这边原样未动。",
                          "muted")
            else:
                messagebox.showerror(
                    "保存失败",
                    "写 config/scripts.json 失败 —— 这条脚本现在只在内存里。"
                    "请检查 config 目录是否可写。", parent=self)

        ScriptNameDialog(self, _on_ok, mode="save")

    def _repo_for_write(self):
        """拿一个**能安全写入**的仓库，外加开着的仓库窗（没开就是 `None`）。

        ★ 仓库窗开着就必须写在**它那份 `repo`** 上（理由见 `_save_bench_to_repo`
          末节）：两个窗口各持一份内存副本、谁后写盘谁覆盖，是"我在这边存了、
          过一会儿又没了"的根因。写在它那份上，再让它 `_refresh()`，
          两个窗口看到的就是同一份数据。
        """
        w = getattr(self, "_repo_win", None)
        if w is not None:
            try:
                if w.winfo_exists():
                    w._flush_if_dirty(silent=True)   # 先把它自己未存的改动带上
                    return w.repo, w
            except Exception:
                pass                                 # 窗口已销毁 → 照常自己读盘
        return scripts_repo.load_repo(), None

    # ------------------------------------------------------------------ #
    # 选窗口
    # ------------------------------------------------------------------ #
    def _choose_window(self):
        def _picked(hwnd, title):
            win = self.profile["window"]
            if hwnd == SCREEN_SENTINEL:
                win["target"] = "screen"
                win["title_keywords"] = []
                self.target_hwnd = None
                self._log("目标 = 「整个屏幕」：不认窗口，抓主屏、坐标就是屏幕坐标。", "ok")
                self._log("  适合全屏游戏 / 无边框程序 / 跨多个窗口的流程。"
                          "代价：桌面上别的窗口会被一起抓进去，跑之前把画面收拾干净。", "muted")
            else:
                win["target"] = "window"
                win["title_keywords"] = [title]
                win["match_mode"] = "exact"
                self.target_hwnd = hwnd
                self._log("目标窗口 = 「%s」（精确匹配）" % title, "ok")
            storage.save_profile(self.profile)
            self._sync_target_label()
            self._refresh_steps()
        winio.require_win32()
        WindowPicker(self, _picked)

    def _sync_target_label(self):
        """刷新"目标：xxx"。

        ⚠ 只写**完整文案**进 `_win_full`，再由 `_on_top_resize()` 按顶栏剩余空间裁 ——
          直接 `configure(text=长标题)` 的话，一句长窗口标题就能把状态文字
          和右侧按钮顶出可视区（P7 实测：880 宽下状态文字只剩 1px）。
        """
        if storage.is_screen_mode(self.profile):
            self._win_full = "目标：整个屏幕（不指定窗口）"
            self.lb_win.configure(fg=OKC)
        else:
            kws = self.profile.get("window", {}).get("title_keywords") or []
            self._win_full = ("目标：%s" % kws[0]) if kws else "未选择目标"
            self.lb_win.configure(fg=FG if kws else MUTED)
        self._on_top_resize()

    # ------------------------------------------------------------------ #
    # 采集画面（**画面一律由程序自己抓**）
    # ------------------------------------------------------------------ #
    def _resolve_target(self):
        """返回 `(kind, hwnd)`：`("screen", None)` / `("window", hwnd)` / `(None, None)`。"""
        winio.require_win32()
        if storage.is_screen_mode(self.profile):
            return ("screen", None)
        if self.target_hwnd and winio.window_title(self.target_hwnd):
            return ("window", self.target_hwnd)
        w = self.profile.get("window", {})
        hwnd = winio.find_window(w.get("title_keywords", []),
                                 w.get("match_mode", "exact"),
                                 w.get("exclude", []))
        self.target_hwnd = hwnd
        return (("window", hwnd) if hwnd else (None, None))

    # ------------------------------------------------------------------ #
    # 采集上下文：**同一条采集链路，两种写入目标**
    # ------------------------------------------------------------------ #
    # 主窗写自己的 `profile["steps"]`；脚本仓库写**某条脚本的 steps**。
    # 两边共用 `_do_capture → _open_overlay → _on_picked → _review → _save_step`，
    # 差别只在"往哪儿写"。所以把这一个差别抽成上下文，而不是复制一条链路 ——
    # 复制的后果必然是"两边行为慢慢走偏"（模板裁法、复核口径、保存顺序都会分叉）。
    #
    # ⚠ 主窗路径**必须零变化**：`self._cap_ctx` 为 None 时，下面每个函数取到的仍是
    #   `self.profile`，日志文案也逐字不变（见 `_cap_step_label`）。
    #
    # ⚠⚠ `_cap_ctx` 是**属性**，不是方法 —— 一律用 `getattr(self, "_cap_ctx", None)` 取。
    #    第一版把它写成了 `def _cap_ctx(self)`，结果 `self._cap_ctx = {...}` 一把
    #    **方法覆盖成了字典**：属性与方法同名，赋值即顶掉，随后 `self._cap_ctx()`
    #    直接炸（"'dict' object is not callable"）。上下文这种"可有可无"的状态，
    #    统一走"属性 + getattr 兜底"，别再开一个同名方法。
    def _cap_steps(self):
        """这次采集要写入的 steps 列表（**同一个 list 对象**，改它即改数据源）。"""
        ctx = getattr(self, "_cap_ctx", None)
        return ctx["steps"] if ctx else self.profile["steps"]

    def _cap_cfg(self):
        """这次采集/复核用的参数（参考分辨率 / 匹配阈值 / 落点半径）。"""
        ctx = getattr(self, "_cap_ctx", None)
        return ctx["cfg"] if ctx else self.profile

    def _cap_step_label(self, i):
        """第 N 步的称呼 —— 日志与遮罩标题都用它。

        ★ 主窗返回的就是 `第 N 步`，拼进日志后与改造前**逐字相同**
          （例如 "已采集" + "第 1 步" + "的画面"）—— 现有断言不受影响。
        """
        ctx = getattr(self, "_cap_ctx", None)
        n = len(self._cap_steps())
        if ctx:
            return "脚本「%s」第 %d 步" % (ctx["name"], i + 1)
        return "第 %d 步" % (i + 1)

    def begin_script_capture(self, script, index, on_saved, win=None):
        """脚本仓库的【采集本步画面】：**走原本那条采集链路**，只是写进脚本自己的 steps。

        返回是否真的开动了（没开动时已经把原因告诉用户了）。

        `on_saved` —— `fn(step_index)`：这一步保存完之后回调（由仓库窗口负责落盘）
        `win`      —— 调用方自己的窗口（仓库窗口）。它会被一起藏起来并在采完叫回来。

        ⚠ **顺序有讲究**：所有校验（含弹窗提示）必须排在"藏窗口"**之前**。
          `messagebox` 是发起窗口的子窗口 —— 父窗口若是 withdrawn 状态，
          提示框会跟着看不见，用户只看到"点了没反应"。
        """
        if self._running or self._exec_lock.locked():
            self._log("正在执行流程，先【停止】再采集画面", "warn")
            return False
        if self.overlay is not None:
            self._log("遮罩还开着呢，先把它关掉再采集", "warn")
            return False
        # ⚠ 目标窗口沿用**工作台当前选中的那个** —— 与主窗【采集本步画面】
        #   完全同一口径，用户不必在仓库里再选一次窗口。
        kind, hwnd = self._resolve_target()
        if kind is None:
            messagebox.showwarning(
                "找不到目标窗口",
                "先在主窗右上角【选择目标窗口】选一个（也可以直接选「整个屏幕」）—— "
                "脚本仓库沿用工作台当前选中的目标。")
            return False
        self._cap_ctx = {
            "steps": scripts_repo.steps_of(script),
            "cfg": scripts_repo.cfg_of(script),
            "name": script.get("name") or "脚本",
            "on_saved": on_saved,
            "on_restore": (self._restore_win(win) if win is not None else None),
            "win": win,                              # 重选点时要再藏一次
        }
        self._status("capture", "采集画面")
        # ⚠ **清场**：自家窗口一个都不许留在屏幕上（含仓库窗 / 设置栏 / 复核框）——
        #   见 `_hide_own_tops`。以前这里只藏 `win` 一个，新窗口一加就是个洞。
        self._hide_own_tops()
        if win is not None:
            try:
                win.withdraw()
            except Exception:
                pass
        self.withdraw()
        self.after(140, lambda: self._do_capture(index, kind, hwnd))
        return True

    @staticmethod
    def _restore_win(win):
        def _go():
            try:
                win.deiconify()
                win.lift()
            except Exception:
                pass
        return _go

    def _capture_for_step(self):
        # **采集与执行互斥**：正在跑流程时不许采集（否则会抢画面/抢前台）
        if self._running or self._exec_lock.locked():
            self._log("正在执行流程，先【停止】再采集画面", "warn")
            return
        if self.overlay is not None:
            self._log("遮罩还开着呢，先把它关掉再采集", "warn")
            return
        i = self._selected_index()
        if i is None:
            messagebox.showinfo("先选一步", "在左边列表里选中要标的那一步（或点【新增一步】）")
            return
        kind, hwnd = self._resolve_target()
        if kind is None:
            messagebox.showwarning("找不到目标窗口",
                                   "先点右上角【选择目标窗口】选一个 —— "
                                   "也可以直接选「整个屏幕」，那样就不用认窗口了。")
            return
        if kind == "screen":
            self._log("准备采集第 %d 步的画面 —— 全屏模式：先把自己收起来，直接抓主屏"
                      % (i + 1), "info")
        else:
            self._log("准备采集第 %d 步的画面 —— 先把自己收起来，再把目标窗口拉回前台"
                      % (i + 1), "info")
        self._start_capture(i, kind, hwnd)

    def _start_capture(self, i, kind, hwnd):
        """收起自己 → 抓帧。主窗与脚本仓库**共用**这一段。

        ⚠ 顺序不能错：**先隐藏主窗**，否则截到的是自己。
          脚本仓库那条路是"仓库窗先藏 → `self.withdraw()`"，见 `begin_script_capture`。
        """
        self._status("capture", "采集画面")
        self._hide_own_tops()
        self.withdraw()
        self.after(140, lambda: self._do_capture(i, kind, hwnd))

    def _do_capture(self, i, kind, hwnd):
        err = None
        frame = None
        client_rect = None
        if kind == "screen":
            # 全屏模式：没有窗口要激活 —— 自己已经藏好了，直接抓
            time.sleep(0.18)
            frame = winio.capture_primary_screen()
            ok, why = winio.frame_is_usable(frame)
            if ok:
                # 遮罩的贴图位置与尺寸**以帧为准**（别拿 GetSystemMetrics 推，DPI 不一致会错位）
                client_rect = (0, 0, frame.shape[1], frame.shape[0])
            else:
                err, frame = why, None
        else:
            if not winio.activate(hwnd):
                err = ("切不到目标窗口前台 —— 请手动点一下目标程序，再回来重试\n"
                       "（也可以把目标程序切到那一步的画面后，按 F8 兜底抓帧）")
            else:
                time.sleep(0.18)                       # 等目标窗把画面画稳
                frame = winio.capture_client(hwnd)
                ok, why = winio.frame_is_usable(frame)
                if ok:
                    client_rect = winio.client_rect_screen(hwnd)
                else:
                    err, frame = why, None
        if frame is None:
            self._restore_self()
            self._log("采集失败：%s" % err, "err")
            self._status("edit", "编辑中")
            messagebox.showwarning("采集不到画面", err or "未知原因")
            return
        self._capture_frame = frame
        self._capture_client_rect = client_rect
        st = self._cap_steps()[i]
        storage.save_step_frame(st["id"], frame)   # 帧落盘：裁模板、做互斥都比对它
        self._log("已采集%s的画面 %dx%d（%s），并落盘 data/frames/%s.png"
                  % (self._cap_step_label(i), frame.shape[1], frame.shape[0],
                     "整个屏幕" if kind == "screen" else "窗口客户区", st["id"]), "ok")
        self._open_overlay(i, frame)

    def _cross_check_point(self, fx, fy):
        """**三源旁证**：用户在帧上点了一个点，顺手核一遍「帧内坐标 → 屏幕坐标」这条路。

        为什么在此处做（而不是采集时）：三条源里最真的是 `cursor` ——
        **用户刚亲手点过，系统光标此刻就停在那**。所以拿"用户点的那一点"做旁证才有意义，
        帧中心做代表点是错的（光标不一定在中心）。

        只告警、不阻断 —— 偏几个像素肉眼看不出来，但点十次就有一次打空。
        全屏模式下帧原点即屏幕原点，天然一致；认窗口模式才真能查出客户区偏移问题。
        """
        try:
            ref = self._cap_cfg().get("reference", {})
            fr = self._capture_frame
            if fr is None:
                return
            fh, fw = fr.shape[0], fr.shape[1]
            try:
                cur = winio.cursor_pos()
            except Exception:
                cur = None
            d = geometry.cross_check_point(
                fx, fy, client_rect=self._capture_client_rect,
                frame_wh=(fw, fh), ref_wh=(ref.get("w", 0), ref.get("h", 0)),
                cursor_xy=cur)
            self._log("旁证：%s" % d["detail"],
                      "muted" if d["consistent"] else "warn")
        except Exception as exc:                      # noqa: BLE001
            self._log("旁证跳过（%s）" % exc, "muted")

    def _open_overlay(self, i, frame):
        label = "%s（共 %d 步）" % (self._cap_step_label(i), len(self._cap_steps()))
        self.overlay = PickOverlay(
            self, frame, self._capture_client_rect, label,
            self.same_pt_var.get(),
            on_done=lambda rec, act: self._on_picked(i, rec, act),
            on_cancel=self._on_pick_cancel,
        )

    def _on_pick_cancel(self):
        self._log("已取消取点（这一步的数据没动）", "warn")
        self._cap_ctx = None                       # 见 `_cancel_review`：清干净，别串台
        self._status("edit", "编辑中")

    def _on_picked(self, i, rec_pt, act_pt):
        """选完当场裁模板 + 当场匹配 + 与别的步骤比一遍（边标边验）。"""
        frame = self._capture_frame
        st = self._cap_steps()[i]
        if frame is None or rec_pt is None:
            self._log("取点数据不全，已放弃", "warn")
            return
        # 三源旁证：此刻系统光标就停在用户刚点的那一点上，是核坐标链路的最好时机
        self._cross_check_point(rec_pt[0], rec_pt[1])
        # **操作类型**：新标时默认单击；已有值就沿用它（这样"重选操作点"不会把
        # 用户先前选好的双击弄丢 —— 重选的是**点**，不是**点法**）。
        old_act = st.get("action") or {}
        self.pending = {"i": i, "rec_pt": rec_pt, "act_pt": act_pt,
                        "box": (storage.DEFAULT_BOX_W, storage.DEFAULT_BOX_H),
                        "click_type": picker.norm_click_type(old_act.get("click_type"))}
        self._review()

    def _review(self):
        p = self.pending
        frame = self._capture_frame
        cfg = self._cap_cfg()                  # 主窗 = profile；仓库 = 脚本自带那份
        st = self._cap_steps()[p["i"]]
        cw, ch = frame.shape[1], frame.shape[0]
        bw, bh = p["box"]
        # 参考分辨率 = 第一张采集帧的尺寸（程序自己量，不问用户）
        ref = cfg.setdefault("reference", {"w": 0, "h": 0})
        if not ref.get("w") or not ref.get("h"):
            ref["w"], ref["h"] = cw, ch
        # 框尺寸按参考分辨率给，实机上等比换算（**框由程序定，用户不拖框**）
        kw = cw / float(ref["w"]) if ref.get("w") else 1.0
        kh = ch / float(ref["h"]) if ref.get("h") else 1.0
        box_px = (max(storage.MIN_BOX, int(round(p["box"][0] * kw))),
                  max(storage.MIN_BOX, int(round(p["box"][1] * kh))))
        box = picker.build_box(frame, p["rec_pt"], box_px)
        if box[2] < storage.MIN_BOX or box[3] < storage.MIN_BOX:
            self._log("识别点太贴边，裁出来的框太小了 —— 往里挪一点重点一下", "warn")
            messagebox.showwarning("点太偏", "识别点离画面边缘太近，裁不出可用的模板。\n"
                                             "请选一个画面内部、独特且静止的元素。")
            self._open_overlay(p["i"], frame)
            return
        tmpl = picker.cut_template(frame, box)
        if tmpl.size == 0:
            self._log("裁出来的模板是空的，重选一次", "warn")
            self._open_overlay(p["i"], frame)
            return

        # ③ 当场拿这块模板匹配这一帧（边标边验，最值钱的一步）
        thr = float(cfg["match"]["threshold"])
        res = picker.spot_check(frame, tmpl, p["rec_pt"], box, cfg)
        # 再拿它去别的步骤的帧上跑一遍（负样本分，越低越好）
        neg = picker.negative_score(cfg, tmpl, st["id"])
        p["box_px"] = box_px

        payload = {
            "frame": frame, "frame_w": cw, "frame_h": ch,
            "box_abs": box, "template": tmpl,
            "rec_pt_abs": p["rec_pt"], "act_pt_abs": p["act_pt"] or p["rec_pt"],
            "rec_rel": geometry.point_abs_to_rel(p["rec_pt"], cw, ch),
            "act_rel": geometry.point_abs_to_rel(p["act_pt"], cw, ch) if p["act_pt"] else None,
            "score": res.score, "threshold": thr, "neg_score": neg,
            "same_as_anchor": self.same_pt_var.get(),
            "click_type": p.get("click_type", "single"),
        }
        p["payload"] = payload
        p["template"] = tmpl
        p["box_abs"] = box

        self._log("复核%s：模板 %dx%d，当场得分 %.4f（%s）"
                  % (self._cap_step_label(p["i"]), box[2], box[3], res.score,
                     "命中" if res.ok else "未达阈值"), "ok" if res.ok else "warn")
        if neg is not None and neg >= thr - 0.1:
            self._log("⚠ 这块模板跟别的步骤最高相似 %.4f，离阈值太近了 —— "
                      "换个更独特的元素重选，别靠调阈值硬撑" % neg, "warn")

        ReviewDialog(self, payload,
                     on_save=self._save_step,
                     on_resize=self._resize_box,
                     on_repick_rec=lambda: self._repick("rec"),
                     on_repick_act=lambda: self._repick("act"),
                     on_cancel=self._cancel_review,
                     on_ct_change=self._set_pending_click_type)

    _CT_LABEL = {"single": "单击", "double": "双击"}
    _CT_VALUE = {"单击": "single", "双击": "double"}

    def _on_click_type_pick(self, _event=None):
        """类型条的下拉：改"当前在改的那一步"的操作类型，改完立即落盘。"""
        i = self._ct_target_index()          # ⚠ 用宽松版，别用 _selected_index
        if i is None:
            # ⚠ 绝不静默 return —— 用户改了却什么都没发生，会以为改成了
            self._log("还没选中要改哪一步：先在左边列表点一下那一步，再改这里", "warn")
            self._sync_click_type_box(None)
            return
        ctype = self._CT_VALUE.get(self.click_type_var.get(), "single")
        st = self.profile["steps"][i]
        act = st.setdefault("action", {})
        if picker.norm_click_type(act.get("click_type")) == ctype:
            return
        act["click_type"] = ctype
        storage.save_profile(self.profile)          # 改完即存，别只活在内存里
        self._log("第 %d 步的操作类型改为「%s」" % (i + 1, self._CT_LABEL.get(ctype, ctype)), "ok")
        self._show_detail()

    def _set_pending_click_type(self, value):
        """复核弹窗里改了「单击/双击」→ 只记进 pending，保存时才落盘。"""
        if not self.pending:
            return
        ct = picker.norm_click_type(value)
        self.pending["click_type"] = ct
        self.pending.setdefault("payload", {})["click_type"] = ct
        self._log("这一步的操作类型改为「%s」（点【保存这一步】后生效）"
                  % ("双击" if ct == picker.CLICK_DOUBLE else "单击"), "info")

    def _resize_box(self, factor):
        p = self.pending
        if not p:
            return
        bw, bh = p["box"]
        nw = max(storage.DEFAULT_BOX_W / 6.0, min(storage.DEFAULT_BOX_W * 4.0, bw * factor))
        nh = max(storage.DEFAULT_BOX_H / 6.0, min(storage.DEFAULT_BOX_H * 4.0, bh * factor))
        p["box"] = (nw, nh)
        dlg = None
        for w in self.winfo_children():
            if isinstance(w, ReviewDialog):
                dlg = w
        if dlg:
            dlg.close()
        self._review()

    def _repick(self, which):
        p = self.pending
        if not p:
            return
        for w in self.winfo_children():
            if isinstance(w, ReviewDialog):
                w.close()
        self._log("重选识别点：把画面重新采一次，避免用旧帧" if which == "rec"
                  else "重选操作点", "info")
        # 重选一律重新采集画面 —— 防止拿旧帧标出新数据
        ctx = getattr(self, "_cap_ctx", None)
        if ctx is not None:
            # 仓库那条路：重采必须回脚本自己的那一步，不能掉到主窗的采集上
            self.after(60, lambda: self._recapture_ctx(ctx, p["i"]))
            return
        self.after(60, lambda: self._capture_for_step())

    def _recapture_ctx(self, ctx, i):
        """仓库里"重选点"：**沿用原上下文**再采一次（目标窗口、写入容器都不变）。"""
        self._cap_ctx = ctx
        kind, hwnd = self._resolve_target()
        if kind is None:
            messagebox.showwarning("找不到目标窗口", "目标窗口不见了 —— 回主窗重新选一个。")
            self._cap_ctx = None
            return
        win = ctx.get("win")
        if win is not None:
            try:
                win.withdraw()
            except Exception:
                pass
        self._start_capture(i, kind, hwnd)

    def _cancel_review(self):
        # ⚠ **必须先关窗**：这个回调是复核框的"取消"。只清状态不关窗 =
        #   用户看到的就是"点了没反应"（本 bug 的病因，见 `ReviewDialog._on_cancel`）。
        self._close_reviews()
        self._log("已取消，这一步的数据没改", "warn")
        self.pending = None
        # ⚠ 采集上下文必须一并清掉：留着它会让**下一次主窗采集**写进脚本的 steps
        #   （"我明明在主窗标的，怎么跑到脚本里去了"）。
        self._cap_ctx = None
        self._status("edit", "编辑中")

    def _close_reviews(self):
        """关掉所有还开着的复核框（同一个动作在多处要用，收成一处）。"""
        for w in self.winfo_children():
            if isinstance(w, ReviewDialog):
                try:
                    w.close()
                except Exception:                               # noqa: BLE001
                    pass

    def _save_step(self):
        p = self.pending
        if not p:
            return
        ctx = getattr(self, "_cap_ctx", None)
        i, frame = p["i"], self._capture_frame
        st = self._cap_steps()[i]
        tmpl = p["template"]
        sid = st["id"]

        if not storage.save_step_template(sid, tmpl):
            self._log("模板写盘失败（检查 assets/templates 权限）", "err")
            return
        same = self.same_pt_var.get()
        ctype = picker.norm_click_type(p.get("click_type"))
        # 落盘：裁框/裁模板/写 anchor+action 全走 picker（与自测同一份实现）
        anchor, _action, _box, _t = picker.apply_to_step(
            st, frame, p["rec_pt"], p["act_pt"], p["box_px"], same,
            radius=int(self._cap_cfg().get("execution", {}).get("click_radius", 6)),
            click_type=ctype)
        rec_rel = tuple(anchor["point"])
        if ctx is None:                                # 主窗：模板进内存缓存 + 落 profile
            self.templates[sid] = tmpl
            storage.save_profile(self.profile)          # 改完即存，别只活在内存里

        for w in self.winfo_children():
            if isinstance(w, ReviewDialog):
                w.close()
        self.pending = None
        self._log("✅ %s已保存：识别点 (%.3f, %.3f)　操作点 %s　操作类型 %s　模板 %s.png"
                  % (self._cap_step_label(i), rec_rel[0], rec_rel[1],
                     "同识别点" if same else "单独",
                     "双击" if ctype == picker.CLICK_DOUBLE else "单击",
                     sid), "ok")
        self._status("edit", "编辑中")

        if ctx is not None:
            # 仓库那条路：数据已经写进脚本自己的 steps，**由仓库窗口负责落盘**
            # （"谁拥有数据谁负责存" —— 主窗不该替仓库写 scripts.json）。
            self._cap_ctx = None
            try:
                ctx["on_saved"](i)
            except Exception as exc:                   # noqa: BLE001
                self._log("回写脚本仓库失败：%s" % exc, "err")
            return

        self._refresh_steps()
        # ⚠ 两个坑，都在这一小段里，别改回去：
        #  ① `selection_set` 要的是**行号**，不是 steps 下标。保存用的是 `i`（下标），
        #     只要用户用过上移/下移/删除，`_step_view` 的行序就与下标不同 ——
        #     直接 `selection_set(i)` 会选中**错的那一行**，用户"返回查看"时
        #     看到的是别的步骤的类型（"我改的明明没生效"），实为此故。
        #     必须经由 `_step_view` 反查行号。
        #  ② `selection_set` **不会**触发 `<<ListboxSelect>>`，`_show_detail` 不会自己刷新 ——
        #     必须显式调一次，否则右侧详情停在空状态（用户看到一片空白）。
        self.lst.selection_clear(0, "end")
        row = self._row_of_step(i)
        if row is not None:
            self.lst.selection_set(row)
            self.lst.see(row)
        self._show_detail()

    # ------------------------------------------------------------------ #
    # 跑流程
    # ------------------------------------------------------------------ #
    def _probe(self):
        """抓一帧，对每个步骤打印置信度 —— 一眼看出"画面不对"还是"模板不对"。"""
        kind, hwnd = self._resolve_target()
        if kind is None:
            messagebox.showwarning("找不到目标窗口",
                                   "先点右上角【选择目标窗口】（也可以直接选「整个屏幕」）")
            return
        self._hide_own_tops()
        self.withdraw()
        self.after(140, lambda: self._do_probe(kind, hwnd))

    def _do_probe(self, kind, hwnd):
        if kind == "screen":
            time.sleep(0.18)
            frame = winio.capture_primary_screen()
        else:
            if not winio.activate(hwnd):
                self._restore_self()
                messagebox.showwarning("切不到前台", "请手动点一下目标窗口再试。")
                return
            time.sleep(0.18)
            frame = winio.capture_client(hwnd)
        ok, why = winio.frame_is_usable(frame)
        self._restore_self()
        if not ok:
            self._log("看一眼失败：%s" % why, "err")
            return
        ref = self.profile.get("reference", {})
        cw, ch = frame.shape[1], frame.shape[0]
        scale = geometry.global_scale(cw, ch, ref.get("w", 0), ref.get("h", 0))
        scales = [s * scale for s in self.profile["match"]["scales"]]
        thr = float(self.profile["match"]["threshold"])
        self._log("—— 当前画面 %dx%d，逐步骤报告 ——" % (cw, ch))
        best = None
        for i, st in enumerate(self.profile["steps"]):
            a = st.get("anchor") or {}
            tmpl = self.templates.get(st["id"])
            if not a.get("point") or tmpl is None:
                self._log("第%d步「%s」：还没标，跳过" % (i + 1, st.get("name", "")), "muted")
                continue
            bx = geometry.rect_rel_to_abs(a["box"], cw, ch)
            px, py = geometry.point_rel_to_abs(a["point"], cw, ch)
            roi = geometry.search_window(px, py, bx[2], bx[3], cw, ch,
                                         float(self.profile["match"].get("search_margin_ratio", 0.6)))
            r = matcher.match_template(frame, tmpl, roi, scales, thr)
            tag = "ok" if r.ok else "muted"
            self._log("第%d步「%s」：%.4f → %s" % (i + 1, st.get("name", ""), r.score,
                                                  matcher.explain_score(r.score)), tag)
            if best is None or r.score > best[1]:
                best = (i, r.score, r.ok)
        if best:
            self._log("判别结果：像第 %d 步「%s」" % (best[0] + 1,
                                                  self.profile["steps"][best[0]].get("name", "")),
                      "ok" if best[2] else "warn")
        else:
            self._log("没有任何步骤可判别", "warn")

    def _start(self):
        # **抢闸门**：非阻塞抢锁。抢不到 = 已有一次执行在路上 —— 直接忽略本次。
        # 旧写法是 `if self._running: return`（弱守卫）：连点两次时，第一次
        # `self.withdraw()` 到 `self.after(160, ...)` 之间 `_running` 已置 True，
        # 能挡住一部分；但双入口并发（将来的计划任务/热键）没有任何保护。
        # 抢到锁后若中途 `return`，**必须显式释放**，否则永久锁死。
        if not self._exec_lock.acquire(blocking=False):
            self._log("已在执行中，忽略本次【开始】", "warn")
            return
        hold = True                     # 闸门是否仍由本函数持有
        try:
            ready, why = storage.profile_ready(self.profile)
            if not ready:
                messagebox.showwarning("还不能跑", why)
                return
            kind, hwnd = self._resolve_target()
            if kind is None:
                messagebox.showwarning("找不到目标窗口",
                                       "目标窗口可能没开。先把它打开，或重新【选择目标窗口】"
                                       "（也可以直接选「整个屏幕」）。")
                return
            dry = not self.dry_var.get()
            unfitted = storage.unfit_steps(self.profile)
            if not dry:
                warn = ("接下来程序会真的操作鼠标去点你的目标程序。\n\n"
                        "确认目标程序已经停在起始的那一步画面上？")
                if unfitted:
                    warn += "\n\n注意：以下步骤还没标齐，本次会跳过 —— %s" % \
                            "、".join("第%d步(缺%s)" % (i, "、".join(m)) for i, m in unfitted)
                if not messagebox.askyesno("真的要动鼠标了", warn):
                    return
            self._running = True
            hold = False            # 正式进入执行 —— 闸门交给 _on_running_change 收口
        finally:
            if hold:
                # 上述任一分支提前 return（含异常）：本次没真正跑起来，**当场还锁**
                self._exec_lock.release()

        self.stop_event.clear()
        self._set_step_label(0)
        self.conclusion.configure(text="", fg=MUTED)
        self.btn_start.configure(state="disabled")

        self._log("=" * 56)
        rl = self._runlog_open(dry)
        if rl:
            self._log("本次执行日志：%s" % rl, "muted")
        self._log("开始：%s　目标：%s"
                  % ("干跑（只识别不点击）" if dry else "真实点击",
                     storage.target_desc(self.profile)), "ok")
        if unfitted:
            self._log("本次只跑已标齐的 %d 步；未标齐的 %d 步会跳过：%s"
                      % (len(self.profile["steps"]) - len(unfitted), len(unfitted),
                         "、".join("第%d步" % i for i, _m in unfitted)), "warn")
        if kind == "screen" and not dry:
            self._log("全屏模式提醒：马上要让开画面；桌面上别的窗口也会被拍进去 —— "
                      "跑完记得自己把画面收回来。", "warn")
        # ★ 真实点击前**先探一次鼠标注入链路**（干跑不探 —— 它不动鼠标）。
        #   理由：`SendInput` 会**返回成功却毫无效果**（本机实测），
        #   不探的话用户只会看到"程序说点了、鼠标没动"，无从判断是环境问题还是标点错了。
        #   探测只移动光标（几十毫秒内还原）、不产生点击，代价极低。
        if not dry:
            probe = winio.probe_injection()
            if probe["ok"] and probe["how"] == "SendInput":
                self._log("鼠标注入自检：正常（%s）" % probe["detail"], "info")
            elif probe["ok"]:
                # 功能可用，但走了降级路径 —— 如实说明，免得日后对不上账
                self._log("鼠标注入自检：%s" % probe["detail"], "warn")
            else:
                # 链路不通：**这是环境问题，不是标点问题**，必须说清楚
                self._log("鼠标注入自检：%s" % probe["detail"], "err")
        # 点【开始】先把自己最小化 —— 否则截到的是自己；自家窗口（设置栏等）一起收
        self._hide_own_tops()
        self.withdraw()
        self.after(160, lambda: self._spawn_worker(dry))

    def _spawn_worker(self, dry):
        prof = self.profile
        templates = dict(self.templates)
        stop_event = self.stop_event
        q = self.q

        def _work():
            winio.set_dpi_aware()                  # **每个后台线程入口都要重申**

            def on_event(kind, payload):
                # 显式类型，别靠元组长度
                q.put((kind, payload))

            def log(text, tag="info"):
                q.put(("log", (text, tag)))

            try:
                host = create_live_host(prof, log=log)
                if host is None:
                    q.put(("error", "认不到目标窗口 —— 它可能被关了，或标题变了。"
                                    "也可以改用「整个屏幕」模式。"))
                    q.put(("result", {"terminal": "中止：找不到目标窗口", "ok": False,
                                      "reason": "窗口没开或标题变了", "done": 0,
                                      "total": len(prof.get("steps", [])), "dry": dry}))
                    return
                runner = Runner(prof, host, templates=templates,
                                on_event=on_event, dry=dry, stop_event=stop_event)
                result = runner.run()
                q.put(("result", result))
            except Exception as exc:
                # **后台线程必须自己兜异常**：线程里逃逸的异常不会走
                # `report_callback_exception`（那只管 Tk 回调），不兜就是**静默死线程**。
                # 这里把异常原文 + traceback 一起回传主线程显示（见 `_handle("error")`）。
                import traceback
                tb = traceback.format_exc()
                # ⚠ **后台线程的异常也要落进 crash.txt**（曾经是盲区）：
                #   `crash.txt` 原先只覆盖 Tk 回调，于是"用户看到红字、
                #   我们翻不到任何记录"（真实点击崩在 `dwExtraInfo=None`
                #   那次就是这个情形）。后台线程这一路必须自己写盘。
                try:
                    safeui.write_crash("【后台执行异常】\n%s" % tb,
                                       context="worker",
                                       title="后台执行异常")
                except Exception:
                    pass
                q.put(("error", "%s\n%s" % (exc, tb)))
            finally:
                # **无论成功/失败/异常，都必须发这一条** —— 它是闸门（`_exec_lock`）
                # 的唯一释放信号来源（主线程 `_on_running_change` 收到才还锁）。
                # 漏发 = 永久锁死，界面再也点不动【开始】。
                q.put(("running", False))

        self.worker = threading.Thread(target=_work, daemon=True)
        self.worker.start()

    def _stop(self):
        if not self._running:
            return
        self.stop_event.set()
        self._log("已请求停止 —— 等当前这一拍走完就停", "warn")

    def _drain(self):
        """主线程定时取队列（UI 只在主线程更新）。"""
        try:
            for _ in range(60):
                kind, payload = self.q.get_nowait()
                self._handle(kind, payload)
        except queue.Empty:
            pass
        except Exception:
            pass
        self.after(80, self._drain)

    def _handle(self, kind, payload):
        if kind == "log":
            text, tag = payload
            self._log(text, tag)
        elif kind == "status":
            self._status(payload)
        elif kind == "step":
            self._set_step_label(int(payload[0]), int(payload[1]))
        elif kind == "running":
            self._on_running_change(bool(payload))
        elif kind == "save_frame":
            if payload:
                self._log("出错现场：%s" % payload, "warn")
        elif kind == "error":
            self._log("出错了：%s" % payload, "err")
        elif kind == "result":
            self._on_result(payload)
        elif kind == "finish":
            pass

    def _on_running_change(self, running):
        """执行状态变化的**唯一收口点** —— 闸门也在这里释放。

        为什么必须收口在这里：`self._running` 的 True→False 有两条来路
        （正常跑完 / 出错中止），都在 `_spawn_worker._work` 的 `finally` 里
        `q.put(("running", False))` 汇入。若在别处各放一次锁，一旦漏掉任一路径，
        闸门就**永久锁死**，界面再也点不动【开始】。
        """
        self._running = running
        if not running:
            # 幂等释放：只有真正持锁时才 release（Lock 无 owner，用 _running 已置 False
            # 前的状态判断不可靠，改用 try/except 兜住「本就没持锁」的情况）
            try:
                if self._exec_lock.locked():
                    self._exec_lock.release()
            except RuntimeError:
                pass                      # 未持锁时 release 会 RuntimeError，忽略
            self._restore_self()
            # ⚠ 必须走 `_btn_set_enabled`，不能直接 `configure(state="normal")`：
            #   裸 configure 只解禁"能不能点"，却把底色留在禁用色（BTN_DISABLED）——
            #   按钮看着还是灰的，用户以为没恢复。底色归位是启用动作的一半。
            _btn_set_enabled(self.btn_start, True)
            _btn_set_enabled(self.btn_stop, False)
            self._status("idle", "待命")
            self._runlog_close()
        else:
            # 【停止】只在真的跑起来时才可点 —— 原来它从头到尾都是亮着的，
            #   没在跑时点它毫无反馈，用户会以为"停不掉"。
            _btn_set_enabled(self.btn_stop, True)

    def _on_result(self, res):
        ok = res.get("ok")
        term = res.get("terminal", "")
        reason = res.get("reason", "")
        color = OKC if ok else ERRC
        # **结论必须明确**：全跑完 ✓ / 中止 ✗ + 原因（不许静默结束）
        self.conclusion.configure(
            text="%s　%s%s" % ("✓" if ok else "✗", term,
                             ("（%s）" % reason) if reason else ""), fg=color)
        if ok:
            self._status("done", "完成")
            self.pb["value"] = 100
        else:
            self._status("abort", "中止")

    def _on_close(self):
        if self._running:
            if not messagebox.askyesno("还在跑", "流程还在跑，确定要关掉吗？"):
                return
            self.stop_event.set()
            # 关闭时若仍在跑：显式还锁，别让闸门跟着窗口一起「带走但没释放」
            # （进程即将结束，这里主要是给「关闭后又被复用」的场景留一道保险）
            try:
                if self._exec_lock.locked():
                    self._exec_lock.release()
            except RuntimeError:
                pass
        self.destroy()


def main():
    winio.require_win32()          # 缺 pywin32 时窗口查找会静默返回 None，早点报
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
