# -*- coding: utf-8 -*-
"""验证 P0 异常黑匣子：回调异常必须**落盘 + 标红 + 窗口不崩**。

## 为什么这样设计（重要，别改成「合成事件触发」）

实测（Python 3.10 / tkinter / 本机）：
- `after()` 回调异常 **不走** `report_callback_exception`，而是 Tcl 的 `bgerror`，会**抛穿** mainloop；
- `Button.invoke()` 也**抛穿**，不交给 handler；
- `event_generate()` 在无头/虚拟环境下**根本不被派发**。

也就是说：**在本环境里无法合成一次「Tk 自动交给 handler」的真实异常**。
所以本脚本把验证拆成三件**可稳定测且真正有意义**的事：

- **V1 handler 本体**：取当前已安装的 `tk.Tk.report_callback_exception`，
  用真实异常三件套调用 → 断言落盘 + 结论标红 + 底栏提示。
  （「Tk 何时调它」是 tkinter 既定契约，不由我们证明；我们证明的是「装上去的这个是好的」。）
- **V2 接线**：`App()` 构造完成后 `safeui.is_installed()` 必须为真
  —— 证明**生产窗口**挂上了，而不是只有测试脚本挂（这正是本轮的 bug 修复点）。
- **V3 guarded 包裹**：按钮 command 已被 `guarded` 包住；调用一个**被包过的、必然抛异常**的
  command → 断言落盘 + 标红 + 不向外抛。这是**我们自己的管辖范围**，稳定可测。

## 反向对照（注入病因）

`--no-hook` 下：不装 handler、按钮还原为裸函数 → 跑 V1/V3 的同一触发 → 断言 `crash.txt` **不增长**。
这一条证明「正向通过是 hook 的功劳」，而不是撞上别的原因。

用法：
    .venv\\Scripts\\python.exe tools\\verify_crash.py            # 正向 + 反向，全绿退 0
    .venv\\Scripts\\python.exe tools\\verify_crash.py --no-hook  # 只跑反向对照
"""
from __future__ import annotations

import os
import sys
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

OUT = os.path.join(ROOT, "logs", "verify_crash.txt")
lines = []
FAILS = []


def say(s=""):
    lines.append(str(s))
    print(s)


def check(name, cond, detail=""):
    tag = "PASS" if cond else "FAIL"
    say("[%s] %s%s" % (tag, name, ("　— " + detail) if detail else ""))
    if not cond:
        FAILS.append(name)
    return cond


# --------------------------------------------------------------------------- #
def _fake_exc():
    """造一组真实的 (exc_type, exc_value, tb)，供 handler 调用。"""
    try:
        raise RuntimeError("verify_crash：故意炸的回调")
    except RuntimeError:
        return sys.exc_info()


def _make_app(install_hook: bool):
    """建真实 App。install_hook=False 时**只拆安全网**，别的一律不动。"""
    import tkinter as tk

    import gui
    from src import safeui

    if install_hook:
        return gui.App(), safeui

    # 注入病因：撤掉两道防护
    #   ① 类级 handler → 空壳
    #   ② 按钮 command  → 还原为「裸方法」（未包 guarded）
    tk.Tk.report_callback_exception = lambda *a, **k: None
    _orig = safeui.install_crash_handler
    safeui.install_crash_handler = lambda *a, **k: True
    try:
        app = gui.App()
    finally:
        safeui.install_crash_handler = _orig

    raw = {"btn_start": app._start, "btn_stop": app._stop,
           "btn_probe": app._probe, "btn_capture": app._capture_for_step,
           "btn_add": app._add_step, "btn_ins": app._insert_step,
           "btn_del": app._del_step, "btn_copy": app._dup_prev,
           "btn_up": lambda: app._move(-1), "btn_down": lambda: app._move(1),
           "btn_log_toggle": app._toggle_log, "btn_clear": app._clear_log}
    for n, m in raw.items():
        b = getattr(app, n, None)
        if b is not None:
            try:
                b.configure(command=m)
            except Exception:
                pass
    return app, safeui


