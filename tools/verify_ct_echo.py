# -*- coding: utf-8 -*-
"""回归验证：改完「操作类型」返回查看，详情必须回显最新值（问题 2）。

用户原话：「我把第 1 步的操作方式从"单击"改成"双击"后，再次返回查看第 1 步，
显示的仍然是"单击"。」

根因（实测确认，不是推理）：
    `_save_step()` 收尾调 `_refresh_steps()`，而它会：
        ① 清空并重建 Listbox → `curselection()` 变空；
        ② 在自己的末尾调一次 `_show_detail()` → 此时"无选中"，详情框写成
           "← 在左边选一步，或点【新增一步】开始。"。
    接着 `_save_step` 用 `selection_set(...)` 把选中恢复回去，
    但 **`selection_set` 不触发 `<<ListboxSelect>>`**，`_show_detail` 不会被
    自动唤起 —— 详情框就停在那个空的"未选中"状态。
    ⚠ 用户看到的是**空白详情**（不是"显示单击"）—— 他据此判断"我的修改没生效"。

修法：收尾处显式补一次 `self._show_detail()`。

反向对照（--check-dead）：把那一行去掉，本脚本必须失败。
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from src import storage  # noqa: E402

# 落盘拦截：本脚本反复改类型，绝不写真实 config/profile.json
storage.save_profile = lambda _p: True
storage.save_step_template = lambda _sid, _t: True

import gui  # noqa: E402
gui.storage.save_profile = lambda _p: True
gui.storage.save_step_template = lambda _sid, _t: True


def build_app():
    app = gui.App()
    app.minsize(1, 1)
    while len(app.profile["steps"]) < 3:
        app.profile["steps"].append(app._new_step_dict(len(app.profile["steps"]) + 1))
    for st in app.profile["steps"]:
        st["anchor"] = {"point": [0.5, 0.5], "box": [0, 0, 0.1, 0.1],
                        "method": "template", "template": st["frame"]}
        st["action"] = {"point": [0.5, 0.5], "radius": 6,
                        "same_as_anchor": False, "click": True,
                        "click_type": "single"}
    app._refresh_steps()
    return app


def tail_of_save_step(app, i, dead=False):
    """复刻 `_save_step()` 的收尾段（真实代码路径）。"""
    app._refresh_steps()                       # 清空重建列表
    app.lst.selection_clear(0, "end")
    row = app._row_of_step(i)
    if row is not None:
        app.lst.selection_set(row)
        app.lst.see(row)
    if not dead:
        app._show_detail()                     # ← 修复点：去掉它必失败
    # else: 反向对照：故意不刷新详情


def main():
    dead = "--check-dead" in sys.argv
    if dead:
        print("=" * 62)
        print("反向对照：去掉收尾的 _show_detail() → 期望本脚本失败")
        print("=" * 62)

    c_pass = c_fail = 0

    def ok(cond, msg):
        nonlocal c_pass, c_fail
        if cond:
            c_pass += 1
            print("  [PASS] %s" % msg)
        else:
            c_fail += 1
            print("  [FAIL] %s" % msg)

    app = build_app()

    # ---- 场景 1：把第 1 步 单击 → 双击，保存后返回查看 ----
    i = 0
    app.profile["steps"][i]["action"]["click_type"] = "double"
    tail_of_save_step(app, i, dead=dead)
    txt = app.detail.get("1.0", "end")
    ok("第 1 步" in txt, "详情回显的是第 1 步（不是别的步骤）")
    ok("操作类型  双击" in txt, "详情回显操作类型 = 双击（改动已体现）")

    # ---- 场景 2：再改回来 双击 → 单击，同样要回显 ----
    app.profile["steps"][i]["action"]["click_type"] = "single"
    tail_of_save_step(app, i, dead=dead)
    txt = app.detail.get("1.0", "end")
    ok("操作类型  单击" in txt, "再改回单击，详情同样回显最新值（可逆）")

    # ---- 场景 3：改中间那一步，详情必须指向那一步而非第 1 步 ----
    j = 1
    app.profile["steps"][j]["action"]["click_type"] = "double"
    tail_of_save_step(app, j, dead=dead)
    txt = app.detail.get("1.0", "end")
    ok("第 2 步" in txt, "改第 2 步后详情指向第 2 步（不是第 1 步）")
    ok("操作类型  双击" in txt, "第 2 步的类型回显为双击")

    # ---- 场景 4：走**真实下拉控件**路径（不是直接改字典）----
    # 下拉的 `set()` 不触发 <<ComboboxSelected>>，必须 event_generate 才走真路径。
    app.lst.selection_clear(0, "end")
    app.lst.selection_set(0)
    app._show_detail()
    app.click_type_var.set("双击")
    app.cmb_click_type.event_generate("<<ComboboxSelected>>")
    app.update()
    ok(app.profile["steps"][0]["action"]["click_type"] == "double",
       "经真实下拉选「双击」→ 数据源已写入 double")
    ok("操作类型  双击" in app.detail.get("1.0", "end"),
       "经真实下拉改完后，详情立刻回显双击")

    app.destroy()
    print("\n通过 %d，失败 %d" % (c_pass, c_fail))
    if dead:
        if c_fail == 0:
            print("\n[FAIL] 反向对照没测出问题 —— 本脚本是假通过！")
            return 1
        print("\n[PASS] 反向对照按预期失败（证明断言确实盯着这个 bug）")
        return 0
    return 0 if c_fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
