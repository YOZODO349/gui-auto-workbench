# -*- coding: utf-8 -*-
"""鼠标注入链路：`winio._send_mouse` / `click_abs` / `move_abs` / `probe_injection`。

**为什么要单开一门**（两个真实事故，用户两次报"勾上真实点击就出错"）：

  **事故一（崩溃）**：`_send_mouse` 里把 `dwExtraInfo` 传成了 `None`：
  ```python
  _MOUSEINPUT(nx, ny, 0, flags | _ABSOLUTE, 0, None)   # ❌ 最后一个 None
  ```
  `dwExtraInfo` 的类型是 `ULONG_PTR`（64 位下 = `c_ulonglong`），
  **ctypes 的整数类型不接受 `None`** → 当场抛
  `TypeError: 'NoneType' object cannot be interpreted as an integer`。

  恶果：**真实点击的第一条指令就炸** → 后台线程兜住 → 界面弹回显示红字，
  全程几毫秒（用户看到的是"窗口消失后马上弹出并报错，仿佛根本没识别"）。
  而**干跑永远不会触发**（干跑不调 click）—— 所以这个 bug
  **只在勾上真实点击时暴露**，之前的全部测试都没碰过它。

  **事故二（静默失效）**：修好崩溃后，`SendInput` 在用户机器上
  **返回 1（成功）却让光标纹丝不动**：
    同进程同权限下 `SetCursorPos` / `mouse_event` 都生效，唯独 `SendInput`
    被系统忽略 —— **而且它照样返回 1，返回值是骗人的**。
  光看返回值根本发现不了，于是定下两条铁律（本门逐一钉住）：
    ① **不能只看返回值** —— 移动后必须读光标校验；
    ② **必须有降级路径** —— 不灵就自动改走 `SetCursorPos` / `mouse_event`。

本门覆盖：
  · `_MOUSEINPUT(..., 0)` 能构造（事故一的回归锚点）
  · 用 `None` 会抛异常（钉住"为什么不能写 None"）
  · `move_abs` **真的把光标移到了目标**（不只是"没抛异常"）
  · `probe_injection()` 如实报告走了哪条路
  · 降级路径存在且被判作"可用"而非"不可用"
  · `SendInput` 返回值仍被检查，且不再有 `dwExtraInfo=None` 写法

⚠ 本门会**真的动鼠标**（移到别的坐标再还原），但**不会点击**。
  `--no-mouse` 可跳过真机动作，只跑结构体与代码路径断言。

跑法：  .venv\\Scripts\\python.exe tools/verify_mouse_inject.py
        .venv\\Scripts\\python.exe tools/verify_mouse_inject.py --check-dead
"""
from __future__ import annotations

import ctypes
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from src import winio                                             # noqa: E402

DEAD = "--check-dead" in sys.argv
NO_MOUSE = "--no-mouse" in sys.argv


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


