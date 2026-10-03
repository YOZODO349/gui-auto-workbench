# -*- coding: utf-8 -*-
"""实弹验证：把真窗口开起来，用 Win32 枚举顶层窗口，看它到底在不在、可不可见。

比 `winfo_viewable()` 更有说服力 —— 那只是 Tk 自己的账本；
`IsWindowVisible` + `GetWindowRect` 才是桌面上真正发生的事。
顺带验证窗口尺寸是否落在屏幕内（全屏遮罩的虚拟屏尺寸可能跟主屏对不上）。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "logs", "probe_window.txt")

u = ctypes.windll.user32
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        u.SetProcessDPIAware()
    except Exception:
        pass

EnumWindowsProc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)


def enum_title(keyword: str):
    hits = []

    def cb(hwnd, _l):
        n = u.GetWindowTextLengthW(hwnd)
        if n:
            buf = ctypes.create_unicode_buffer(n + 1)
            u.GetWindowTextW(hwnd, buf, n + 1)
            if keyword in buf.value:
                r = wt.RECT()
                u.GetWindowRect(hwnd, ctypes.byref(r))
                hits.append({
                    "hwnd": int(hwnd),
                    "title": buf.value,
                    "visible": bool(u.IsWindowVisible(hwnd)),
                    "iconic": bool(u.IsIconic(hwnd)),
                    "rect": (r.left, r.top, r.right - r.left, r.bottom - r.top),
                })
        return True

    u.EnumWindows(EnumWindowsProc(cb), 0)
    return hits


def main() -> int:
    lines = []

    def say(s):
        lines.append(str(s))
        print(s)

    pyw = os.path.join(ROOT, ".venv", "Scripts", "pythonw.exe")
    if not os.path.exists(pyw):
        pyw = sys.executable
    say("解释器：%s" % pyw)

    p = subprocess.Popen([pyw, os.path.join(ROOT, "gui.py")], cwd=ROOT)
    say("已启动 pid=%d" % p.pid)

    for t in (1.5, 3.0, 5.0):
        time.sleep(t if t == 1.5 else t - prev if False else 1.5)
        prev = t
        hits = enum_title("工作台")
        say("t≈%.1fs  命中的顶层窗口 %d 个：%s" % (t, len(hits), hits))
        say("   进程存活=%s" % (p.poll() is None,))

    hits = enum_title("工作台")
    if not hits:
        say(">>> 结论：桌面上**根本没有**这个窗口 —— 问题在构造/映射阶段（构建即崩）")
    else:
        vis = [h for h in hits if h["visible"] and not h["iconic"]]
        if vis:
            say(">>> 结论：窗口在且可见：%s" % vis)
        else:
            say(">>> 结论：窗口**存在但不可见**（被 withdraw / 最小化）")

    try:
        p.terminate()
    except Exception:
        pass

    # 顺带量一下虚拟屏 vs 主屏，看全屏遮罩的尺寸判断有没有坑
    say("主屏 %dx%d ｜ 虚拟屏 %dx%d 原点(%d,%d)"
        % (u.GetSystemMetrics(0), u.GetSystemMetrics(1),
           u.GetSystemMetrics(78), u.GetSystemMetrics(79),
           u.GetSystemMetrics(76), u.GetSystemMetrics(77)))

    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
