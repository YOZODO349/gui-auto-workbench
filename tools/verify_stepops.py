# -*- coding: utf-8 -*-
"""验证新功能：插入/删除/重排按钮真的存在且能用。

用真实的 tkinter invoke() 走一遍，不碰真实数据（在临时 profile 上做）。
"""
from __future__ import annotations

import os
import sys
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src import storage                                     # noqa: E402

OUT = os.path.join(ROOT, "logs", "verify_stepops.txt")
lines = []

# 落盘拦截器要跨函数用（main 与 verify_exec_lock 都要碰），所以放模块级
_REAL_SAVE = None
_SAVED_N = []


def say(s):
    lines.append(str(s))
    print(s)


def main() -> int:
    import tkinter as tk

    def hook(self, exc, val, tb):
        say("!!! 回调异常 !!!")
        say("".join(traceback.format_exception(exc, val, tb)))

    tk.Tk.report_callback_exception = hook

    import gui
    from src import storage

    # ⚠ **把落盘掐掉**：本脚本要反复增删/移位步骤，而 gui 的 _after_struct_change
    #   会调 storage.save_profile 真写 config/profile.json —— 跑一次就把用户的
    #   真实配置清空了。这里换成内存版：profile 照改、界面照刷，就是不落盘。
    #   （这是本脚本"不碰真实数据"这句承诺真正兑现的地方。）
    global _REAL_SAVE
    _REAL_SAVE = storage.save_profile

    def _mem_save(profile):
        _SAVED_N.append(len(profile.get("steps") or []))
        return True

    storage.save_profile = _mem_save
    gui.storage.save_profile = _mem_save

    app = gui.App()
    app.update()

    # 清空步骤，从零开始
    app.profile["steps"] = []
    app._after_struct_change()
    app.update()

    def names():
        return [s["name"] for s in app.profile["steps"]]

    def ids():
        return [s["id"] for s in app.profile["steps"]]

    say("初始：%s" % names())

    # 1) 新增一步 ×3
    for _ in range(3):
        app.btn_add.invoke()
        app.update()
    say("点 3 次【新增一步】→ %s  ids=%s" % (names(), ids()))
    say("  按钮存在性：btn_ins=%s" % hasattr(app, "btn_ins"))

    # 2) 选中第 1 步，然后【插入一步】→ 应插到它后面
    app.lst.selection_clear(0, "end")
    app.lst.selection_set(0)
    app.update()
    kept_ids = ids()
    app.btn_ins.invoke()
    app.update()
    say("选中第1步后【插入一步】→ %s  ids=%s" % (names(), ids()))
    say("  新插入的 id=%s（应为 s04，因为原有 s01-s03）"
        % [i for i in ids() if i not in kept_ids])
    say("  插入后选中项=%s（应指向新插入那行，即下标 1）" % (app.lst.curselection(),))

    # 3) 删除中间一步 → 编号重排
    app.lst.selection_clear(0, "end")
    app.lst.selection_set(1)
    app.update()
    before_ids = ids()
    # 绕开确认框
    real_ask = gui.messagebox.askyesno
    gui.messagebox.askyesno = lambda *a, **k: True
    try:
        app.btn_del.invoke()
        app.update()
    finally:
        gui.messagebox.askyesno = real_ask
    say("删掉第2行 → %s  ids=%s" % (names(), ids()))
    say("  编号连续？%s" % (names() == ["第 %d 步" % i
                                      for i in range(1, len(names()) + 1)]))

    # 4) 上移/下移仍然正常，且重排
    app.lst.selection_clear(0, "end")
    app.lst.selection_set(0)
    app.update()
    order_before = ids()
    app.btn_down.invoke()
    app.update()
    say("第1行下移 → ids=%s（应与之前不同：%s）" % (ids(), ids() != order_before))
    say("  编号仍是连续的：%s" % names())

    # 5) 门控：新按钮在编辑模式可用
    say("编辑模式：btn_ins state=%s" % str(app.btn_ins["state"]))
    app.mode.set("edit")
    app._apply_mode_gate()
    app.update()
    say("强制 edit 后：btn_ins state=%s  btn_add state=%s  btn_del state=%s"
        % (str(app.btn_ins["state"]), str(app.btn_add["state"]),
           str(app.btn_del["state"])))

    # 6) 随时预演：标齐 1 步就能开执行模式吗？（用假数据判断门控，不真的点）
    say("--- 门控放宽验证 ---")
    ready_before = gui.storage.profile_ready(app.profile)
    say("当前（步骤都没标）执行模式：ready=%s why=%s" % ready_before)

    # 手工造一个"标齐"的假步骤
    import copy
    fake = copy.deepcopy(app.profile["steps"])
    if fake:
        fake[0]["anchor"] = {"point": [0.5, 0.5], "box": [0.4, 0.4, 0.2, 0.1],
                             "template": "s01.png"}
        fake[0]["action"] = {"point": [0.5, 0.5], "click": True, "same_as_anchor": False}
    app.profile["steps"] = fake
    ready_after = gui.storage.profile_ready(app.profile)
    say("只标齐 1 步（其余没标）→ ready=%s why=%s" % ready_after)

    # 7) **P1 执行互斥闸门**
    verify_exec_lock(app, gui)

    app.destroy()
    return 0