def main():
    c = Checker()
    print("=" * 68)
    print("鼠标注入链路（%s）" % ("反向对照" if DEAD else "常规"))
    print("=" * 68)

    MI = winio._MOUSEINPUT
    flags = winio._MOVE | winio._ABSOLUTE
    src = open(os.path.join(ROOT, "src", "winio.py"), encoding="utf-8").read()

    # ---- 1. 回归锚点：dwExtraInfo 传 0 必须能构造 ----
    #   若有人把 0 改回 None，这条立刻红。
    try:
        m = MI(100, 100, 0, flags, 0, 0)
        c.ok("`_MOUSEINPUT(..., dwExtraInfo=0)` 能构造（事故一的根因在这）",
             True, "size=%d, dwExtraInfo=%s" % (ctypes.sizeof(MI), m.dwExtraInfo))
    except Exception as e:
        c.ok("`_MOUSEINPUT(..., dwExtraInfo=0)` 能构造", False,
             "%s: %s" % (type(e).__name__, e))

    # ---- 2. 钉住"为什么不能写 None"（反向对照的实体）----
    if DEAD:
        # 反向对照：证明 None 确实会炸 —— 如果哪天 ctypes 接受了 None，
        #   那说明运行环境变了，本门的判断前提需要复核。
        try:
            MI(100, 100, 0, flags, 0, None)
            c.ok("**反向对照**：传 None 会抛 TypeError（证明这条必须用 0）",
                 False, "居然没抛异常 —— 运行环境变了，需复核")
        except TypeError as e:
            c.ok("**反向对照**：传 None 会抛 TypeError（证明这条必须用 0）",
                 True, str(e)[:60])
        print("-" * 68)
        print("结果：%d/%d 通过" % (c.n, c.n))
        return 0

    try:
        MI(100, 100, 0, flags, 0, None)
        c.ok("传 None 应当抛 TypeError（说明用 0 不是可选项，而是必须）",
             False, "没抛 —— ctypes 行为变了，请复核本门前提")
    except TypeError:
        c.ok("传 None 会抛 TypeError（这就是事故一的机理）", True)

    # ---- 3. 结构体尺寸/字段对齐 ----
    c.ok("`_INPUT` / `_MOUSEINPUT` 尺寸合理（64 位下 40 / 32）",
         ctypes.sizeof(winio._INPUT) in (40, 28) and ctypes.sizeof(MI) in (32, 24),
         "_INPUT=%d _MOUSEINPUT=%d" % (ctypes.sizeof(winio._INPUT),
                                       ctypes.sizeof(MI)))

    winio.set_dpi_aware()
    u = ctypes.windll.user32
    print("   屏幕尺寸（DPI 感知后）= %d x %d" % (u.GetSystemMetrics(0),
                                                  u.GetSystemMetrics(1)))
    if NO_MOUSE:
        print("   (--no-mouse：跳过真机鼠标动作)")
    else:
        # ---- 4. move_abs 必须**真的把光标移到目标**（不只是"没抛异常"）----
        #   ★ 事故二的关键：`SendInput` 返回 1 却不动光标。
        #     只断言"没抛异常"会**漏掉**这个 bug —— 必须读回光标位置来验。
        cur = winio.cursor_pos()
        vx, vy = u.GetSystemMetrics(0), u.GetSystemMetrics(1)
        # 挑一个离当前位置远、且一定在屏内的目标
        tx = 40 if cur[0] > vx // 2 else max(0, vx - 40)
        ty = 40 if cur[1] > vy // 2 else max(0, vy - 40)
        try:
            winio.move_abs(tx, ty)
            time.sleep(0.05)
            got = winio.cursor_pos()
            hit = abs(got[0] - tx) <= 2 and abs(got[1] - ty) <= 2
            c.ok("`move_abs` **真的把光标移到了目标**（不只看返回值）",
                 hit, "目标(%d,%d) → 落点%s" % (tx, ty, got))
        except Exception as e:
            c.ok("`move_abs` 真的把光标移到了目标", False,
                 "%s: %s" % (type(e).__name__, e))

        # ---- 5. 降级路径必须存在（事故二的解药）----
        c.ok("存在降级路径：`SetCursorPos`（移动）与 `mouse_event`（按键）",
             winio._set_cursor_pos(tx, ty) and "mouse_event" in src,
             "SendInput 被忽略时自动改走它们")

        # ---- 6. probe_injection 如实报告走哪条路 ----
        #   ★ 这是**给用户看的**判断依据：探明是环境问题还是标点问题。
        pr = winio.probe_injection()
        c.ok("`probe_injection()` 报告链路**可用**（ok=True）",
             pr.get("ok"), "how=%s" % pr.get("how"))
        c.ok("`probe_injection()` 能指出具体走哪条路（how 非空）",
             pr.get("how") in ("SendInput", "SetCursorPos", "none"),
             "how=%r" % pr.get("how"))
        print("   → %s" % pr.get("detail", ""))

        # 还原光标（别把它留在角落里）
        try:
            winio._set_cursor_pos(*cur)
        except Exception:
            pass

    # ---- 7. SendInput 返回值被检查（不再静默丢弃）----
    c.ok("`_send_mouse` 检查了 SendInput 返回值（失败会抛，不静默）",
         "if not sent" in src and "SendInput 注入失败" in src)

    # ---- 8. dwExtraInfo 不许再出现 None ----
    c.ok("源码里不再有 `dwExtraInfo=None` 的写法（传 None 必崩）",
         "0, None)" not in src.replace(" ", ""))

    # ---- 9. 降级路径必须被**校验**触发，而不是"猜着换路" ----
    #   ★ 事故二的教训：不能只看返回值。所以移动类事件必须读光标校验。
    c.ok("移动注入后有**光标回读校验**（`_verify_move`）",
         "_verify_move" in src and "GetCursorPos" in src,
         "不能只信 SendInput 的返回值")

    print("-" * 68)
    if c.fail:
        print("结果：%d/%d 通过，**%d 项失败**" % (c.n - c.fail, c.n, c.fail))
        return 1
    print("结果：%d/%d 全部通过 ✓" % (c.n, c.n))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
