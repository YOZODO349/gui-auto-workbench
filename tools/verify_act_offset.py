# -*- coding: utf-8 -*-
"""操作点"出格"检查：`picker.act_point_offset`。

**这个功能的由来**（用户实报）：用户标了识别点和操作点，两点水平只差 3 px
看着"很接近"，但**垂直差了 48 px，而那个控件框总共才 33 px 高** ——
操作点整个落在框外面。于是真实点击时**点了空**：程序报告"完成"，
用户看到的却是"什么都没发生"。

★ 注意这里的分寸：**操作点与识别点不一致本身是允许的、甚至是常态**
  （识别点管"认出这是哪一页"，操作点管"要点的位置"）。
  所以本函数**不能也不该禁止两点不同**，只做"跑出框 → 可能点空"的提示。

跑法：  .venv\\Scripts\\python.exe tools/verify_act_offset.py
        .venv\\Scripts\\python.exe tools/verify_act_offset.py --check-dead
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from src import picker                                              # noqa: E402

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


# 用户的真实数据（2560×1600 下的像素值）
BOX = (342.0, 1110.0, 119.0, 33.0)      # 识别框 119×33
REC = (402.0, 1127.0)                   # 识别点：框正中
ACT_BAD = (399.0, 1079.0)               # 操作点：框上方 31 px —— 用户实际标的
ACT_GOOD = (402.0, 1125.0)              # 操作点：框内（有意微调，合理）


def main():
    c = Checker()
    print("=" * 68)
    print("操作点出格检查（%s）" % ("反向对照" if DEAD else "常规"))
    print("=" * 68)

    f = picker.act_point_offset
    if DEAD:
        # 注入病因：让检查"永远说在框内" → 下面那条断言必须失败
        f = lambda *a, **k: (True, "")                          # noqa: E731
        ok, why = f(REC, ACT_BAD, BOX)
        c.ok("**反向对照**：让检查恒判'在框内' → 用户那个点必须被放过去"
             "（证明断言真在盯它）", ok is True, "ok=%s why=%r" % (ok, why))
        # 但反过来也必须成立：真实实现不许把它判成"在框内"
        ok2, _ = picker.act_point_offset(REC, ACT_BAD, BOX)
        c.ok("**反向对照**：真实实现**必须**判它出格（否则这功能形同虚设）",
             ok2 is False, "ok=%s" % ok2)
        print("-" * 68)
        print("结果：%d/%d 通过" % (c.n, c.n))
        return 0

    # ---- 用户实况：操作点跑出框（正是"点了空"的那次）----
    ok, why = f(REC, ACT_BAD, BOX)
    c.ok("用户实况：操作点在框上方 31 px → 判**出格**（这正是点空的原因）",
         ok is False and "框外" in why, why)

    # ---- 操作点在框内：即便与识别点不同，也不该报警 ----
    ok, why = f(REC, ACT_GOOD, BOX)
    c.ok("操作点在框内（与识别点不同）→ **不报警**（两点不同是允许的）",
         ok is True and why == "", "ok=%s why=%r" % (ok, why))

    # ---- 两点完全重合：不许报警 ----
    ok, why = f(REC, REC, BOX)
    c.ok("识别点 == 操作点 → 不报警", ok is True, "ok=%s" % ok)

    # ---- 容差之内：略微出框仍放行（别过度报警，会变成狼来了）----
    #   框高 33 → 垂直容差 = 33*0.5 = 16.5 px（夹在 6~24 内）。出框 10px 在容差内。
    ok, _ = f(REC, (402.0, 1110.0 - 10.0), BOX)   # 距框顶 10px，在容差内
    c.ok("只出框 10 px（容差 16px 内）→ 不报警（避免过度打扰）",
         ok is True, "ok=%s" % ok)
    #   用户的真实案例距框顶 31px > 16px → 必须抓到（这条是回归锚点）
    ok, _ = f(REC, (402.0, 1110.0 - 31.0), BOX)
    c.ok("出框 31 px（超过容差 16px）→ 必须报警（用户的真实案例）",
         ok is False, "ok=%s" % ok)
    #   ★ 容差必须是"按框比例"而不是固定值：换个更高的框，同样的偏差应放行
    BOX_TALL = (342.0, 1110.0, 119.0, 200.0)      # 框高 200 → 容差 24（上限）
    ok, _ = f((402.0, 1210.0), (402.0, 1210.0 - 30.0), BOX_TALL)
    c.ok("容差**按框尺寸按比例**给：框高 200px 时，出框 30px 仍放行"
         "（固定 32px 容差会误报，按比例的不会）",
         ok is True, "ok=%s" % ok)

    # ---- 方向描述要对（别把"上方"写成"下方"）----
    _, why_up = f(REC, (402.0, 1000.0), BOX)
    _, why_down = f(REC, (402.0, 1250.0), BOX)
    c.ok("方向描述正确：上方 / 下方分得清",
         "上方" in why_up and "下方" in why_down,
         "上=%r 下=%r" % (why_up[:20], why_down[:20]))

    # ---- 水平出格也要抓到 ----
    ok, why = f(REC, (100.0, 1127.0), BOX)
    c.ok("水平方向跑出框 → 也能抓到（不只看垂直）",
         ok is False and "左侧" in why, why)

    # ---- 缺数据不许炸 ----
    c.ok("缺数据（None / 空框）→ 直接放行，不抛异常",
         f(None, ACT_BAD, BOX)[0] is True and f(REC, ACT_BAD, None)[0] is True)

    print("-" * 68)
    if c.fail:
        print("结果：%d/%d 通过，**%d 项失败**" % (c.n - c.fail, c.n, c.fail))
        return 1
    print("结果：%d/%d 全部通过 ✓" % (c.n, c.n))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