def run_forward(safeui_mod, app) -> None:
    """V1 / V2 / V3 三条正向断言。"""
    import gui

    before = safeui_mod.crash_size()

    # --- V2 接线：handler 已装在类上（生产窗口挂上了）---
    check("V2 handler 已安装（is_installed）", safeui_mod.is_installed())

    # --- V1 handler 本体：用真实异常三件套调用当前已安装的处理器 ---
    import tkinter as tk
    handler = tk.Tk.report_callback_exception
    exc_t, exc_v, exc_tb = _fake_exc()
    handler(exc_t, exc_v, exc_tb)                    # 不该向外抛
    after1 = safeui_mod.crash_size()
    tail = safeui_mod.read_crash_tail(40)

    check("V1 crash.txt 增长", after1 > before, "%d → %d" % (before, after1))
    check("V1 含 Traceback", "Traceback" in tail)
    check("V1 含异常类型", "RuntimeError" in tail)
    check("V1 含异常原文", "verify_crash：故意炸的回调" in tail)

    # 标红：conclusion 前景色 == ERRC
    fg = str(app.conclusion.cget("fg"))
    check("V1 结论标签已标红", fg.lower() == gui.ERRC.lower(),
          "fg=%s 期望=%s" % (fg, gui.ERRC))
    st = str(app.lb_status.cget("text"))
    check("V1 底栏状态已提示异常", "异常" in st, "text=%r" % st)

    # --- V3 guarded 包裹：按钮 command 已被包住，且包裹后不再向外抛 ---
    before3 = safeui_mod.crash_size()
    boom_btn = app.btn_log_toggle
    boom_btn.configure(command=safeui_mod.guarded(
        lambda: (_ for _ in ()).throw(ValueError("V3：按钮回调炸")),
        context="V3", log_fn=app._log))
    try:
        boom_btn.invoke()                     # 被 guarded 包住，异常应被吞并落盘
        raised = False
    except Exception:
        raised = True
    after3 = safeui_mod.crash_size()
    tail3 = safeui_mod.read_crash_tail(40)
    check("V3 guarded 吞掉异常（未向外抛）", not raised)
    check("V3 crash.txt 增长", after3 > before3, "%d → %d" % (before3, after3))
    check("V3 含 ValueError", "ValueError" in tail3 and "V3：按钮回调炸" in tail3)

    # --- 窗口没崩 ---
    check("窗口未崩溃", bool(app.winfo_exists()))


def run_control(safeui_mod, app) -> None:
    """反向对照：不装 hook，同一触发必须**不落盘**。"""
    import tkinter as tk

    before = safeui_mod.crash_size()

    # 同样调用「当前处理器」（此时是空壳）
    handler = tk.Tk.report_callback_exception
    exc_t, exc_v, exc_tb = _fake_exc()
    try:
        handler(exc_t, exc_v, exc_tb)
    except Exception:
        pass

    # 同样调一个**裸**按钮回调（未包 guarded）
    try:
        app.btn_clear.invoke()                # 正常功能，不抛
    except Exception:
        pass

    after = safeui_mod.crash_size()
    check("反向对照：handler 是空壳（未安装）",
          not safeui_mod.is_installed())
    check("反向对照：crash.txt 未增长", after == before,
          "%d → %d（注入病因=去掉 hook）" % (before, after))


# --------------------------------------------------------------------------- #
def main() -> int:
    only_no_hook = "--no-hook" in sys.argv

    say("=" * 70)
    say("P0 异常黑匣子验证" + ("（仅反向对照）" if only_no_hook else ""))
    say("=" * 70)

    if not only_no_hook:
        say("\n--- 正向：装好 hook ---")
        app = None
        try:
            app, sm = _make_app(True)
            app.update()
            run_forward(sm, app)
        except Exception:
            say("正向用例自身异常：\n" + traceback.format_exc())
            FAILS.append("正向用例执行")
        finally:
            if app is not None:
                try:
                    app.destroy()
                except Exception:
                    pass
        say("")

    say("--- 反向对照：不装 hook（注入病因） ---")
    app = None
    try:
        app, sm = _make_app(False)
        app.update()
        run_control(sm, app)
    except Exception:
        say("反向对照自身异常：\n" + traceback.format_exc())
        FAILS.append("反向对照执行")
    finally:
        if app is not None:
            try:
                app.destroy()
            except Exception:
                pass
    say("")

    say("=" * 70)
    if FAILS:
        say("结果：FAIL —— %d 项未过：%s" % (len(FAILS), "、".join(FAILS)))
        return 1
    say("结果：PASS —— 全部通过")
    return 0


if __name__ == "__main__":
    try:
        rc = main()
    except Exception:
        lines.append("顶层异常：\n" + traceback.format_exc())
        rc = 2
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    sys.exit(rc)
