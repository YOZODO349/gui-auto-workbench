# -*- coding: utf-8 -*-
"""命令行：**分层验**，从最无害的一层开始逐级放开。

    runner.py --list-windows   枚举可见窗口（认目标窗口用）
    runner.py --check          验配置能不能加载、数据齐不齐
    runner.py --list           列出所有步骤
    runner.py --probe          抓一帧，逐步骤打印置信度（最省事、最该先跑的一层）
    runner.py --dry            干跑：只识别不点击
    runner.py --live           真实点击（**会真的动鼠标**）
    runner.py --selftest       跑全套自测（48 项，含反向对照）
    runner.py --fixtures       导出夹具图给人看

**加 `--json`** 则输出机器可读的 JSON（给外部程序/计划任务/MCP 消费）：

    runner.py --check --json
    runner.py --probe --json
    runner.py --dry   --json     # 终态 + 全程事件流

不带 `--json` 时输出**一字不改**（老脚本/人眼照旧）。

用项目自带的解释器跑，别用系统默认的那个：
    .venv\\Scripts\\python.exe runner.py --check
"""
from __future__ import annotations

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src import geometry, matcher, picker, storage, winio         # noqa: E402
from src.engine import Runner                                     # noqa: E402
from src.host import create_live_host                             # noqa: E402


def emit(args, obj, text_fn=None):
    """统一输出口：`--json` 时打印 JSON，否则走 `text_fn()` 打人类可读文本。

    `text_fn` 为 None 时（纯 JSON 命令）在非 json 模式下也打一句人话兜底。
    """
    if getattr(args, "json", False):
        print(json.dumps(obj, ensure_ascii=False, indent=2))
    elif text_fn is not None:
        text_fn()
    return obj


def _prof_summary(prof):
    """profile 的公共摘要 —— 各 `--json` 命令共用，避免字段名漂移。"""
    ref = prof.get("reference", {})
    return {
        "target": storage.target_desc(prof),
        "screen_mode": bool(storage.is_screen_mode(prof)),
        "reference": {"w": ref.get("w", 0), "h": ref.get("h", 0)},
        "mode": prof.get("execution", {}).get("mode", "dry"),
        "step_count": len(prof.get("steps", [])),
    }


def cmd_list_windows(args):
    winio.require_win32()
    rows = winio.list_visible_windows()
    if getattr(args, "json", False):
        out = []
        for hwnd, title, cls in rows:
            _x, _y, w, h = winio.window_rect(hwnd)
            out.append({"hwnd": hwnd, "title": title, "class": cls,
                        "rect": [_x, _y, w, h], "size": [w, h]})
        emit(args, {"count": len(out), "windows": out})
        return 0
    if not rows:
        print("（没枚举到可见窗口）")
        return 0
    print("%-12s %-30s %-12s %s" % ("hwnd", "类名", "尺寸", "标题"))
    for hwnd, title, cls in rows:
        _x, _y, w, h = winio.window_rect(hwnd)
        print("%-12s %-30s %-12s %s" % (hwnd, cls[:30], "%dx%d" % (w, h), title))
    print()
    print("说明：桌面壳窗口（Progman / WorkerW）与任务栏已过滤，不在这张表里 ——")
    print("      那是防你误把「桌面」当成目标程序。要的是「整个屏幕」的话，走全屏模式：")
    print("      界面里选「整个屏幕」，或把 config/profile.json 的 window.target 改成 \"screen\"。")
    return 0


