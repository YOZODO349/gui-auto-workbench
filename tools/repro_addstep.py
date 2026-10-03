# -*- coding: utf-8 -*-
"""最小复现：模拟「点【新增一步】按钮」这一下，把连锁异常全抓出来。

为什么需要它：Tkinter 的回调异常默认走 `report_callback_exception` —— 如果没接管，
它只往 stderr 打一行字然后**吞掉**；进程还活着，但界面可能已经被 `withdraw()` 藏了。
窗口用 pythonw 启动时连 stderr 都没有，于是现象就是「点一下，窗口没了」。

做法：monkeypatch `Tk.report_callback_exception` 把栈写进文件，再走一遍
`_add_step()` 的完整调用链（手工触发按钮的 invoke），最后检查窗口状态。
"""
from __future__ import annotations

import os
import sys
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

OUT = os.path.join(ROOT, "logs", "repro_addstep.txt")
os.makedirs(os.path.dirname(OUT), exist_ok=True)

lines = []


def say(s):
    lines.append(str(s))
    print(s)


def main() -> int:
    import tkinter as tk

    # 1) 接管回调异常 —— 模拟「加上 report_callback_exception 钩子」的行为
    real = tk.Tk.report_callback_exception

    def hook(self, exc, val, tb):
        say("!!! 回调里抛异常了 !!!")
        say("".join(traceback.format_exception(exc, val, tb)))
        try:
            real(self, exc, val, tb)
        except Exception:
            pass

    tk.Tk.report_callback_exception = hook

    import gui

    app = gui.App()
    app.update()                       # 真建出来
    say("窗口建好了。state=%s  步数=%d" % (app.state(), len(app.profile["steps"])))

    # 2) 全屏模式下探针链路（这是另一条可疑路径，但先只做只读观察）
    say("模式=%s  目标=%s" % (app.mode.get(), gui.storage.target_desc(app.profile)))

    # 3) 模拟点击【新增一步】—— 走真实的 invoke，而不是直接调方法，
    #    这样连按钮 command 包装层的问题也能一起暴露
    say("—— 模拟点击【新增一步】——")
    try:
        app.btn_add.invoke()
    except Exception:
        say("invoke 直接抛了：")
        say(traceback.format_exc())
    app.update()
    say("点完：state=%s  步数=%d  列表项=%d"
        % (app.state(), len(app.profile["steps"]), app.lst.size()))
    say("选中项=%s" % (app.lst.curselection(),))

    # 4) 再点一次，看第二次是否也正常
    say("—— 再点一次【新增一步】——")
    try:
        app.btn_add.invoke()
    except Exception:
        say(traceback.format_exc())
    app.update()
    say("点完：state=%s  步数=%d" % (app.state(), len(app.profile["steps"])))

    # 5) 窗口可见性判据
    try:
        say("winfo_viewable=%s  winfo_ismapped=%s"
            % (app.winfo_viewable(), app.winfo_ismapped()))
    except Exception as e:
        say("可见性探测失败：%r" % (e,))

    try:
        app.destroy()
    except Exception:
        pass
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
