# -*- coding: utf-8 -*-
"""换肤验证：主题切换必须**真的落到每个控件上**，且不许碰工作台数据。

跑法：  .venv\\Scripts\\python.exe tools\\verify_theme.py
        .venv\\Scripts\\python.exe tools\\verify_theme.py --check-dead

## 这个门在防什么

换肤最容易的失败是"**换了一半**"：色号表换了，某些控件还留着旧主题的颜色
（典型是自绘画布、ttk 样式、Text 的 tag、以及**状态被记住的按钮底色**）。
只截一张图靠肉眼看，恰恰看不出这种"某一块颜色差一档"。

判据：切到深色后，遍历整棵控件树，**任何命中旧色表且映射后应当变色的选项都不许留着旧值**。
反向对照则证明这个检查真的能抓到"换了一半"（不调换肤函数时，必然抓出一堆遗留色）。

★ 铁律：换肤只写 `config/ui.json`，**`config/profile.json` 必须字节不变**。
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


def _read_ui():
    import json
    with open(UIFILE, encoding="utf-8") as f:
        return json.load(f)


def _write_ui(theme):
    import json
    with open(UIFILE, "w", encoding="utf-8") as f:
        json.dump({"theme": theme}, f, ensure_ascii=False, indent=2)


def stale_colors(root, bgmap, fgmap):
    """找出"本应变色却还留着旧值"的颜色选项。返回可读列表。"""
    import tkinter as tk
    import gui
    bad = []

    def hit(w, opt, mp, cur):
        new = mp.get(str(cur).lower())
        return new is not None and new.lower() != str(cur).lower()

    def walk(w):
        try:
            if isinstance(w, gui.PickOverlay):
                return
        except Exception:                                       # noqa: BLE001
            pass
        try:
            opts = set(w.keys())
        except Exception:                                       # noqa: BLE001
            opts = set()
        for opt, mp in [(o, bgmap) for o in gui._WALK_BG_OPTS] + \
                       [(o, fgmap) for o in gui._WALK_FG_OPTS]:
            if opt not in opts:
                continue
            try:
                cur = w.cget(opt)
            except Exception:                                   # noqa: BLE001
                continue
            if hit(w, opt, mp, cur):
                bad.append("%s.%s=%s" % (w.winfo_class(), opt, cur))
        if isinstance(w, tk.Canvas):
            for item in w.find_all():
                for opt, mp in (("fill", bgmap), ("outline", fgmap)):
                    try:
                        cur = w.itemcget(item, opt)
                    except Exception:                           # noqa: BLE001
                        continue
                    if hit(w, opt, mp, cur):
                        bad.append("Canvas.item.%s=%s" % (opt, cur))
        for ch in w.winfo_children():
            walk(ch)

    walk(root)
    return bad


def main():
    import gui
    from src import tokens, uiprefs

    print("=" * 68)
    print("换肤验证（%s）" % ("反向对照" if DEAD else "常规"))
    print("=" * 68)

    # ---- 1. 两张色表必须键齐（缺一个键 = 换了一半的界面） ----
    ok("★ LIGHT / DARK 的键集合完全一致（增删令牌必须两边一起加）",
       set(tokens.LIGHT) == set(tokens.DARK),
       "共 %d 键" % len(tokens.LIGHT))
    ok("★ 换肤用的两组角色键都在色表里",
       all(k in tokens.LIGHT for k in tokens.BG_KEYS + tokens.FG_KEYS))

    # ---- 2. apply() 改的是模块真值，_reload_tokens() 才把它搬进 gui ----
    before_gui = gui.BG
    tokens.apply("dark")
    ok("apply('dark') 后 tokens 真值已变",
       tokens.BG == tokens.DARK["BG"], tokens.BG)
    if DEAD:
        # 反向对照 A：只改 tokens、不 reload → gui 侧必然还是旧值
        ok("**反向对照 A**：不调 `_reload_tokens()` → gui 侧仍是旧主题色"
           "（证明「改了表界面没动」这个病真的会被这条断言抓住）",
           gui.BG == before_gui and gui.BG != tokens.BG,
           "gui.BG=%s" % gui.BG)
        gui._reload_tokens()
    else:
        gui._reload_tokens()
        ok("`_reload_tokens()` 后 gui.BG 跟着变了（这就是「改了表界面没动」的解药）",
           gui.BG == tokens.DARK["BG"], gui.BG)

    # ---- 3. 真机换肤：整棵树不许留旧色 ----
    tokens.apply("light")
    gui._reload_tokens()
    prof_before, ui_before = sha(PROFILE), sha(UIFILE)
    app = gui.App()
    app.geometry("1240x880")
    app.update()
    bgmap, fgmap = tokens.color_maps("light", "dark")

    if DEAD:
        # 反向对照：**只改色表、不通知界面**（等价于"换肤忘了落地"这个病）。
        # 下面这条就是正向那条断言本身 —— 它**必须失败**，否则说明正向的绿是假的。
        tokens.apply("dark")
        left = stale_colors(app, bgmap, fgmap)
        ok("★★ 切到深色后，整棵树**零遗留旧色**", not left,
           "%d 处遗留，例如 %s" % (len(left), left[:3]))
        tokens.apply("light")
    else:
        app._set_theme("dark")
        app.update()
        left = stale_colors(app, bgmap, fgmap)
        ok("★★ 切到深色后，整棵树**零遗留旧色**（含画布图形 / ttk / 按钮记在身上的身份色）",
           not left, "遗留 %d 处：%s" % (len(left), left[:5]))

        # 按钮"身份色"是记在控件属性上的，最容易漏（漏了就是"悬停跳回旧主题"）
        stale_kind = [w.winfo_class() for w in _all_btns(app)
                      if getattr(w, "_kind_bg", None) not in
                      (tokens.ACCENT, tokens.OK_BG, tokens.DANGER_BG,
                       tokens.PANEL2)]
        ok("★ 按钮的 `_kind_bg`（悬停回退要用的身份色）也换成新主题了",
           not stale_kind, str(stale_kind[:4]))

        # 日志 tag 是 Text 内部的，不在控件选项里
        ok("★ 日志 tag 颜色跟着换（`info` 现为 %s）"
           % app.log.tag_cget("info", "foreground"),
           str(app.log.tag_cget("info", "foreground")).lower()
           == tokens.FG.lower())

        # 设置栏：真实点单选能换肤
        win = gui.SettingsWindow(app)
        win.update()
        win.var_theme.set("light")
        win._pick("light")
        app.update()
        ok("★ 设置栏点【浅色】→ 真的切回浅色（走真实控件，不是直接调函数）",
           tokens.THEME_NAME == "light" and gui.PANEL.lower()
           == tokens.LIGHT["PANEL"].lower(),
           "THEME_NAME=%s" % tokens.THEME_NAME)
        left2 = stale_colors(app, dict((v, k) for k, v in bgmap.items()),
                             dict((v, k) for k, v in fgmap.items()))
        ok("★★ 切回来同样零遗留（可逆 —— 换肤不是单向的）",
           not left2, str(left2[:4]))
        win.close()

    # ---- 4. 落盘契约：偏好只进 ui.json，工作台数据一字不动 ----
    if not DEAD:
        app._set_theme("dark")
        ok("★ 换肤后 `config/ui.json` 里 theme=dark",
           os.path.exists(UIFILE) and _read_ui().get("theme") == "dark")
        ok("★★ 换肤**没动** `config/profile.json`（字节级；偏好不好混进工作台数据）",
           sha(PROFILE) == prof_before, "%s → %s" % (prof_before, sha(PROFILE)))
        app._set_theme("light")
        ok("★ 偏好能切回 light（可逆）", _read_ui().get("theme") == "light")

    app.destroy()
    # 还原用户偏好与文件原状（本门不产副作用）
    if os.path.exists(UIFILE):
        if ui_before == "-":
            os.remove(UIFILE)
        else:
            _write_ui(uiprefs.load().get("theme", "light"))

    print("-" * 68)
    print("换肤：通过 %d，失败 %d" % (_n[0] - _bad[0], _bad[0]))
    if DEAD:
        want_fail = True
        allok = (_bad[0] > 0)
        print("[%s] 反向对照按预期失败（证明断言确实盯着这件事）"
              % ("PASS" if allok else "FAIL"))
        return 0 if allok else 1
    return 0 if _bad[0] == 0 else 1


def _all_btns(w):
    import tkinter as tk
    out = []
    if isinstance(w, tk.Button):
        out.append(w)
    for ch in w.winfo_children():
        out.extend(_all_btns(ch))
    return out


if __name__ == "__main__":
    raise SystemExit(main())
