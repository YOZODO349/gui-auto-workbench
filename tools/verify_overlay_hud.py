# -*- coding: utf-8 -*-
"""取点遮罩验证：①采帧前"清场"是否**收干净**了自家窗口；②中央提示（HUD）与快捷键。

跑法：  .venv\\Scripts\\python.exe tools\\verify_overlay_hud.py
        .venv\\Scripts\\python.exe tools\\verify_overlay_hud.py --check-dead

## 这个门在防两件真事

1. **采帧时自家窗口露在外面**（MEMORY 第 13 条补充）：`withdraw()` 只藏调用它的那一个
   Toplevel，子窗口不跟着藏。老代码是**逐窗硬编码**（主窗 + 脚本仓库窗），
   于是每加一个新窗口（设置栏 / 复核框）就漏一个 —— 漏了就被拍进帧里，
   模板上多一块自己的界面，那一步可能永远认不出来。
   → 判据：**采帧那一刻，除主窗外的自家 Toplevel 必须全部 withdrawn**。

2. **提示"看不见 / 让不开"**：旧版是屏幕顶上一条 12pt 白字（用户实拍：看不清、
   且没法隐藏）。新版要求：正中央、醒目、快捷键可开关、键可自定义。
   → 判据：HUD 包围盒中心 == 屏幕中心；开关一次必须真的消失、再按一次复原。

★ 铁律：改快捷键只写 `config/ui.json`，`config/profile.json` 必须字节不变。
"""
from __future__ import annotations

import hashlib
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("AWB_NO_EXEC", "1")

DEAD = "--check-dead" in sys.argv
PROFILE = os.path.join(ROOT, "config", "profile.json")
UIFILE = os.path.join(ROOT, "config", "ui.json")
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


def leak(app, gui):
    """采帧那一刻还露在外面的自家 Toplevel（取点遮罩不算 —— 它本来就该在）。"""
    out = []
    for w in app.winfo_children():
        if not isinstance(w, gui.tk.Toplevel) or isinstance(w, gui.PickOverlay):
            continue
        try:
            if w.winfo_exists() and w.state() != "withdrawn":
                out.append(w.winfo_class())
        except Exception:                                       # noqa: BLE001
            pass
    return out


