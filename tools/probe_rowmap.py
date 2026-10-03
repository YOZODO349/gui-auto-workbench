# -*- coding: utf-8 -*-
"""探针：找出能让 `_step_view`（行号→下标）与 steps 下标真正错位的操作序列。

结论导向：如果任何操作序列后 `_step_view` 都恒等 [0,1,2,...]，
那么 `selection_set(i)` 用下标当行号在**当前实现**下并不会选错行，
问题 2 的可见症状就只来自"`selection_set` 不触发 `_show_detail`"这一层。
本脚本用实测说话，不靠推理。
"""
import os
import sys
import json

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

os.environ.setdefault("AUTOWB_PROBE", "1")

import tkinter as tk  # noqa: E402
from src import storage  # noqa: E402

# 落盘拦截：探针绝不写真实 config
storage.save_profile = lambda _p: True
storage.save_step_template = lambda _sid, _t: True

import gui  # noqa: E402
gui.storage.save_profile = lambda _p: True
gui.storage.save_step_template = lambda _sid, _t: True


def fresh_app():
    app = gui.App()
    app.minsize(1, 1)
    app.update_idletasks()
    return app


def show(app, tag):
    print("  [%s] steps = %s" % (
        tag, [s["id"] for s in app.profile["steps"]]))
    print("        _step_view = %s" % (app._step_view,))
    print("        行序==下标? %s" % (app._step_view == list(range(len(app._step_view)))))


def main():
    app = fresh_app()
    # 保证有 3 步
    while len(app.profile["steps"]) < 3:
        app.profile["steps"].append(app._new_step_dict(len(app.profile["steps"]) + 1))
    app._refresh_steps()
    show(app, "初始")

    # --- 操作 A：选中中间步 → 下移 ---
    row = 1
    app.lst.selection_clear(0, "end")
    app.lst.selection_set(row)
    app._move(1)
    show(app, "中间步下移后")

    # --- 操作 B：选中第 1 行 → 删除 ---
    if len(app.profile["steps"]) >= 2:
        app.lst.selection_clear(0, "end")
        app.lst.selection_set(0)
        i = app._selected_index()
        app.profile["steps"].pop(i)
        app._after_struct_change(select_row=0)
        show(app, "删掉第 1 行后")

    # --- 操作 C：在中间插入一步 ---
    app.lst.selection_clear(0, "end")
    app.lst.selection_set(0)
    app._insert_step()
    show(app, "中间插入一步后")

    app.destroy()
    print("\n如果以上每一处都打印 行序==下标? True，说明 _step_view 恒等；")
    print("问题 2 的可见症状即来自 _save_step 里 selection_set 后未刷新详情。")


if __name__ == "__main__":
    main()
