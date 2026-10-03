# -*- coding: utf-8 -*-
"""问题 2 复现：走 `_save_step` 的真实收尾路径，看"保存后返回查看"时详情框里是什么。

复现要点（对齐用户口述"我把第 1 步从单击改成双击，再返回查看第 1 步，还是单击"）：
  * 步骤列表在保存后会被 `_refresh_steps()` **清空重建** → `curselection()` 变空；
  * `selection_set()` **不触发** `<<ListboxSelect>>` → 详情不会自动刷新；
  * 老代码在 `selection_set` 后**没有**补一次 `_show_detail()` → 详情框停在
    `_refresh_steps` 内部那次 `_show_detail()` 留下的"未选中"空状态。
  * 用户眼里就是"我的改动丢了 / 没生效"。

反向对照：把 `_show_detail()` 那一行去掉，本脚本必须失败。
"""
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
    app = gui.App()
    app.minsize(1, 1)
    while len(app.profile["steps"]) < 3:
        app.profile["steps"].append(app._new_step_dict(len(app.profile["steps"]) + 1))
    # 全部补上 anchor/action，便于详情渲染
    for st in app.profile["steps"]:
        st["anchor"] = {"point": [0.5, 0.5], "box": [0, 0, 0.1, 0.1],
                        "method": "template", "template": st["frame"]}
        st["action"] = {"point": [0.5, 0.5], "radius": 6,
                        "same_as_anchor": False, "click": True,
                        "click_type": "single"}
    app._refresh_steps()

    # 目标：第 1 步（steps 下标 0）改成双击
    i = 0
    app.profile["steps"][i]["action"]["click_type"] = "double"

    # ==== 复刻 `_save_step` 的收尾段（真实路径） ====
    app._refresh_steps()          # 清空重建列表
    app.lst.selection_clear(0, "end")
    row = app._row_of_step(i)
    if row is not None:
        app.lst.selection_set(row)
        app.lst.see(row)
    app._show_detail()
    # ==============================================

    txt = app.detail.get("1.0", "end")
    app.destroy()

    ok_detail = ("第 1 步" in txt)
    ok_type = ("双击" in txt)
    print("详情框内容 >>>")
    print(txt.strip())
    print()
    print("详情含『第 1 步』? %s" % ok_detail)
    print("详情含『双击』  ? %s" % ok_type)
    if ok_detail and ok_type:
        print("\n[PASS] 返回查看时详情正确回显『第 1 步 / 双击』")
        return 0
    print("\n[FAIL] 详情未正确回显 —— 正是用户说的『返回查看还是单击』")
    return 1


if __name__ == "__main__":
    sys.exit(main())
