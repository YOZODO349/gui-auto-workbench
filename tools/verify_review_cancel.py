# -*- coding: utf-8 -*-
"""复核框"取消"验证：点下去必须**窗走 + 状态清**，三个入口一个都不能漏。

跑法：  .venv\\Scripts\\python.exe tools\\verify_review_cancel.py
        .venv\\Scripts\\python.exe tools\\verify_review_cancel.py --check-dead

## 这个门在防什么（用户实拍）

「复核这一步」右下角的【取消】**点了没反应**。病因不是按钮坏了：
按钮当时直接绑 `App._cancel_review`，而那个函数只清 `pending` / `_cap_ctx`、
**不关窗** —— 数据其实清干净了，是**窗口赖着不走**，用户只能判断"键坏了"。

所以判据是两条一起：**窗没了**（`winfo_exists()` 为假）**且**主窗那边收到取消。

★ 三个出口必须等价：按钮 / 标题栏 ×（`WM_DELETE_WINDOW`）/ Esc。
  少绑一个就是同一个 bug 换个入口再犯一次 —— 标题栏 × 若只 destroy 不通知，
  会留下脏 `pending`，下一次主窗采集就写进脚本的 `steps`。
"""
from __future__ import annotations

import hashlib
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("AWB_NO_EXEC", "1")

DEAD = "--check-dead" in sys.argv
PROFILE = os.path.join(ROOT, "config", "profile.json")
_n = [0]
_bad = [0]


def ok(title, cond, extra=""):
    _n[0] += 1
    if not cond:
        _bad[0] += 1
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", title,
                           ("　—　" + extra) if extra else ""))
    return bool(cond)


def sha(path):
    if not os.path.exists(path):
        return "-"
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()[:16]


def payload(frame):
    import numpy as np
    h, w = frame.shape[:2]
    return {
        "frame": frame, "frame_w": w, "frame_h": h,
        "box_abs": (300, 200, 160, 44),
        "template": np.zeros((44, 160, 3), dtype=np.uint8),
        "rec_pt_abs": (380, 222), "act_pt_abs": (380, 222),
        "rec_rel": (0.5, 0.5), "act_rel": None,
        "score": 1.0, "threshold": 0.82, "neg_score": 0.16,
        "same_as_anchor": True, "click_type": "single",
    }


def main():
    import numpy as np
    import gui

    print("=" * 68)
    print("复核框取消（%s）" % ("反向对照" if DEAD else "常规"))
    print("=" * 68)

    prof_before = sha(PROFILE)
    app = gui.App()
    app.update()
    frame = np.zeros((600, 800, 3), dtype=np.uint8)
    calls = []

    def make(dead=False):
        """造一个复核框。dead=True 时还原**旧写法**（按钮直接绑 on_cancel）。"""
        dlg = gui.ReviewDialog(app, payload(frame),
                               on_save=lambda: None,
                               on_resize=lambda f: None,
                               on_repick_rec=lambda: None,
                               on_repick_act=lambda: None,
                               on_cancel=lambda: calls.append("cancel"),
                               on_ct_change=lambda v: None)
        if dead:
            dlg.unbind("<Escape>")
            dlg.protocol("WM_DELETE_WINDOW", dlg.destroy)   # 只 destroy、不通知
            for w in dlg.winfo_children():
                for b in _btns(w):
                    if b.cget("text") == "取消":
                        b.configure(command=lambda: calls.append("cancel"))
        dlg.update()
        return dlg

    def _btns(w):
        import tkinter as tk
        out = []
        if isinstance(w, tk.Button):
            out.append(w)
        for ch in w.winfo_children():
            out.extend(_btns(ch))
        return out

    def find_cancel(dlg):
        for w in dlg.winfo_children():
            for b in _btns(w):
                if b.cget("text") == "取消":
                    return b
        return None

    # ---------- ① 主入口：点【取消】 ----------
    dlg = make(dead=DEAD)
    dlg.lift()
    app.update()
    ok("复核框已弹出（前提）", bool(dlg.winfo_exists()))
    btn = find_cancel(dlg)
    ok("找得到右下角那颗【取消】", btn is not None)
    calls.clear()
    btn.invoke()                     # 走**真实控件**，不是直接调函数
    app.update()
    gone = not bool(dlg.winfo_exists())
    if DEAD:
        # 跑**正向那条断言本身**：旧写法下它必须红（否则正向的绿是假的）
        ok("★★ 点【取消】→ **窗真的关了**（旧写法：窗赖着不走 → 本行必红）",
           gone, "gone=%s calls=%s" % (gone, calls))
    else:
        ok("★★ 点【取消】→ **窗真的关了**（用户实拍「点了没反应」就是这个病）",
           gone, "winfo_exists=%s" % (not gone))
        ok("★★ 且主窗收到了取消（数据清干净：pending / _cap_ctx）",
           calls == ["cancel"], "calls=%s" % calls)

    # ---------- ② 标题栏 × / Esc 必须同一条路 ----------
    for label, trigger in (("标题栏 ×", "wm"), ("Esc", "esc")):
        d2 = make()
        d2.update()
        calls.clear()
        if trigger == "wm":
            handler = d2.protocol("WM_DELETE_WINDOW")
            # Tk 返回的是注册过的命令名/回调
            if callable(handler):
                handler()
            else:
                d2.tk.call("eval", str(handler))
        else:
            d2.focus_force()
            app.update()
            d2.event_generate("<Escape>")
        app.update()
        gone2 = not bool(d2.winfo_exists())
        if DEAD:
            continue
        ok("★★ %s 也**关窗 + 通知**（少绑一个 = 同一 bug 换个入口再犯）" % label,
           gone2 and calls == ["cancel"], "gone=%s calls=%s" % (gone2, calls))

    # ---------- ③ 主窗那条路：_cancel_review 也得关窗 ----------
    if not DEAD:
        d3 = make()
        d3.update()
        app._close_reviews()
        app.update()
        ok("★ `App._close_reviews()`（主窗侧统一收口）能把复核框清干净",
           not bool(d3.winfo_exists()))
        ok("★★ 全程没写 `config/profile.json`（取消不改数据）",
           sha(PROFILE) == prof_before, "%s → %s" % (prof_before, sha(PROFILE)))
    else:
        d3 = make(dead=True)
        d3.update()
        app._close_reviews = lambda: None      # 旧写法里根本没有这个收口
        app._close_reviews()
        app.update()
        ok("★ `App._close_reviews()` 能把复核框清干净（旧写法：无此收口 → 本行必红）",
           not bool(d3.winfo_exists()))
        try:
            d3.destroy()
        except Exception:                                       # noqa: BLE001
            pass

    app.destroy()
    print("-" * 68)
    print("复核框取消：通过 %d，失败 %d" % (_n[0] - _bad[0], _bad[0]))
    if DEAD:
        allok = _bad[0] > 0
        print("[%s] 反向对照按预期失败（证明断言确实盯着这个病）"
              % ("PASS" if allok else "FAIL"))
        return 0 if allok else 1
    return 0 if _bad[0] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
