# -*- coding: utf-8 -*-
"""小窗模式布局验证 —— 逐控件量几何，确认没有被挤出可视区。

为什么要写这个：
    用户报「小窗模式下 UI 显示不完整」。靠肉眼看截图只能"感觉挤"，
    真正的判据是**几何**：控件的 winfo_rootx/y + 宽高 必须落在
    窗口客户区之内。本脚本把窗口设成几个偏窄的尺寸，逐个量。

反向对照：
    --check-dead 会故意把日志区改回旧的「pack 在最后 + 无 bottom 保位」写法，
    此时下方控件必然出界 —— 用来证明本脚本真的量得出问题，
    而不是永远打印 OK（测的是假通过）。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ⚠ 必须用带 tkinter 的解释器
try:
    import tkinter as tk
except Exception as e:                                          # pragma: no cover
    print("需要带 tkinter 的 Python：", e)
    raise SystemExit(2)

import gui                                                      # noqa: E402
from src import storage as _storage                              # noqa: E402

# ⚠ **掐掉落盘**：本脚本会反复切模式/加步骤，gui 的收尾函数会真写
#   config/profile.json —— 跑一次就把用户真实配置改了。换成内存版，
#   profile 照改、界面照刷，就是不落盘。
_storage.save_profile = lambda _p: True
gui.storage.save_profile = lambda _p: True

# 用户截图里那个偏窄的尺寸 + 边界值。
# ⚠ 每个尺寸都必须 ≥ minsize(880,630)，否则会被 Tk 钳回去，测出来是同一个数 ——
#   那就是"假绿"。这正是上一轮的翻车点：minsize 虚高成 (1020,660)，把 960×620、
#   1084×600 全钳成 1020×660，脚本却仍打印"全过"。
#   · 1084×600 是用户实拍口径：宽 1084 保得住（>880），高 600 会被钳到 630 ——
#     那不是缺陷，是 minsize 在**尽责**。它在这里的作用变成"验证钳制生效"。
#   · 620/600 这类低于下限的高度，下面有专门一段验证"会被挡回去"。
SIZES = [(1180, 760), (1084, 660), (1020, 660), (960, 630), (880, 630)]

# 期望「永远看得见」的控件：名字 → 取控件的 lambda
WATCH = [
    ("顶栏·选择目标窗口", lambda a: a.btn_window),
    ("顶栏·存入仓库", lambda a: a.btn_to_repo),
    ("顶栏·状态文字", lambda a: a.lb_mode),
    ("左栏·步骤列表", lambda a: a.lst),
    ("左栏·采集本步画面", lambda a: a.btn_capture),
    ("左栏·新增一步", lambda a: a.btn_add),
    ("左栏·插入一步", lambda a: a.btn_ins),
    ("左栏·上移", lambda a: a.btn_up),
    ("左栏·下移", lambda a: a.btn_down),
    ("左栏·复制上一步", lambda a: a.btn_copy),
    ("左栏·删除", lambda a: a.btn_del),
    # ⚠ 标签写「右栏」不是笔误：选择器原本在左栏按钮区，被挤出可视区后
    #   搬到了右栏顶部（side="top" 先抢位，吃不到挤压）。详见 6.12 第 5 条。
    ("右栏·操作类型下拉", lambda a: a.cmb_click_type),
    ("右上·类型条容器", lambda a: a._ct_bar),
    # 日志**标题条 + 两个开关**也必须始终可见：日志正文是可折叠内容，
    # 矮窗下让位合理；但"能操作到日志"的入口不能在。
    ("日志·标题条", lambda a: a._logf),
    ("日志·收起/展开", lambda a: a.btn_log_toggle),
    ("日志·清空", lambda a: a.btn_clear),
    ("右栏·开始", lambda a: a.btn_start),
    ("右栏·停止", lambda a: a.btn_stop),
    ("右栏·看一眼当前画面", lambda a: a.btn_probe),
    ("右栏·日志字号小", lambda a: a.btn_log_small),
    ("右栏·日志字号大", lambda a: a.btn_log_big),
    ("右栏·进度条", lambda a: a.pb),
    ("日志·收起/展开", lambda a: a.btn_log_toggle),
    ("日志·清空", lambda a: a.btn_clear),
]


class Ctx:
    def __init__(self):
        self.passed = 0
        self.failed = 0

    def ok(self, cond, msg):
        if cond:
            self.passed += 1
            print("  [PASS] %s" % msg)
        else:
            self.failed += 1
            print("  [FAIL] %s" % msg)
        return bool(cond)


def window_rect(app):
    """客户区在屏幕上的矩形（左上 + 右下）。"""
    app.update_idletasks()
    x = app.winfo_rootx()
    y = app.winfo_rooty()
    w = app.winfo_width()
    h = app.winfo_height()
    return x, y, x + w, y + h


def inside(app, wdg):
    """控件是否完整落在客户区内（含 1px 容差）。"""
    if wdg is None or not wdg.winfo_exists():
        return False, "控件不存在"
    if not wdg.winfo_ismapped():
        return False, "未显示（被挤出可视区）"
    wx, wy, wx2, wy2 = window_rect(app)
    x = wdg.winfo_rootx()
    y = wdg.winfo_rooty()
    w = wdg.winfo_width()
    h = wdg.winfo_height()
    if w <= 1 or h <= 1:
        return False, "尺寸为 0（未布局）"
    # 允许 1px 容差（边框像素归属）
    if x < wx - 1 or y < wy - 1 or x + w > wx2 + 1 or y + h > wy2 + 1:
        return False, ("出界 x=%d y=%d w=%d h=%d / 窗口 %d,%d~%d,%d"
                       % (x, y, w, h, wx, wy, wx2, wy2))
    return True, ""


def run_sizes(dead=False):
    c = Ctx()
    app = gui.App()
    app.update()

    if dead:
        # 反向对照：还原**旧布局的两个致命前提**，缺一不可 ——
        #   ① 日志区不做 side="bottom" 保位（改 pack 在主体之后）
        #   ② 步骤列表用**固定 18 行高度**（不再 expand 让位）
        # 只改 ① 是不够的：新布局的列表会主动压缩让空间，把缺陷吃掉。
        # 必须把「造成挤压的原因」一并还原，才叫真正的对照。
        print("  （反向对照模式：还原旧写法 —— 日志不保位 + 列表固定 18 行）")
        app._logf.pack_forget()
        app._logf.pack(side="top", fill="x", padx=10, pady=(0, 10))
        app.lst.configure(height=18)
        app.update()

        # ③ **本次 bug 的病因**：把类型条搬回左栏按钮区的末尾。
        #   左栏按钮区已有一堆 side="bottom"/grid 行，这一条排在最后，
        #   窗口一矮就被挤出去 —— 用户实拍看到的正是这个。
        #   只做 ①② 是还原不了"下拉不见"的，必须把类型条真的挪回去。
        print("  （并注入了本次 bug 的病因：类型条搬回左栏按钮区末尾）")
        cmb = app.cmb_click_type
        note = app.lb_ct_note
        cmb.pack_forget()
        note.pack_forget()
        app._ct_bar.pack_forget()
        # 直接把下拉挂到左栏按钮区（lbtns）最末
        cmb.master = app.lbtns
        cmb.pack(side="top", fill="x")
        app.update()

    for (W, H) in SIZES:
        print("\n—— 窗口 %d×%d ——" % (W, H))
        app.geometry("%dx%d" % (W, H))
        app.update()
        app.update_idletasks()
        # 让 after 队列里排的布局回调跑一轮
        for _ in range(3):
            app.update()

        # ── 钳制哨兵：minsize 若高于请求尺寸，Tk 会把窗口顶回去 ──
        # 这就是上一轮"假绿"的根源：所有尺寸都被钳到同一个值，测什么都是绿的。
        # 单独断言出来，让钳制**显形**而不是被悄悄吞掉。
        aw, ah = app.winfo_width(), app.winfo_height()
        c.ok(aw >= W - 2 and ah >= H - 2,
             "请求 %dx%d → 实际 %dx%d（未被 minsize 顶回）" % (W, H, aw, ah))

        bad = []
        for name, getter in WATCH:
            try:
                wdg = getter(app)
            except AttributeError as e:
                c.failed += 1
                print("  [FAIL] %s：取不到控件（%s）" % (name, e))
                continue
            good, why = inside(app, wdg)
            if good:
                c.passed += 1
            else:
                c.failed += 1
                bad.append(name)
                print("  [FAIL] %s：%s" % (name, why))
        if not bad:
            print("  [PASS] %d 个关键控件全部可见" % len(WATCH))

        # 日志文本框：矮窗下允许让位（它是可折叠内容），但**不许半死** ——
        # 要么完整在界内，要么整条未映射（用户点【收起】即可）。
        # "露一半"才是真的坏：既看不见又占着高度。
        good, why = inside(app, app.log)
        if good:
            c.passed += 1
            print("  [PASS] 日志文本框可见")
        elif not app.log.winfo_ismapped():
            c.passed += 1
            print("  [PASS] 日志文本框让位（整条未映射，非半截残留）")
        else:
            c.failed += 1
            print("  [FAIL] 日志文本框：%s" % why)

        # ── 位置断言：类型选择器必须"贴顶"而不是"沉底" ──
        # 光断言"可见"是不够的：当初它挂在左栏按钮区的最底部，
        # 窗口一矮就整条沉出可视区。改成右栏 side="top" 后应当**恒在上半部**。
        # 判据用「距窗口顶部的距离 < 窗口高度的 1/3」—— 与像素无关，随尺寸缩放。
        _, wy, _, wy2 = window_rect(app)
        ch_y = app.cmb_click_type.winfo_rooty()
        head_room = (wy2 - wy) / 3.0
        c.ok(app._ct_bar.winfo_ismapped() and (ch_y - wy) < head_room,
             "操作类型下拉贴着窗口顶部（距顶 %dpx < 窗高/3 %.0fpx）"
             % (ch_y - wy, head_room))

    # ── minsize 是否真的尽责：请求小于下限的尺寸，Tk 必须顶回 ──
    # 上一轮 minsize(1020,660) 的问题不是"太小"，而是**用户窗口比它矮**，
    # 它没能成为安全网。这里把它当契约测：请求 600 高 → 实际不得低于 630。
    print("\n—— minsize 尽责性 ——")
    min_w, min_h = app.minsize()
    c.ok((min_w, min_h) == (880, 630),
         "minsize = (%d, %d)" % (min_w, min_h))
    app.geometry("600x400")
    for _ in range(3):
        app.update()
    aw, ah = app.winfo_width(), app.winfo_height()
    c.ok(aw >= min_w and ah >= min_h,
         "请求 600x400 被顶回 %dx%d（≥ minsize %dx%d）" % (aw, ah, min_w, min_h))

    # 状态文字截断能力验证（窄窗口下必须真的截断，且带省略号）
    print("\n—— 状态文字截断 ——")
    app.geometry("960x620")
    app.update()
    long_txt = "（执行模式未解锁：第1步缺识别点、缺操作点；第2步缺识别点……）" * 3
    app._set_mode_text(long_txt, gui.WARNC)
    app.update()
    shown = app.lb_mode.cget("text")
    c.ok(len(shown) < len(long_txt),
         "超长状态文字被截断（原文 %d 字 → 显示 %d 字）" % (len(long_txt), len(shown)))
    c.ok(shown.endswith("…"), "截断处带省略号（证明是真截断不是空白）")
    # 反向对照：宽窗口下同样的文字不该被截断
    app.geometry("1600x800")
    app.update()
    app._set_mode_text("短文案", gui.WARNC)
    app.update()
    c.ok(app.lb_mode.cget("text") == "短文案", "宽窗口下短文案原样显示，不误截")

    # 日志折叠/展开
    print("\n—— 日志折叠开关 ——")
    open_before = app._log_body.winfo_ismapped()
    app._toggle_log()
    app.update()
    c.ok(not app._log_body.winfo_ismapped(), "点【收起】后日志体不再显示")
    app._toggle_log()
    app.update()
    c.ok(app._log_body.winfo_ismapped(), "再点【展开】后日志体恢复显示")
    c.ok(app._log_open == open_before, "折叠状态标志回到初始值")

    # 日志滚动条延迟复判（P4）：空日志不显示，灌满才显示
    print("\n—— 日志滚动条延迟复判 ——")
    import time as _t

    def _pump(ms=300):
        t0 = _t.time()
        while (_t.time() - t0) * 1000 < ms:
            app.update()
            _t.sleep(0.01)

    app.btn_clear.invoke()
    _pump()
    c.ok(not app._log_sb.winfo_ismapped(),
         "空日志时滚动条**不占位**（旧代码恒 pack，白占 12px）")

    for i in range(60):
        app._log("第 %d 行测试内容 —— 用来把日志灌过一屏" % i)
    _pump()
    c.ok(app._log_sb.winfo_ismapped(), "日志溢出一屏后滚动条自动出现")

    app.btn_clear.invoke()
    _pump()
    c.ok(not app._log_sb.winfo_ismapped(), "清空日志后滚动条自动收起（状态可逆）")

    # **反向对照（注入病因）**：把复判彻底停掉 —— 灌满一屏后滚动条**不该出现**。
    # 这才证明「上面的'自动出现'确实是复判的功劳」，而不是别的东西顺手 pack 了它。
    real_sched = app._log_sb_schedule
    app._log_sb_schedule = lambda: None       # 注入病因：摘掉复判调度
    try:
        app.btn_clear.invoke()
        app._log_sb.pack_forget()
        app._log_sb_shown = False
        _pump(150)
        for i in range(60):
            app._log("对照：灌满也不该出现滚动条 %d" % i)
        _pump(300)
        c.ok(not app._log_sb.winfo_ismapped(),
             "**反向对照**：摘掉复判后灌满一屏，滚动条**不出现**"
             "（证明上面的'自动出现'确实是复判的功劳）")
    finally:
        app._log_sb_schedule = real_sched
        app._log_sb_shown = False
        app.btn_clear.invoke()
        _pump(200)
        c.ok(not app._log_sb.winfo_ismapped(),
             "恢复复判并清空日志后，滚动条回到隐藏态")

    app.destroy()
    return c


def main():
    dead = "--check-dead" in sys.argv

    if dead:
        print("=" * 62)
        print("反向对照：故意制造缺陷 → 期望本脚本能测出问题")
        print("=" * 62)
        c = run_sizes(dead=True)
        print("\n反向对照结果：通过 %d，失败 %d" % (c.passed, c.failed))
        if c.failed == 0:
            print("\n[FAIL] 反向对照没有测出任何问题 —— 说明本脚本是假通过！")
            return 1
        print("\n[PASS] 反向对照按预期测出了问题（证明脚本有效）")
        return 0

    print("=" * 62)
    print("小窗模式布局验证")
    print("=" * 62)
    c = run_sizes(dead=False)
    print("\n" + "=" * 62)
    print("小窗布局：通过 %d，失败 %d" % (c.passed, c.failed))
    print("=" * 62)
    return 0 if c.failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