def cmd_check(args):
    prof = storage.load_profile()
    ready, why = storage.profile_ready(prof)

    if getattr(args, "json", False):
        steps = []
        for i, st in enumerate(prof["steps"], 1):
            steps.append({"idx": i, "name": st.get("name", ""),
                          "id": st.get("id"),
                          "missing": storage.step_missing(st)})
        obj = _prof_summary(prof)
        obj.update({
            "profile_path": storage.PROFILE_PATH,
            "ready": bool(ready),
            "reason": "" if ready else why,
            "steps": steps,
        })
        # 未就绪时退出码仍为 1（与文本模式一致），外部可据此判断
        emit(args, obj)
        return 0 if ready else 1

    print("配置文件：%s" % storage.PROFILE_PATH)
    print("目标：%s" % storage.target_desc(prof))
    if not storage.is_screen_mode(prof):
        print("  窗口关键字：%s（%s 匹配）" % (prof["window"]["title_keywords"],
                                              prof["window"]["match_mode"]))
    else:
        print("  （全屏模式：抓主屏、坐标就是屏幕坐标，不需要认窗口）")
    ref = prof["reference"]
    print("参考分辨率：%dx%d" % (ref.get("w", 0), ref.get("h", 0)))
    print("步骤数：%d" % len(prof["steps"]))
    print("默认执行方式：%s" % ("干跑（只识别不点击）"
                                if prof["execution"].get("mode", "dry") == "dry"
                                else "真实点击"))
    for i, st in enumerate(prof["steps"], 1):
        miss = storage.step_missing(st)
        print("  第%2d步 %-16s %s" % (i, st.get("name", ""),
                                      "✅ 齐" if not miss else "⚠ 缺 " + "、".join(miss)))
    print("-" * 50)
    print("执行模式：%s%s" % ("可用 ✓" if ready else "不可用 ✗ —— ", "" if ready else why))
    return 0 if ready else 1


def cmd_list(args):
    prof = storage.load_profile()
    if not prof["steps"]:
        if getattr(args, "json", False):
            emit(args, {"step_count": 0, "steps": []})
            return 0
        print("（还没有任何步骤）")
        return 0

    if getattr(args, "json", False):
        rows = []
        for i, st in enumerate(prof["steps"], 1):
            a = st.get("anchor") or {}
            act = st.get("action") or {}
            rows.append({
                "idx": i,
                "id": st.get("id"),
                "name": st.get("name", ""),
                "missing": storage.step_missing(st),
                "anchor": {"point": a.get("point"), "box": a.get("box")},
                "action": {"point": act.get("point"),
                           "same_as_anchor": bool(act.get("same_as_anchor")),
                           "click": bool(act.get("click", False)),
                           "click_type": picker.norm_click_type(
                               act.get("click_type")),
                           "radius": act.get("radius", 6)},
            })
        emit(args, {"step_count": len(rows), "steps": rows})
        return 0

    for i, st in enumerate(prof["steps"], 1):
        a = st.get("anchor") or {}
        act = st.get("action") or {}
        print("第%d步  %s  (id=%s)" % (i, st.get("name", ""), st.get("id")))
        if a.get("point"):
            print("    识别点 相对 (%.4f, %.4f)   框相对 %s"
                  % (a["point"][0], a["point"][1],
                     "x".join("%.4f" % v for v in (a.get("box") or [0, 0, 0, 0])[2:])))
        else:
            print("    识别点 —— 还没标")
        if act.get("same_as_anchor"):
            print("    操作点 同识别点")
        elif act.get("point"):
            print("    操作点 相对 (%.4f, %.4f)  半径 %dpx"
                  % (act["point"][0], act["point"][1], act.get("radius", 6)))
        else:
            print("    操作点 —— 还没标")
        _ct = picker.norm_click_type(act.get("click_type"))
        print("    操作类型 %s" % ("双击" if _ct == picker.CLICK_DOUBLE else "单击"))
    return 0


def _resolve(prof):
    """返回 `(kind, hwnd)`：("screen", None) / ("window", hwnd) / (None, None)。"""
    if storage.is_screen_mode(prof):
        return ("screen", None)
    winio.require_win32()
    w = prof["window"]
    hwnd = winio.find_window(w.get("title_keywords", []), w.get("match_mode", "exact"),
                             w.get("exclude", []))
    if not hwnd:
        print("找不到目标窗口 —— 关键字 %s。先跑 --list-windows 看看真实标题；"
              "也可以把目标改成「整个屏幕」模式（config/profile.json 的 "
              "window.target = \"screen\"）。" % w.get("title_keywords"))
    return (("window", hwnd) if hwnd else (None, None))


