# -*- coding: utf-8 -*-
"""回归：「左栏选中丢了之后，右栏类型条仍然改得动、且改对了步」。

用户报："第二步我选了双击切回来还是单击"。程序内路径测不出，因为
测试总是"先 selection_set 再改"；真人操作里，**点右栏下拉/点空白/焦点转移
都可能让 Listbox 的 curselection 变空**，而列表高亮看起来还在。

一旦变空，老代码：
    · `_sync_click_type_box(None)` → 下拉置灰 + 显示"单击"（用户看到的"还是单击"）
    · `_on_click_type_pick` 拿到 None → **静默 return**（改动根本没落盘）

本脚本专测这条路径。反向对照（--check-dead）：把 `_ct_target_index`
换回 `_selected_index`，必须失败。
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from src import storage  # noqa: E402

storage.save_profile = lambda _p: True
storage.save_step_template = lambda _sid, _t: True

import gui  # noqa: E402
gui.storage.save_profile = lambda _p: True
gui.storage.save_step_template = lambda _sid, _t: True


def main():
    dead = "--check-dead" in sys.argv
    c_pass = c_fail = 0

    def ok(cond, msg):
        nonlocal c_pass, c_fail
        if cond:
            c_pass += 1
            print("  [PASS] %s" % msg)
        else:
            c_fail += 1
            print("  [FAIL] %s" % msg)

    if dead:
        print("=" * 62)
        print("反向对照：类型条改用严格版 _selected_index → 期望本脚本失败")
        print("=" * 62)

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

    if dead:
        # 注入病因：把类型条的目标解析换回严格版（丢失选中即失效）
        app._ct_target_index = app._selected_index
    app._refresh_steps()

    # ══ 场景：用户点选第 2 步（记住它），随后选中因故丢失 ══
    app.lst.selection_clear(0, "end")
    app.lst.selection_set(app._row_of_step(1))
    app._show_detail()
    app.update()
    ok(app._ct_target_index() == 1, "点选第 2 步后，类型条目标 = 第 2 步")

    # **模拟选中丢失**（真人操作时点下拉/空白/切换焦点都会这样）
    app.lst.selection_clear(0, "end")
    app.update()
    ok(app._selected_index() is None, "（前提）curselection 已空 —— 选中丢了")

    # 此时用户去右栏下拉选「双击」
    target_before = app._ct_target_index()
    ok(target_before == 1,
       "选中丢失后，类型条目标**仍是第 2 步**（当前 %r）" % target_before)

    app.click_type_var.set("双击")
    app._on_click_type_pick()
    app.update()
    ok(app.profile["steps"][1]["action"]["click_type"] == "double",
       "选中丢失后改下拉，第 2 步数据源 = double（真落盘了）")
    ok(app.profile["steps"][0]["action"]["click_type"] == "single",
       "没有误改到第 1 步")

    # ══ 反向场景：从没点过任何步骤时，不许瞎猜 ══
    app2 = gui.App()
    app2.minsize(1, 1)
    while len(app2.profile["steps"]) < 3:
        app2.profile["steps"].append(app2._new_step_dict(len(app2.profile["steps"]) + 1))
    app2._refresh_steps()
    ok(app2._ct_target_index() is None,
       "从没点过任何步骤时，类型条目标 = None（不瞎猜第 0 步）")
    app2.destroy()

    app.destroy()
    print("\n通过 %d，失败 %d" % (c_pass, c_fail))
    if dead:
        if c_fail == 0:
            print("\n[FAIL] 反向对照没测出问题 —— 脚本是假通过！")
            return 1
        print("\n[PASS] 反向对照按预期失败（证明断言盯着这条路径）")
        return 0
    return 0 if c_fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
