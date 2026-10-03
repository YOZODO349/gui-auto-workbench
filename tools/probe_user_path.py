# -*- coding: utf-8 -*-
"""走**完整真实路径**复现：「复核弹窗里选双击 → 保存 → 切回查看仍显示单击」。

与 verify_ct_echo.py 的区别（这是关键）：
    verify_ct_echo 是**手工构造** `_save_step` 的收尾段；
    本脚本走**真正的用户链路**：
        选中第 N 步 → `_on_picked()` → `_review()`（真弹 ReviewDialog）
        → 在弹窗里点「双击」单选 → 点「保存这一步」→ 切走 → 切回 → 看详情。
    这样才可能抓到"我以为对、实际不对"的中间环节。

⚠ 弹窗是模态（`grab_set` + `-topmost`），测试里不点它自己的按钮，
   而是**直接调它的回调**（`_on_ct_change` / `on_save`），模拟真人点选的结果。
"""
from __future__ import annotations

import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from src import storage  # noqa: E402

storage.save_profile = lambda _p: True
storage.save_step_template = lambda _sid, _t: True

import gui  # noqa: E402
gui.storage.save_profile = lambda _p: True
gui.storage.save_step_template = lambda _sid, _t: True


def synth_frame(w=800, h=600):
    """造一张有纹理的帧，保证裁出的模板能用（不是纯色）。"""
    rng = np.random.default_rng(7)
    f = (rng.integers(0, 60, (h, w, 3))).astype("uint8")
    # 放一个明显的方块，供模板匹配
    f[260:320, 360:440] = np.array([230, 90, 60], dtype="uint8")
    return f


def find_dialog(app):
    for w in app.winfo_children():
        if isinstance(w, gui.ReviewDialog):
            return w
    return None


def main():
    c_pass = c_fail = 0

    def ok(cond, msg):
        nonlocal c_pass, c_fail
        if cond:
            c_pass += 1
            print("  [PASS] %s" % msg)
        else:
            c_fail += 1
            print("  [FAIL] %s" % msg)

    app = gui.App()
    app.minsize(1, 1)
    while len(app.profile["steps"]) < 3:
        app.profile["steps"].append(app._new_step_dict(len(app.profile["steps"]) + 1))
    # 三源旁证要读真实光标；测试环境跳过它（否则会告警刷屏，但不影响结论）
    app._cross_check_point = lambda *a, **k: None
    app._capture_frame = synth_frame()
    app._refresh_steps()

    # ══ 走真实链路：针对第 2 步（steps 下标 1，就是用户标过的那步）══
    i = 1
    app.lst.selection_clear(0, "end")
    app.lst.selection_row = None
    app.lst.selection_set(app._row_of_step(i))
    app._show_detail()
    print("① 选中第 %d 步，详情当前 = %r" %
          (i + 1, app.detail.get("1.0", "end").strip().splitlines()[-2]
           if app.detail.get("1.0", "end").strip() else ""))

    # 模拟用户在画面上点了识别点与操作点 → 进复核
    app._on_picked(i, (400, 290), (400, 290))
    app.update()
    dlg = find_dialog(app)
    ok(dlg is not None, "复核弹窗已弹出")
    if dlg is None:
        print("弹窗没出来，后续无法继续")
        app.destroy()
        return 1

    print("② 弹窗里初始选中值 = %r" % dlg._ct_var.get())
    # 模拟真人点「双击」单选
    dlg._ct_var.set("double")
    dlg._on_ct_change()
    app.update()
    ok(app.pending.get("click_type") == "double",
       "点『双击』后 pending.click_type = double")

    # 模拟真人点「保存这一步」
    app._save_step()
    app.update()
    print("③ 保存后 profile[steps][%d].action = %r"
          % (i, app.profile["steps"][i].get("action")))

    ok(picker_ct(app, i) == "double", "保存后数据源里 = double")

    # ══ 模拟"切走再切回"——用户口述的"回到第二步查看" ══
    app.lst.selection_clear(0, "end")
    app.lst.selection_set(app._row_of_step(0))
    app._show_detail()
    app.update()
    app.lst.selection_clear(0, "end")
    app.lst.selection_set(app._row_of_step(i))
    app._show_detail()
    app.update()

    txt = app.detail.get("1.0", "end")
    print("④ 切回第 %d 步，详情 >>>" % (i + 1))
    print("   " + txt.strip().replace("\n", "\n   "))
    ok("第 2 步" in txt, "详情指向第 2 步")
    ok("操作类型  双击" in txt, "切回后仍显示『双击』（用户的核心诉求）")

    # ══ 再模拟：切走切回时用下拉看（用户可能在右栏下拉处查看）══
    ok(app.click_type_var.get() == "双击",
       "右栏下拉切回后显示『双击』（当前 %r）" % app.click_type_var.get())

    app.destroy()
    print("\n通过 %d，失败 %d" % (c_pass, c_fail))
    return 0 if c_fail == 0 else 1


def picker_ct(app, i):
    from src import picker
    act = app.profile["steps"][i].get("action") or {}
    return picker.norm_click_type(act.get("click_type"))


if __name__ == "__main__":
    raise SystemExit(main())