def cmd_probe(args):
    """抓一帧，逐步骤打印「置信度 + 读法」——**这一步能省掉大半猜测**。"""
    prof = storage.load_profile()
    kind, hwnd = _resolve(prof)
    if kind is None:
        if getattr(args, "json", False):
            emit(args, {"ok": False, "reason": "找不到目标窗口", "steps": []})
            return 1
        return 1
    templates = storage.load_all_templates(prof)
    import time
    if kind == "screen":
        time.sleep(0.2)
        frame = winio.capture_primary_screen()
    else:
        if not winio.activate(hwnd):
            if getattr(args, "json", False):
                emit(args, {"ok": False, "reason": "切不到前台", "steps": []})
                return 1
            print("切不到前台 —— 请手动点一下目标窗口，或先把它还原（别最小化）")
            return 1
        time.sleep(0.2)
        frame = winio.capture_client(hwnd)
    ok, why = winio.frame_is_usable(frame)
    if not ok:
        if getattr(args, "json", False):
            emit(args, {"ok": False, "reason": why, "steps": []})
            return 1
        print("抓帧失败：%s" % why)
        return 1
    cw, ch = frame.shape[1], frame.shape[0]
    ref = prof["reference"]
    scale = geometry.global_scale(cw, ch, ref.get("w", 0), ref.get("h", 0))
    scales = [s * scale for s in prof["match"]["scales"]]
    thr = float(prof["match"]["threshold"])
    as_json = getattr(args, "json", False)
    rows = []
    best = None

    if not as_json:
        print("目标：%s" % storage.target_desc(prof))
        print("帧尺寸：%dx%d　参考分辨率：%dx%d　全局缩放系数：%.4f"
              % (cw, ch, ref.get("w", 0), ref.get("h", 0), scale))
        print("-" * 60)

    for i, st in enumerate(prof["steps"], 1):
        a = st.get("anchor") or {}
        tmpl = templates.get(st.get("id"))
        if not a.get("point") or tmpl is None:
            if as_json:
                rows.append({"idx": i, "name": st.get("name", ""), "id": st.get("id"),
                             "score": None, "ok": False, "marked": False,
                             "explain": "", "note": "还没标"})
            else:
                print("第%d步 %-16s —— 还没标" % (i, st.get("name", "")))
            continue
        bx = geometry.rect_rel_to_abs(a["box"], cw, ch)
        px, py = geometry.point_rel_to_abs(a["point"], cw, ch)
        roi = geometry.search_window(px, py, bx[2], bx[3], cw, ch,
                                     float(prof["match"].get("search_margin_ratio", 0.6)))
        r = matcher.match_one(frame, tmpl, roi, a.get("method", "template"), scales, thr)
        if as_json:
            rows.append({"idx": i, "name": st.get("name", ""), "id": st.get("id"),
                         "score": round(float(r.score), 6), "ok": bool(r.ok),
                         "marked": True, "explain": matcher.explain_score(r.score),
                         "note": r.note or "", "method": a.get("method", "template")})
        else:
            print("第%d步 %-16s 得分 %.4f  %s"
                  % (i, st.get("name", ""), r.score, "命中" if r.ok else "未命中"))
            print("      ↳ %s" % matcher.explain_score(r.score))
            if r.note:
                print("      ↳ %s" % r.note)
        if best is None or r.score > best[1]:
            best = (i, r.score, r.ok)

    best_obj = None
    if best:
        best_obj = {"idx": best[0], "name": prof["steps"][best[0] - 1].get("name", ""),
                    "score": round(float(best[1]), 6), "ok": bool(best[2])}

    if as_json:
        emit(args, {
            "ok": True,
            "target": storage.target_desc(prof),
            "frame_wh": [cw, ch],
            "reference": {"w": ref.get("w", 0), "h": ref.get("h", 0)},
            "scale": round(float(scale), 6),
            "threshold": thr,
            "steps": rows,
            "best": best_obj,
        })
        return 0

    if best:
        print("-" * 60)
        print("判别结果：当前画面像「第 %d 步」（得分 %.4f）"
              % (best[0], best[1]))
    return 0


