# -*- coding: utf-8 -*-
"""量脚本仓库底部按钮排的**真实宽度**，专门盯【关闭】。

用户报：「脚本仓库的关闭按钮被挤的很小」。

先量再改 —— 不凭眼看。本脚本对**多个窗口宽度**逐档量：
  - 整排按钮的所需宽度（reqwidth）与可用宽度
  - 每个按钮的 winfo_width / winfo_reqwidth
  - 【关闭】是否被压缩（实际宽 < 所需宽）

用法：
    .venv\\Scripts\\python.exe tools\\probe_close_btn.py
"""
import os
import sys
import tkinter as tk

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("AWB_NO_EXEC", "1")

import gui  # noqa: E402


def _desc(w):
    try:
        return w.cget("text")
    except Exception:
        return "?"


def probe(width, height):
    app = gui.App()
    app.update_idletasks()
    try:
        win = gui.ScriptRepoWindow(app)
    except Exception as e:  # noqa: BLE001
        print("  开窗失败：%r" % (e,))
        app.destroy()
        return
    win.geometry("%dx%d" % (width, height))
    win.update_idletasks()
    win.update()

    print("=" * 68)
    print("窗口 %dx%d" % (width, height))

    # ---- ① 底部按钮排（grid 两行）----
    bar = win.btn_save.master
    print("  bar 实际宽=%d  所需宽=%d" % (bar.winfo_width(), bar.winfo_reqwidth()))
    bad = []
    for w in bar.winfo_children():
        if not isinstance(w, tk.Button):
            continue
        aw, rw = w.winfo_width(), w.winfo_reqwidth()
        st = "OK" if aw >= rw else "★被压 %d px" % (rw - aw)
        if aw < rw:
            bad.append((_desc(w), aw, rw))
        print("  %-14s x=%-5d y=%-3d 实宽=%d 所需=%d  %s"
              % (_desc(w), w.winfo_x(), w.winfo_y(), aw, rw, st))
    if bad:
        print("  → ★底部被压缩：%s"
              % ", ".join("「%s」%d<%d" % t for t in bad))
    else:
        print("  → 底部按钮全部拿到所需宽度 ✓")

    # ---- ②【关闭】在标题栏，单独量 ----
    cb = win.btn_close
    aw, rw = cb.winfo_width(), cb.winfo_reqwidth()
    print("  【关闭】标题栏 x=%d y=%d 实宽=%d 所需=%d %s"
          % (cb.winfo_x(), cb.winfo_y(), aw, rw,
             "OK" if aw >= rw else "★被压 %d px" % (rw - aw)))
    print("  【关闭】底边=%d  窗口高=%d  %s"
          % (cb.winfo_y() + cb.winfo_height(), win.winfo_height(),
             "在窗内 ✓" if cb.winfo_y() + cb.winfo_height() <= win.winfo_height()
             else "★出界"))
    print("  【关闭】右边缘=%d  窗口宽=%d  %s"
          % (cb.winfo_x() + cb.winfo_width(), win.winfo_width(),
             "在窗内 ✓" if cb.winfo_x() + cb.winfo_width() <= win.winfo_width()
             else "★出界"))

    try:
        win.close()
    except Exception:  # noqa: BLE001
        pass
    try:
        app.destroy()
    except Exception:  # noqa: BLE001
        pass


def main():
    for w, h in ((1240, 880), (1100, 800), (1000, 700), (1000, 620)):
        probe(w, h)


if __name__ == "__main__":
    main()
