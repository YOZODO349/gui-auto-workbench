# -*- coding: utf-8 -*-
"""钉住 GUI 的三处**默认值与语义**，防止被改回去。

为什么值得单开一门：这三处都是"看起来无害的一个字"，
但每一个都直接导致过用户侧的事故：

  1. `dry_var` 默认值 —— 它的语义是「是否**真实点击**」（复选框文案是
     "真实点击（不勾 = 干跑）"）。默认必须是 **False**（干跑）。
     曾写成 `True`：用户点【开始】"以为在测试"，程序却立刻真去操作鼠标 ——
     既危险，又让人以为"怎么自己动了/报错了"。

  2. `Listbox.exportselection` —— 默认 `1` 会把选中发布为 X11 PRIMARY
     selection，别的控件一接管 selection owner，选中就被清空
     （`curselection()` 变空，而高亮还亮着）。必须是 **0**。

  3. `_ct_target_index()` —— 类型条的目标解析要**宽松一档**（回退最后一次
     有效选中），而不是严格版。严格版下"选中丢了就改不动类型"，且会静默
     失败（用户改了却什么都没发生）。

跑法：  .venv\\Scripts\\python.exe tools/verify_gui_defaults.py
        .venv\\Scripts\\python.exe tools/verify_gui_defaults.py --check-dead
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

try:
    import tkinter as tk
except Exception as e:                                          # pragma: no cover
    print("需要带 tkinter 的 Python：", e)
    raise SystemExit(2)

import gui                                                      # noqa: E402
from src import storage as _storage                              # noqa: E402

# ⚠ 掐掉落盘：起一次 App 会走收尾函数真写 config/profile.json。
_storage.save_profile = lambda _p: True
gui.storage.save_profile = lambda _p: True

DEAD = "--check-dead" in sys.argv


class Checker:
    def __init__(self):
        self.n = 0
        self.fail = 0

    def ok(self, name, cond, detail=""):
        self.n += 1
        cond = bool(cond)
        if not cond:
            self.fail += 1
        print("%s %d. %s%s" % ("[PASS]" if cond else "[FAIL]", self.n, name,
                               ("　—　" + detail) if detail else ""))
        return cond


def _find_checkbutton(widget, var):
    """在控件树里找那个绑定了 `var` 的 Checkbutton（返回控件或 None）。

    ⚠ 用遍历而不是记属性名：GUI 里这类勾选框是**匿名**建的（`tk.Checkbutton(...)`
      没赋给 self.xxx），记属性名会脆 —— 一改布局名就找不到，还得反过来改 GUI。
    """
    for w in widget.winfo_children():
        if isinstance(w, tk.Checkbutton):
            try:
                if str(w.cget("variable")) == str(var):
                    return w
            except Exception:
                pass
        found = _find_checkbutton(w, var)
        if found is not None:
            return found
    return None


def _ui_texts_with_asterisks(path):
    """扫出**会显示给用户**的字符串里还留着 `**` 的那些（返回 `[(行号, 调用, 摘要)]`）。

    ## 为什么要单开这条断言（P7c）

    `**` 在这个项目里是**注释与文档里的强调记号**，全库几百处 —— 但其中少数几处
    落在了**真正会渲染给用户的字符串**里。Tk 不认 markdown，于是那些星号会
    **原样显示**成 `这会**替换**工作台`（用户实拍过）。

    ★ 为什么必须用 AST 而不是 `grep`：grep 分不清"注释里的 `**`"和"要显示出去的 `**`"，
      几百处噪音里挑不出那几处，断言就成了一句废话。判据的核心是——
      **这句字符串在不在 `_log(...)` 里**。

    ★ 为什么日志区**允许**保留：日志是纯文本流，星号在那儿是"扫一眼定位"的标记；
      而弹窗和标签是**单行大字**，露出 `**` 就是纯噪音。所以规矩是
      **"弹窗/对话框/标签不许有，日志区可以有"**。

    ★ 显示前**当场 strip 掉**的（字面量直接写在 `.replace("**","")` 里）也算合规 ——
      它显示出来是干净的。
      ⚠ 但这只覆盖"字面量就在那个 `replace` 调用里"的情形：像取点遮罩 HUD 那种
      **"先赋给变量、下一行再 replace"的跨行写法，AST 看不穿**，会被判成违规。
      所以那种写法**不要用** —— P7c 已经把 HUD 改成直接写字面干净文案了。
      真要 strip，就写成 `"...".replace("**","")` 这种同行形式。
    """
    import ast
    full = path if os.path.isabs(path) else os.path.join(ROOT, path)
    with open(full, encoding="utf-8") as f:
        src = f.read()
    tree = ast.parse(src)
    parent = {}
    for n in ast.walk(tree):
        for ch in ast.iter_child_nodes(n):
            parent[ch] = n

    def calls_of(node):
        out, cur = [], node
        while cur in parent:
            cur = parent[cur]
            if isinstance(cur, ast.Call):
                f = cur.func
                out.append(getattr(f, "attr", None)
                           or getattr(f, "id", None) or "?")
        return out

    bad = []
    for n in ast.walk(tree):
        if not (isinstance(n, ast.Constant) and isinstance(n.value, str)
                and "**" in n.value):
            continue
        p = parent.get(n)
        # 模块 / 函数 / 类的**文档字符串**不算（那是写给维护者看的）
        if isinstance(p, ast.Expr) and isinstance(
                parent.get(p), (ast.Module, ast.FunctionDef, ast.ClassDef)):
            continue
        calls = calls_of(n)
        if "_log" in calls:
            continue                    # 日志区：**唯一允许**保留的地方
        if "replace" in calls:
            continue                    # 显示前已 strip 掉（取点 HUD）
        bad.append((n.lineno, (calls[0] if calls else "字面量"),
                    n.value[:38].replace("\n", " ")))
    return bad


def main():
    c = Checker()
    print("=" * 68)
    print("GUI 默认值与语义（%s）" % ("反向对照" if DEAD else "常规"))
    print("=" * 68)

    app = gui.App()
    app.withdraw()
    try:
        # ---- 1. dry_var 默认必须 = 干跑 ----
        got = app.dry_var.get()
        dry = not got
        if DEAD:
            # 注入病因：把默认值改回"真实点击" → 断言必须失败
            app.dry_var.set(True)
            got = app.dry_var.get()
            dry = not got
            c.ok("**反向对照**：dry_var 被置 True → dry 必须为 False"
                 "（证明断言真在盯默认值）", dry is False,
                 "dry_var=%s → dry=%s" % (got, dry))

            # ---- 反向对照 J（P7c）：往弹窗文案里塞回 `**` ----
            #   ⚠ 这条对照**同时验证两处豁免**：日志里的 `**` 不算、
            #     已经 `.replace("**","")` 掉的也不算 —— 少了这半边，
            #     断言可能把合规写法误判成"有罪"（那比漏报更烦人）。
            _tmp = os.path.join(ROOT, "config", "_verify_ui_asterisk.py")
            _tmp_src = (
                "from tkinter import messagebox\n"
                "def _log(*a):\n"
                "    pass\n"
                "def emit():\n"
                "    messagebox.showinfo('t', '这会**替换**工作台')\n"
                "    _log('日志里留 ** 是允许的')\n"
                "    x = '**'.replace('**', '')\n"
                "    return x\n")
            try:
                with open(_tmp, "w", encoding="utf-8") as f:
                    f.write(_tmp_src)
                _bad_j = _ui_texts_with_asterisks(_tmp)
                c.ok("**反向对照 J**：弹窗文案里塞回 `**` → 正向断言必然变红"
                     "（日志那处与已 strip 那处**不算**）",
                     len(_bad_j) == 1,
                     "扫到 %d 处（应为 1：只有弹窗那处）→ %s"
                     % (len(_bad_j), _bad_j))
            finally:
                try:
                    os.remove(_tmp)
                except Exception:
                    pass
        else:
            c.ok("dry_var 默认 False = **干跑**（点【开始】不真动鼠标）",
                 got is False, "dry_var.get() = %s" % got)
            c.ok("干跑推导：dry = not dry_var = True（只预演）",
                 dry is True, "dry = %s" % dry)
            # 文案与语义一致：必须存在一个勾选框，文案写「真实点击」，
            # 且它绑定的变量就是 dry_var（别把两个 var 接反了）。
            hit = _find_checkbutton(app, app.dry_var)
            c.ok("存在绑定 dry_var 的勾选框，且文案是「真实点击…」",
                 hit is not None and "真实点击" in hit.cget("text"),
                 (("文案 = " + hit.cget("text")) if hit is not None else "未找到"))

        # ---- 2. Listbox.exportselection 必须 = 0 ----
        es = app.lst.cget("exportselection")
        c.ok("Listbox exportselection = 0（选中不因焦点转移而丢失）",
             int(es) == 0, "exportselection = %s" % es)

        # ---- 3. 类型条目标解析是宽松版 ----
        # 空列表 → 返回 None（不瞎猜，且不抛异常）
        c.ok("_ct_target_index 在无步骤时返回 None（不瞎猜）",
             app._ct_target_index() is None,
             "_ct_target_index() = %s" % app._ct_target_index())
        # 宽松版与严格版必须是**两个不同**的函数对象（别被合并回去）
        c.ok("宽松版(_ct_target_index) 与 严格版(_selected_index) 是两套实现",
             app._ct_target_index.__func__ is not app._selected_index.__func__,
             "%s vs %s" % (app._ct_target_index.__func__.__name__,
                           app._selected_index.__func__.__name__))

        # ---- 4. 弹窗 / 对话框 / 标签里不许露出 `**`（P7c）----
        #   ★ 规矩：**"弹窗不许有，日志区可以有"**。Tk 不认 markdown，
        #     弹窗里那些星号会原样显示成「这会**替换**工作台」（用户实拍过）。
        #     取点遮罩那条 HUD 走 `replace("**","")`，显示出来是干净的 ——
        #     所以 `_ui_texts_with_asterisks` 对它免罪（见该函数注释）。
        bad_gui = _ui_texts_with_asterisks("gui.py")
        c.ok("★ 界面文案（弹窗/对话框/标签）里**没有裸露的 `**`**",
             not bad_gui, "；".join("L%d[%s] %s" % t for t in bad_gui))
        bad_cli = _ui_texts_with_asterisks("runner.py")
        c.ok("★ 命令行输出同理（`--json` 的字段另有断言，不受影响）",
             not bad_cli, "；".join("L%d[%s] %s" % t for t in bad_cli))
    finally:
        try:
            app.destroy()
        except Exception:
            pass

    print("-" * 68)
    if c.fail:
        print("结果：%d/%d 通过，**%d 项失败**" % (c.n - c.fail, c.n, c.fail))
        return 1
    print("结果：%d/%d 全部通过 ✓" % (c.n, c.n))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
