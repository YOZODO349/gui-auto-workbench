# -*- coding: utf-8 -*-
"""量出「所有关键控件都可见」所需的最小窗口高度。

为什么要有它：`minsize()` 这个数字不能拍脑袋。拍高了 = 拦不住用户手缩窗口
（用户实拍已证实：窗口比 minsize 矮，控件照样被挤出去）；拍低了 = 界面残缺。
唯一靠得住的办法是**扫一遍**，找出真临界线。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gui                                                        # noqa: E402
from src import storage as _storage                                # noqa: E402

_storage.save_profile = lambda _p: True
gui.storage.save_profile = lambda _p: True

WATCH = [
    ("左栏·步骤列表", lambda a: a.lst),
    ("左栏·采集", lambda a: a.btn_capture),
    ("左栏·新增", lambda a: a.btn_add),
    ("左栏·插入", lambda a: a.btn_ins),
    ("左栏·上移", lambda a: a.btn_up),
    ("左栏·下移", lambda a: a.btn_down),
    ("左栏·复制", lambda a: a.btn_copy),
    ("左栏·删除", lambda a: a.btn_del),
    ("操作类型下拉", lambda a: a.cmb_click_type),
    ("日志区", lambda a: a._logf),
    ("日志文本", lambda a: a.log),
]


def inside(app, wdg):
    if wdg is None or not wdg.winfo_exists() or not wdg.winfo_ismapped():
        return False
    app.update_idletasks()
    wx, wy = app.winfo_rootx(), app.winfo_rooty()
    wx2, wy2 = wx + app.winfo_width(), wy + app.winfo_height()
    x, y = wdg.winfo_rootx(), wdg.winfo_rooty()
    w, h = wdg.winfo_width(), wdg.winfo_height()
    if w <= 1 or h <= 1:
        return False
    return not (x < wx - 1 or y < wy - 1 or x + w > wx2 + 1 or y + h > wy2 + 1)


def main():
    app = gui.App()
    app.update()
    W = 1180
    print("固定宽 %d，扫高度（从 460 到 800，步长 10）" % W)
    print("%-6s %s" % ("高", "缺失的控件"))
    print("-" * 62)
    first_all_ok = None
    for H in range(460, 801, 10):
        app.geometry("%dx%d" % (W, H))
        for _ in range(3):
            app.update()
        miss = [n for n, f in WATCH if not inside(app, f(app))]
        if not miss:
            print("%-6d ✓ 全部可见" % H)
            if first_all_ok is None:
                first_all_ok = H
        else:
            print("%-6d 缺：%s" % (H, "、".join(miss)))
    app.destroy()
    print("-" * 62)
    print("全部可见的最小高度 = %s" % first_all_ok)


if __name__ == "__main__":
    main()