def verify_exec_lock(app, gui):
    """P1 闸门验证：连点【开始】只生效一次；提前 return 的路径要还锁。

    - 正向：`_start()` 连点两次 → 第二次被拦（放行次数 == 1，且闸门已锁）
    - 提前返回：数据不齐时 `_start()` → 必须**当场还锁**（否则永久锁死）
    - 反向对照（`--no-lock`）：把锁换成「永远抢得到」 → 第二次必然能进（放行 2 次）
    """
    say("")
    say("--- P1 执行互斥闸门 ---")
    no_lock = "--no-lock" in sys.argv

    if no_lock:
        class _AlwaysAcquire:
            @staticmethod
            def acquire(blocking=False):
                return True

            @staticmethod
            def locked():
                return False

            @staticmethod
            def release():
                pass

        app._exec_lock = _AlwaysAcquire()
        say("（反向对照：_exec_lock 已换成「永远抢得到」的假锁）")

    spawned = []
    real_spawn = app._spawn_worker
    # 屏蔽模态框：数据不齐/找不到窗口时 _start 会 showwarning，无人值守会挂住
    real_warn = gui.messagebox.showwarning
    real_ask = gui.messagebox.askyesno
    gui.messagebox.showwarning = lambda *a, **k: None
    gui.messagebox.askyesno = lambda *a, **k: True

    def _fake_spawn(dry):
        spawned.append(dry)          # 记一次「真的放行执行」

    app._spawn_worker = _fake_spawn

    # 造一个必然过校验的 profile（1 步、标齐、干跑）
    app.profile["steps"] = [{
        "id": "s01", "name": "第 1 步",
        "anchor": {"point": [0.5, 0.5], "box": [0.4, 0.4, 0.2, 0.1],
                   "template": "s01.png"},
        "action": {"point": [0.5, 0.5], "click": False, "same_as_anchor": False},
    }]
    # `dry_var` = "是否真实点击"，所以置 True 表示**走实跑分支**
    # （`_start` 里 `dry = not dry_var.get()`）。这里 worker 已被换掉，
    # 不会真动鼠标，只为验证闸门。⚠ 与 UI 默认值（干跑）无关，别照着改。
    app.dry_var.set(True)
    app.update()

    def _pump(ms=400):
        """空转事件循环若干毫秒 —— `_start` 用 after(160) 延迟启动 worker，
        必须真的把这个延迟跑完，否则计数为 0（假失败）。"""
        import time as _t
        t0 = _t.time()
        while (_t.time() - t0) * 1000 < ms:
            app.update()
            _t.sleep(0.01)

    spawned.clear()
    app._start()
    _pump(300)                       # 跨过 after(160)
    first_locked = app._exec_lock.locked()
    running_after_first = app._running
    app._start()                     # 第二次：应被拦
    _pump(300)
    n = len(spawned)

    say("连点【开始】两次 → 实际放行 %d 次（期望 1）" % n)
    say("  第一次后闸门已锁：%s（期望 True）" % first_locked)
    say("  第一次后 _running=%s（期望 True）" % running_after_first)
    if no_lock:
        say("  反向对照：期望放行 2 次（去掉锁后第二次必然能进）→ 实际 %d 次" % n)
    else:
        say("  结论：%s" % ("PASS" if (n == 1 and first_locked
                                     and running_after_first is True) else "FAIL"))

    # 提前返回必须还锁
    try:
        if app._exec_lock.locked():
            app._exec_lock.release()
    except Exception:
        pass
    app._running = False
    app.deiconify()                  # _start 成功时把自己 withdraw 了，这里恢复
    app.update()

    app.profile["steps"] = []        # 一步都没有 → profile_ready 必拦
    app.update()
    app._start()
    app.update()
    leaked = app._exec_lock.locked()
    say("数据不齐时点【开始】→ 闸门是否泄漏（期望 False）：%s" % leaked)
    if not no_lock:
        say("  结论：%s" % ("PASS（已当场还锁）" if not leaked
                            else "FAIL（锁被泄漏 → 永久锁死）"))

    app._spawn_worker = real_spawn
    gui.messagebox.showwarning = real_warn
    gui.messagebox.askyesno = real_ask
    storage.save_profile = _REAL_SAVE          # 还回真落盘（本进程内保持对称）
    gui.storage.save_profile = _REAL_SAVE
    say("落盘已被拦截：本次共 %d 次 save_profile 全部只在内存生效，"
        "config/profile.json **未被改动**" % len(_SAVED_N))


if __name__ == "__main__":
    try:
        rc = main()
    except Exception:
        lines.append("顶层异常：\n" + traceback.format_exc())
        rc = 2
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    sys.exit(rc)