def _run(args, dry: bool):
    prof = storage.load_profile()
    ready, why = storage.profile_ready(prof)
    as_json = getattr(args, "json", False)
    if not ready:
        if as_json:
            emit(args, {"ok": False, "reason": "还不能跑：%s" % why,
                        "screen_mode": bool(storage.is_screen_mode(prof))})
            return 1
        print("还不能跑：%s" % why)
        return 1
    kind, hwnd = _resolve(prof)
    if kind is None:
        if as_json:
            emit(args, {"ok": False, "reason": "找不到目标窗口"})
            return 1
        return 1
    templates = storage.load_all_templates(prof)
    prof["execution"]["mode"] = "dry" if dry else "live"

    events = []                       # --json 下把事件流也收着

    def on_event(kind_, payload):
        if as_json:
            events.append({"kind": kind_, "payload": payload})
            return
        if kind_ == "log":
            text, tag = payload
            prefix = {"ok": "  ✓ ", "warn": "  ! ", "err": "  ✗ "}.get(tag, "    ")
            print(prefix + text)
        elif kind_ == "status":
            print("\n[%s]" % payload)
        elif kind_ == "step":
            print("    —— 进度 第 %d/%d 步" % (payload[0], payload[1]))

    if not as_json:
        print("目标：%s" % storage.target_desc(prof))
        if not dry:
            print("=" * 60)
            print("接下来会真的操作鼠标去点目标程序。3 秒后开始，Ctrl+C 可中断。")
            print("=" * 60)
            import time
            time.sleep(3)

    host_log = (lambda t, tag="info": None) if as_json else \
        (lambda t, tag="info": print("    " + t))
    host = create_live_host(prof, log=host_log)
    if host is None:
        if as_json:
            emit(args, {"ok": False, "reason": "认不到目标窗口 —— 它可能被关了，或标题变了",
                        "events": events})
            return 1
        print("认不到目标窗口 —— 它可能被关了，或标题变了")
        return 1
    runner = Runner(prof, host, templates=templates, on_event=on_event, dry=dry)
    res = runner.run()

    if as_json:
        # `_finish` 返回的就是结构化 dict（engine.py），原样带出 + 事件流
        out = dict(res)
        out["events"] = events
        emit(args, out)
        return 0 if res.get("ok") else 2

    print("-" * 60)
    print("终态：%s" % res["terminal"])
    if res.get("reason"):
        print("原因：%s" % res["reason"])
    return 0 if res.get("ok") else 2


def cmd_dry(args):
    return _run(args, dry=True)


def cmd_live(args):
    return _run(args, dry=False)


def cmd_selftest(_args):
    import subprocess
    return subprocess.call([sys.executable,
                            os.path.join(ROOT, "tools", "selftest.py")])


def cmd_verify_all(_args):
    """一键跑全部门（含反向对照）+ 落盘哈希校验。改完代码先跑这个。"""
    import subprocess
    return subprocess.call([sys.executable,
                            os.path.join(ROOT, "tools", "verify_all.py")])


def cmd_fixtures(_args):
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    from tools import make_fixtures
    d, n = make_fixtures.export()
    print("夹具已导出 %d 张 → %s" % (n, d))
    return 0


def main():
    winio.set_dpi_aware()      # 命令行也要先声明：不然尺寸/坐标与界面标定时不是一套像素
    ap = argparse.ArgumentParser(description="GUI 自动化工作台 · 命令行分层验")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--list-windows", action="store_true", help="枚举可见窗口")
    g.add_argument("--check", action="store_true", help="验配置与数据齐备")
    g.add_argument("--list", action="store_true", help="列出步骤")
    g.add_argument("--probe", action="store_true", help="抓一帧逐步骤报告置信度")
    g.add_argument("--dry", action="store_true", help="干跑（只识别不点击）")
    g.add_argument("--live", action="store_true", help="真实点击（会动鼠标）")
    g.add_argument("--selftest", action="store_true", help="跑全套自测")
    g.add_argument("--verify-all", action="store_true",
                   help="一键跑全部门（含反向对照）+ 落盘零污染校验")
    g.add_argument("--fixtures", action="store_true", help="导出夹具图")
    # **全局开关**：加在互斥组外，可与任一动作组合（如 `--check --json`）
    ap.add_argument("--json", action="store_true",
                    help="输出机器可读 JSON（不带则保持原文本输出，一字不改）")
    args = ap.parse_args()
    table = {
        "list_windows": cmd_list_windows, "check": cmd_check, "list": cmd_list,
        "probe": cmd_probe, "dry": cmd_dry, "live": cmd_live,
        "selftest": cmd_selftest, "fixtures": cmd_fixtures,
        "verify_all": cmd_verify_all,
    }
    for key, fn in table.items():
        if getattr(args, key, False):
            return fn(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