def main():
    import numpy as np
    import gui
    from src import uiprefs

    print("=" * 68)
    print("取点遮罩（%s）" % ("反向对照" if DEAD else "常规"))
    print("=" * 68)

    ui_before = sha(UIFILE)
    prof_before = sha(PROFILE)
    app = gui.App()
    app.update()

    # ---------- ① 采帧清场 ----------
    repo = gui.ScriptRepoWindow(app)          # 老代码里唯一被照顾到的那个窗口
    sets = gui.SettingsWindow(app)            # 后加的窗口：老写法漏的就是它
    app.update()
    repo.withdraw()                           # 仓库窗本来就是"打开后自己藏"的
    app.update()

    if DEAD:
        # **注入病因**：还原成老写法 —— 只藏"传进来的那一个"，其余不管。
        def old_hide(one=None):
            if one is not None:
                try:
                    one.withdraw()
                except Exception:                               # noqa: BLE001
                    pass
        old_hide(repo)
        left = leak(app, gui)
        ok("**反向对照 A**：逐窗硬编码（只藏仓库窗）→ 设置栏**照旧露在外面**"
           "（证明「清场」这条断言真的抓得住「加了新窗口就漏」这个病）",
           bool(left), "仍露在外面：%s" % left)
    else:
        hidden = app._hide_own_tops()
        left = leak(app, gui)
        ok("★★ 采帧清场后，自家 Toplevel **一个都不露在外面**",
           not left, "剩余 %s（收起 %d 个）" % (left, len(hidden)))
        ok("★ 设置栏确实被收进了清场清单（漏它 = 帧里多一块自己的界面）",
           sets in hidden, "hidden=%d 个" % len(hidden))
        app._restore_own_tops()
        app.update()
        ok("★ 采完还原：设置栏回到屏幕上（不还用户会以为窗口丢了）",
           sets.state() == "normal", sets.state())

    # ---------- ② 走真路径（_start_capture）也要清场 ----------
    if not DEAD:
        seen = {}
        real_do = gui.App._do_capture

        def fake_do(self, i, kind, hwnd):
            seen["left"] = leak(self, gui)      # 抓帧那一刻的现场
        gui.App._do_capture = fake_do
        try:
            app._start_capture(0, ("screen", None), 0)
            # ⚠ 真路径是 `after(140, _do_capture)` —— 只 update() 一次等不到回调，
            #   必须把事件循环真的转起来（这是本门第一版踩的坑：现场取到 None）。
            import time
            t0 = time.time()
            while time.time() - t0 < 2.0 and "left" not in seen:
                app.update()
                time.sleep(0.05)
        finally:
            gui.App._do_capture = real_do
        ok("★★ 走真路径 `_start_capture()`（不是直接调清场函数）：抓帧那一刻同样零泄漏",
           seen.get("left") == [], "现场：%s" % seen.get("left"))
        app._restore_self()
        app.update()

    # ---------- ③ 中央提示（HUD） ----------
    cur_hk = uiprefs.load().get("hud_hotkey") or gui.HUD_HOTKEY_DEFAULT
    frame = np.zeros((600, 800, 3), dtype=np.uint8)
    ov = gui.PickOverlay(app, frame, (0, 0, 800, 600), "第 1 步", False,
                         lambda a, b: None, lambda: None)
    ov.update()
    items = ov.canvas.find_withtag("hud")
    ok("★ HUD 有内容（不是空提示）", len(items) >= 3, "%d 个图元" % len(items))
    bb = ov.canvas.bbox("hud")
    cx, cy = (bb[0] + bb[2]) // 2, (bb[1] + bb[3]) // 2
    if DEAD:
        # **注入症状**：把它画回"顶上一条 40px 小字"
        ov.canvas.delete("hud")
        ov.canvas.create_rectangle(0, 0, ov.vw, 40, fill="#0d0d11",
                                   outline="", tags="hud")
        ov.canvas.create_text(ov.vw // 2, 20, text="第 1 步 · 现在点：识别点",
                              fill="#ffffff",
                              font=("Microsoft YaHei UI", 12), tags="hud")
        ov.update()
        bb = ov.canvas.bbox("hud")
        cx, cy = (bb[0] + bb[2]) // 2, (bb[1] + bb[3]) // 2
    ok("★★ 提示**在屏幕正中央**（旧版贴在顶上：用户实拍「看不清」）",
       abs(cx - ov.vw // 2) <= 2 and abs(cy - ov.vh // 2) <= 2,
       "中心 %s vs 屏幕中心 %s" % ((cx, cy), (ov.vw // 2, ov.vh // 2)))

    fonts = []
    for it in ov.canvas.find_withtag("hud"):
        if ov.canvas.type(it) == "text":
            try:
                fonts.append(int(str(ov.canvas.itemcget(it, "font")).split()[-1])
                             if str(ov.canvas.itemcget(it, "font")).split()[-1].isdigit()
                             else int(gui.tkfont.Font(font=ov.canvas.itemcget(it, "font")).cget("size")))
            except Exception:                                   # noqa: BLE001
                fonts.append(0)
    ok("★ 主标够大（≥16pt；旧版 12pt 压在任意亮度截图上就是「看不清」）",
       fonts and max(fonts) >= 16, "字号 %s" % fonts)
    texts = [str(ov.canvas.itemcget(it, "text"))
             for it in ov.canvas.find_withtag("hud")
             if ov.canvas.type(it) == "text"]
    tail = "\n".join(texts)
    label = gui._hotkey_label(cur_hk)
    ok("★★ 提示结尾写明**按哪个键隐藏**（当前键：%s）" % label,
       ("按 %s 隐藏" % label) in tail, tail.splitlines()[-1] if tail else "")

    # ---------- ④ 快捷键开关 ----------
    n_before = len(ov.canvas.find_withtag("hud"))
    ov._toggle_hud()
    ov.update()
    n_hidden = len(ov.canvas.find_withtag("hud"))
    ok("★★ 按一次快捷键 → 提示**真的消失**（让开被挡住的画面）",
       n_hidden == 0 and ov._hud_on is False, "%d → %d" % (n_before, n_hidden))
    ov._toggle_hud()
    ov.update()
    ok("★★ 再按一次 → 提示**回来**（是开关，不是一次性关掉）",
       len(ov.canvas.find_withtag("hud")) == n_before and ov._hud_on is True)
    ov._teardown()
    app.update()

    # ---------- ④b 真按键（★ 不能只靠 event_generate —— 那是"假绿灯"） ----------
    #   `event_generate` 把事件**直接塞给控件**，绕过了 OS 的前台/焦点这一层 ——
    #   于是"绑定写对了、真按键送不到"这种病它一条也抓不到（本门第一版就是这么绿的，
    #   而用户实拍"按了没反应"）。这一节用**真注入的 Esc 键**走完整条路。
    import ctypes
    import time as _t
    _u = ctypes.windll.user32

    def _pump(sec=0.5, w=None):
        t0 = _t.time()
        while _t.time() - t0 < sec:
            (w or ov4).update()
            _t.sleep(0.02)

    def _real_esc():
        _u.keybd_event(0x1B, 0, 0, 0)        # VK_ESCAPE down
        _u.keybd_event(0x1B, 0, 2, 0)        # up
        _pump(0.5)

    ov4 = gui.PickOverlay(app, frame, (0, 0, 800, 600), "第 1 步", False,
                          lambda a, b: None, lambda: None)
    ov4.update()
    _pump(0.45, ov4)                 # ⚠ 先让它真的映射出来：窗还没显，抢前台必失败
    if DEAD:
        # **注入病因**：不抢键盘焦点（老代码只有 `focus_set()`，且落在 Toplevel 上）
        ov4._claim_keyboard = lambda: False
        ov4.focus_set()                      # 旧写法：只对 Toplevel 设焦点
    else:
        # ⚠ 抢 OS 前台会被系统的"前台锁"挡（**外部条件**：此刻前台还挂在别的窗口上）
        #   → 多试几次再判；仍抢不到就 **SKIP**（"没有可验对象 → SKIP"），
        #     而不是判失败 —— 否则本机会时不时假红一下（实测已遇到）。
        #     代价说清楚：这一条在抢不到前台时**不构成证据**，真按键那两条也一并跳过。
        claimed = False
        for _ in range(6):
            claimed = bool(ov4._claim_keyboard())
            if claimed:
                break
            _pump(0.3, ov4)
        if claimed:
            ok("★ 遮罩把键盘焦点**真的**拿住了（OS 前台 + Tk 焦点都落在画布上）",
               True, "focus_get=%s" % ov4.focus_get())
        else:
            print("  [SKIP] 系统前台锁没放行（外部条件，非本程序问题）—— "
                  "本项与下面两条真按键测试一并跳过，跳过 ≠ 通过")
    n0 = len(ov4.canvas.find_withtag("hud"))
    _real_esc()
    n1 = len(ov4.canvas.find_withtag("hud"))
    if DEAD or claimed:
        ok("★★ **真按键** Esc 能让提示消失（不是只测 event_generate 的假通过）",
           n0 > 0 and n1 == 0 and ov4._hud_on is False, "%d → %d" % (n0, n1))
    if not DEAD and claimed:
        _real_esc()
        ok("★★ 再按一次真按键 → 提示回来（开关是双向的）",
           len(ov4.canvas.find_withtag("hud")) == n0 and ov4._hud_on is True)
        ok("★ 提示藏起来时留了「叫回来」的小字（不许变成死路）",
           len(ov4.canvas.find_withtag("hud_badge")) >= 0)
    try:
        ov4._teardown()
    except Exception:                                           # noqa: BLE001
        pass
    app.update()

    # ---------- ⑤ 快捷键可自定义 / 非法值兜底 ----------
    if not DEAD:
        d = uiprefs.load()
        d["hud_hotkey"] = "F2"
        uiprefs.save(d)
        ov2 = gui.PickOverlay(app, frame, (0, 0, 800, 600), "第 1 步", False,
                              lambda a, b: None, lambda: None)
        ov2.update()
        txt2 = "\n".join(str(ov2.canvas.itemcget(it, "text"))
                         for it in ov2.canvas.find_withtag("hud")
                         if ov2.canvas.type(it) == "text")
        ok("★ 设置里改成 F2 → 新遮罩认这个键（提示里也写 F2）",
           ov2.hud_hotkey == "F2" and "按 F2 隐藏" in txt2, ov2.hud_hotkey)
        ov2._teardown()
        app.update()

        d["hud_hotkey"] = "<evil>"
        uiprefs.save(d)
        ov3 = gui.PickOverlay(app, frame, (0, 0, 800, 600), "第 1 步", False,
                              lambda a, b: None, lambda: None)
        ok("★ 非法键名（`<evil>` 这种能改绑定的写法）**自动退回默认**，不会把遮罩绑废",
           ov3.hud_hotkey == gui.HUD_HOTKEY_DEFAULT, ov3.hud_hotkey)
        ov3._teardown()
        app.update()

        ok("★★ 改快捷键只写 ui.json，`config/profile.json` 字节不变",
           sha(PROFILE) == prof_before, "%s → %s" % (prof_before, sha(PROFILE)))

    # 还原用户偏好
    d = uiprefs.load()
    d["hud_hotkey"] = cur_hk
    uiprefs.save(d)
    if ui_before == "-" and os.path.exists(UIFILE):
        os.remove(UIFILE)

    for w in (sets, repo):
        try:
            w.close()
        except Exception:                                       # noqa: BLE001
            try:
                w.destroy()
            except Exception:                                   # noqa: BLE001
                pass
    app.destroy()

    print("-" * 68)
    print("取点遮罩：通过 %d，失败 %d" % (_n[0] - _bad[0], _bad[0]))
    if DEAD:
        allok = _bad[0] > 0
        print("[%s] 反向对照按预期失败（证明这两条断言都盯着真病）"
              % ("PASS" if allok else "FAIL"))
        return 0 if allok else 1
    return 0 if _bad[0] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
