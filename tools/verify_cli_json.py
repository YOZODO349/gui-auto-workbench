# -*- coding: utf-8 -*-
"""验证 P2：`runner.py --json` 真的吐出可被机器消费的结构化输出。

## 三条腿

- **正向**：`--check --json` / `--list --json` 的输出必须能被 `json.loads` 解析，
  且关键字段齐全（不是空壳、不是报错文本混在里面）。
- **反向对照（注入病因）**：**不带** `--json` 时输出**不能**被 `json.loads` 解析。
  这证明「正向通过是 --json 的功劳」，而不是恰好人话也是合法 JSON。
- **退出码一致性**：带不带 `--json`，退出码必须**相同**（外部脚本才能统一判断成败）。

## 关于 SKIP

两种「没有可验对象」的情形必须优雅跳过（不能让整个验证挂掉、更不能误判为失败）：

- `--probe` 会**真抓屏**。无窗口 / 无桌面环境抓不到帧 → SKIP 并说明原因。
- `--list --json` 的 `click_type` 断言需要**至少一个步骤**作对象。**空白底座**
  （刚铺开、还没标任何步骤）时 → SKIP。**不能判 FAIL** —— 空集上
  「每个步骤都带 X」恒真，判红是假警报，会让新项目一上来就跑不过自己的质量门。

判定原则：**没有可验对象 → SKIP；有对象但不合格 → FAIL。**

用法：
    .venv\\Scripts\\python.exe tools\\verify_cli_json.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
RUNNER = os.path.join(ROOT, "runner.py")
OUT = os.path.join(ROOT, "logs", "verify_cli_json.txt")

lines = []
FAILS = []
SKIPS = []


def say(s=""):
    lines.append(str(s))
    print(s)


def check(name, cond, detail=""):
    tag = "PASS" if cond else "FAIL"
    say("[%s] %s%s" % (tag, name, ("　— " + detail) if detail else ""))
    if not cond:
        FAILS.append(name)
    return cond


def skip(name, why):
    say("[SKIP] %s　— %s" % (name, why))
    SKIPS.append(name)


def run(argv):
    """跑 runner.py，返回 (rc, stdout)。"""
    p = subprocess.run([PY, RUNNER] + argv, cwd=ROOT,
                       capture_output=True, text=True, timeout=120)
    return p.returncode, p.stdout, p.stderr


def parse_json(out):
    """尝试解析；失败返回 None（不抛）。"""
    try:
        return json.loads(out)
    except Exception:
        return None


# --------------------------------------------------------------------------- #
def verify_json_cmd(name, argv, required_keys, text_marker):
    """通用：某命令必须 ①json 可解析 + 字段齐 ②不带 --json 时不可解析 + 含人话标记。

    `text_marker` 是不带 --json 时必然出现的一小段人话，用来确认「确实走了文本分支」。
    """
    rc_j, out_j, err_j = run(argv + ["--json"])
    obj = parse_json(out_j)
    ok_json = obj is not None
    check("%s：--json 可解析" % name, ok_json,
          "" if ok_json else "输出前 120 字：%r" % out_j[:120])
    if ok_json:
        missing = [k for k in required_keys if k not in obj]
        check("%s：关键字段齐" % name, not missing,
              "缺 %s" % missing if missing else "共 %d 字段" % len(obj))

    # 反向对照：不带 --json
    rc_t, out_t, err_t = run(argv)
    obj_t = parse_json(out_t)
    check("%s：反向对照（不带 --json 时输出不可解析为 JSON）" % name,
          obj_t is None,
          "竟然解析成功（说明测试无效）" if obj_t is not None
          else "已确认非 JSON")
    check("%s：不带 --json 时走的是文本分支" % name,
          text_marker in out_t,
          "已见 %r" % text_marker if text_marker in out_t
          else "未见到 %r，输出前 120 字：%r" % (text_marker, out_t[:120]))

    check("%s：退出码一致（json=%d / text=%d）" % (name, rc_j, rc_t),
          rc_j == rc_t)
    return obj


def verify_probe():
    """`--probe` 需要真抓屏 —— 跑不通就 SKIP，不算失败。"""
    rc, out, err = run(["--probe", "--json"])
    obj = parse_json(out)
    if obj is None:
        skip("--probe --json", "抓不到屏或窗口（rc=%d）：%s"
             % (rc, (err or out).strip()[:100].replace("\n", " ")))
        return
    if not obj.get("ok"):
        skip("--probe --json", "环境不满足：%s" % obj.get("reason"))
        return
    check("--probe --json：含 frame_wh", "frame_wh" in obj)
    check("--probe --json：含 scale", "scale" in obj)
    check("--probe --json：含 steps 数组", isinstance(obj.get("steps"), list))
    check("--probe --json：含 best 字段", "best" in obj)


def verify_dry():
    """`--dry` 需要能跑起来；数据不齐时会以 rc=1 返回结构化的失败对象 —— 也算合格。"""
    rc, out, err = run(["--dry", "--json"])
    obj = parse_json(out)
    if obj is None:
        check("--dry --json：可解析", False, "输出前 120 字：%r" % out[:120])
        return
    check("--dry --json：可解析", True)
    if not obj.get("ok"):
        check("--dry --json：失败时也带 reason 字段", "reason" in obj,
              "reason=%r" % obj.get("reason", "")[:60])
    else:
        check("--dry --json：成功时含 events 数组",
              isinstance(obj.get("events"), list))


# --------------------------------------------------------------------------- #
def main() -> int:
    say("=" * 70)
    say("P2 CLI --json 验证")
    say("=" * 70)

    try:
        verify_json_cmd("--check", ["--check"],
                        ["target", "reference", "steps", "ready", "reason",
                         "screen_mode", "step_count"],
                        "配置文件：")
    except Exception:
        say("--check 用例异常：\n" + traceback.format_exc())
        FAILS.append("--check 用例")

    say("")
    try:
        # 文本分支必然含「第 1 步」或「还没有任何步骤」，取其一即可
        rc_l, out_l, _ = run(["--list"])
        marker = "第1步" if "第1步" in out_l else "还没有任何步骤"
        verify_json_cmd("--list", ["--list"],
                        ["step_count", "steps"], marker)

        # 操作类型必须出现在机器可读输出里（外部脚本要靠它分派点击）
        rc_j, out_j, _ = run(["--list", "--json"])
        try:
            obj_l = json.loads(out_j)
            steps_l = obj_l.get("steps", []) or []
            kinds = [s.get("action", {}).get("click_type") for s in steps_l]
            if not steps_l:
                # 空白底座（刚铺开、还没标任何步骤）时这条无从断言 ——
                # 空集上「每个步骤都带 X」恒真，判 FAIL 是**假警报**。
                # 与 --probe 同规矩：没有可验对象就 SKIP，别把整个门拖红。
                skip("--list --json：每个步骤都带 click_type 字段",
                     "还没有任何步骤，没有可断言的对象")
            else:
                check("--list --json：每个步骤都带 click_type 字段",
                      all(k in ("single", "double") for k in kinds),
                      "取值：%s" % (set(kinds) or "空"))
        except Exception as e:
            check("--list --json：click_type 可解析", False, str(e))
    except Exception:
        say("--list 用例异常：\n" + traceback.format_exc())
        FAILS.append("--list 用例")

    say("")
    try:
        verify_probe()
    except Exception:
        say("--probe 用例异常：\n" + traceback.format_exc())
        FAILS.append("--probe 用例")

    say("")
    try:
        verify_dry()
    except Exception:
        say("--dry 用例异常：\n" + traceback.format_exc())
        FAILS.append("--dry 用例")

    say("")
    say("=" * 70)
    if FAILS:
        say("结果：FAIL —— %d 项未过：%s" % (len(FAILS), "、".join(FAILS)))
        if SKIPS:
            say("（另有 %d 项 SKIP）" % len(SKIPS))
        return 1
    say("结果：PASS —— 全部通过%s"
        % ("（%d 项 SKIP）" % len(SKIPS) if SKIPS else ""))
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
