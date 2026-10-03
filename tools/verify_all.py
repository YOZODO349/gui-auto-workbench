# -*- coding: utf-8 -*-
"""一键跑全部质量门。

为什么要有它：改一次代码要手动跑五个脚本、还要自己记得「哪些要带 --check-dead」，
跑漏一个就等于没验。收成一条命令，谁跑都一样。

用法：
    .venv\\Scripts\\python.exe tools/verify_all.py         # 跑全部
    .venv\\Scripts\\python.exe tools/verify_all.py -v      # 打印每个门的完整输出

设计要点：
  * 每个门都**既有正向也有反向对照**（--check-dead）—— 只跑正向是自欺：
      脚本可能因为"什么都没测"而恒绿。反向对照要求它**必须失败**，
      失败才算这个门是活的。
  * **落盘零污染**是硬约束：跑完必须 `config/profile.json` 一字未动。
      各脚本内部都做了内存版拦截，本脚本再兜一层"前后哈希比对"。
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = os.path.join(ROOT, ".venv", "Scripts", "python.exe")
if not os.path.exists(PY):
    PY = sys.executable

PROFILE = os.path.join(ROOT, "config", "profile.json")

# (名字, 参数, 期望退出码)
GATES = [
    ("自检（73 项，含反向对照）", ["tools/selftest.py"], 0),
    ("小窗布局（148 项）", ["tools/verify_smallwin.py"], 0),
    ("小窗布局·反向对照", ["tools/verify_smallwin.py", "--check-dead"], 0),
    ("步骤操作", ["tools/verify_stepops.py"], 0),
    ("异常黑匣子", ["tools/verify_crash.py"], 0),
    ("命令行 JSON 契约", ["tools/verify_cli_json.py"], 0),
    ("操作类型回显（问题 2）", ["tools/verify_ct_echo.py"], 0),
    ("操作类型回显·反向对照", ["tools/verify_ct_echo.py", "--check-dead"], 0),
    ("选中丢失后仍能改类型", ["tools/verify_ct_lostsel.py"], 0),
    ("选中丢失·反向对照", ["tools/verify_ct_lostsel.py", "--check-dead"], 0),
    ("GUI 默认值与语义（干跑/选中/类型条）", ["tools/verify_gui_defaults.py"], 0),
    ("GUI 默认值·反向对照", ["tools/verify_gui_defaults.py", "--check-dead"], 0),
    ("操作点出格检查（点空预警）", ["tools/verify_act_offset.py"], 0),
    ("操作点出格·反向对照", ["tools/verify_act_offset.py", "--check-dead"], 0),
    # ⚠ 这一门会真动一下鼠标（移到它当前所在位置，肉眼无感），但不会点击
    ("鼠标注入链路（真实点击）", ["tools/verify_mouse_inject.py"], 0),
    ("鼠标注入·反向对照", ["tools/verify_mouse_inject.py", "--check-dead"], 0),
    # 脚本仓库：全程只碰临时文件（config/_verify_repo.json），跑完自删
    ("脚本仓库（持久化/搜索/排序/增删改）", ["tools/verify_script_repo.py"], 0),
    ("脚本仓库·反向对照", ["tools/verify_script_repo.py", "--check-dead"], 0),
    # 换肤：只写 config/ui.json，profile.json 必须字节不变
    ("主题切换（换肤零遗留）", ["tools/verify_theme.py"], 0),
    ("主题切换·反向对照", ["tools/verify_theme.py", "--check-dead"], 0),
    # 取点遮罩：采帧清场 + 中央提示 + 快捷键开关
    ("取点遮罩（清场/提示/快捷键）", ["tools/verify_overlay_hud.py"], 0),
    ("取点遮罩·反向对照", ["tools/verify_overlay_hud.py", "--check-dead"], 0),
    # 复核框的"取消"必须窗走 + 状态清（三个入口等价）
    ("复核框取消（按钮/×/Esc）", ["tools/verify_review_cancel.py"], 0),
    ("复核框取消·反向对照", ["tools/verify_review_cancel.py", "--check-dead"], 0),
]


def sha(path):
    if not os.path.exists(path):
        return "-"
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()[:16]


def main():
    verbose = "-v" in sys.argv
    before = sha(PROFILE)
    print("=" * 68)
    print("一键质量门 —— 共 %d 道" % len(GATES))
    print("=" * 68)

    results = []
    for name, argv, want in GATES:
        print("\n▶ %s" % name)
        p = subprocess.run([PY] + argv, cwd=ROOT,
                           capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        out = (p.stdout or "") + (p.stderr or "")
        ok = (p.returncode == want)
        if verbose or not ok:
            print(out.rstrip())
        else:
            # 只打最后一行结论，保持输出清爽
            lines = [l for l in out.splitlines() if l.strip()]
            tail = lines[-1] if lines else "(无输出)"
            print("   %s" % tail)
        print("   → %s" % ("[PASS]" if ok else "[FAIL] 退出码 %d（期望 %d）"
                           % (p.returncode, want)))
        results.append((name, ok))

    after = sha(PROFILE)
    print("\n" + "=" * 68)
    npass = sum(1 for _, ok in results if ok)
    for name, ok in results:
        print("  %s  %s" % ("✓" if ok else "✗", name))
    print("-" * 68)
    print("质量门：%d/%d 通过" % (npass, len(results)))
    if before == after:
        print("落盘：config/profile.json 未被改动 ✓（%s）" % before)
    elif before == "-":
        print("落盘：config/profile.json **首次生成**（全新克隆时本来就没有，属初始化）"
              "→ %s" % after)
    else:
        print("落盘：⚠ config/profile.json 被改动了！%s → %s" % (before, after))
    print("=" * 68)

    if npass != len(results) or (before != after and before != "-"):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
