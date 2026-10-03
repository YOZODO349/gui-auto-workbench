# -*- coding: utf-8 -*-
"""自测：关键路径**全带反向对照**。

**反向对照是什么、为什么必须有**：一条"关掉它就必然失败"的对照 —— 否则你测到的可能是
**假通过**。例如要证明"全局缩放系数真在起作用"，就得把系数钉死回 1.0，看它是否**必然失配**。

覆盖：坐标换算 / 三向互斥 / 缩放与反向对照 / 抗背景变化 / 色调匹配四重证据
（含纯噪声与纯色块两个对抗样本）/ 状态机整链 / 入口判别反向对照 / 重试闸门不盲点 /
已离开原页立即停手 / 门控超时不盲跑 / 终态名不冒充成功 / 干跑零点击 / 数据齐备判定。

跑法：
    .venv\\Scripts\\python.exe tools/selftest.py
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src import geometry, matcher, picker, storage          # noqa: E402
from src.engine import Runner                                 # noqa: E402
from src.host import OfflineHost                              # noqa: E402
from tools import make_fixtures as fx                         # noqa: E402

BOX = (storage.DEFAULT_BOX_W, storage.DEFAULT_BOX_H)
REC_PT = (700, 82)              # 标题条上的识别点（三页文字不同 → 互斥性好）
STEP_KEYS = ("p1", "p2", "p3")


class Checker:
    def __init__(self):
        self.n = 0
        self.fail = 0
        self.rows = []
        self.metrics = []          # evals 式量化指标（只记录，不参与断言）

    def ok(self, name, cond, detail=""):
        self.n += 1
        cond = bool(cond)
        if not cond:
            self.fail += 1
        self.rows.append((cond, name, detail))
        print("%s %2d. %s%s" % ("[PASS]" if cond else "[FAIL]", self.n, name,
                                ("　—　" + detail) if detail else ""))
        return cond

    def metric(self, name, value, unit="", note=""):
        """记录一条**量化指标**（evals 式）。

        ⚠ 只记录、**不参与断言** —— 指标是给"阈值该定多少"提供依据的，
        不是通过/失败条件。判断仍然是上面的 `ok()`。
        """
        self.metrics.append({"name": name, "value": value,
                             "unit": unit, "note": note})
        return value

    def summary(self):
        if self.metrics:
            print("-" * 68)
            print("量化指标（evals · 仅供定阈值参考，不参与断言）：")
            for m in self.metrics:
                v = m["value"]
                vs = ("%.4f" % v) if isinstance(v, float) else str(v)
                print("   · %-34s %s%s%s" % (m["name"], vs, m["unit"],
                                             ("　（%s）" % m["note"]) if m["note"] else ""))
        print("-" * 68)
        if self.fail:
            print("结果：%d/%d 通过，**%d 项失败**" % (self.n - self.fail, self.n, self.fail))
            for cond, name, _d in self.rows:
                if not cond:
                    print("   ✗ %s" % name)
        else:
            print("结果：%d/%d 全部通过 ✓" % (self.n, self.n))
        return self.fail


# --------------------------------------------------------------------------- #
# 夹具：模拟"用户在编辑模式里点选"造出 profile
# --------------------------------------------------------------------------- #
def make_profile(data, ref=(fx.REF_W, fx.REF_H)):
    prof = storage.default_profile()
    prof["reference"] = {"w": ref[0], "h": ref[1]}
    prof["window"]["title_keywords"] = ["夹具目标程序"]
    prof["execution"].update({
        "ready_timeout_s": 2.0, "verify_timeout_s": 0.6, "poll_interval_s": 0.1,
        "return_home_wait_s": 1.0, "retry_max": 2, "recover_limit": 5,
    })
    templates = {}
    for i, key in enumerate(STEP_KEYS):
        sid = "s%02d" % (i + 1)
        frame = data["pages"][key]
        act_pt = fx.PAGE_SPECS[i]["btn_pos"]
        st = {"id": sid, "name": "第%d步" % (i + 1), "frame": "%s.png" % sid,
              "anchor": {}, "action": {}}
        # **就是 gui 保存时走的那条路**（同一份 picker 实现）
        picker.apply_to_step(st, frame, REC_PT, act_pt, BOX, False, 6)
        prof["steps"].append(st)
        templates[sid] = picker.cut_template(
            frame, picker.build_box(frame, REC_PT, BOX))
    return prof, templates


def run_engine(prof, templates, frames, dry=False, **host_kw):
    host = OfflineHost(frames, **host_kw)
    logs = []

    def on_event(kind, payload):
        if kind == "log":
            logs.append(payload[0])

    r = Runner(prof, host, templates=templates, on_event=on_event, dry=dry)
    res = r.run()
    return res, host, logs


def scores_on(prof, templates, frame):
    host = OfflineHost([frame])
    r = Runner(prof, host, templates=templates, dry=True)
    return r.page_scores(frame)


# --------------------------------------------------------------------------- #
def main():
    c = Checker()
    print("=" * 68)
    print("GUI 自动化工作台 · 自测")
    print("=" * 68)
    data = fx.build_all()
    prof, templates = make_profile(data)
    thr = float(prof["match"]["threshold"])

    # ---------------- 坐标换算 ----------------
    rel = geometry.point_abs_to_rel((960, 540), 1920, 1080)
    back = geometry.point_rel_to_abs(rel, 1920, 1080)
    c.ok("坐标换算往返（点）：960,540 → 相对值 → 回来",
         abs(back[0] - 960) <= 1 and abs(back[1] - 540) <= 1,
         "rel=(%.4f,%.4f) back=%s" % (rel[0], rel[1], back))

    rrel = geometry.rect_abs_to_rel((880, 60, 160, 44), 1920, 1080)
    rback = geometry.rect_rel_to_abs(rrel, 1920, 1080)
    c.ok("坐标换算往返（框）", all(abs(a - b) <= 1 for a, b in
                                  zip(rback, (880, 60, 160, 44))), str(rback))

    b = geometry.center_box(3, 3, 160, 44, 1920, 1080)
    c.ok("识别点贴角时，框被夹在画面内（不会裁出半截）",
         b[0] >= 0 and b[1] >= 0 and b[0] + b[2] <= 1920 and b[1] + b[3] <= 1080,
         str(b))

    roi = geometry.search_window(5, 5, 160, 44, 1920, 1080, 0.6)
    c.ok("搜索窗不出界（铁律：不全屏搜索，但不许越界）",
         geometry.rect_inside(roi, 1920, 1080), str(roi))

    # ---------------- 识别层 ----------------
    f1 = data["pages"]["p1"]
    st1 = prof["steps"][0]
    rec1 = geometry.point_rel_to_abs(st1["anchor"]["point"], 1920, 1080)
    box1 = geometry.rect_rel_to_abs(st1["anchor"]["box"], 1920, 1080)
    res1 = picker.spot_check(f1, templates["s01"], rec1, box1, prof)
    c.ok("模板在它自己那一页上命中（正向）",
         res1.ok and res1.score >= 0.99, "score=%.4f" % res1.score)

    # 三向互斥：喂哪一页，就只有那一页命中
    exclusive = True
    detail = []
    for i, key in enumerate(STEP_KEYS):
        sc = scores_on(prof, templates, data["pages"][key])
        hits = [s[0] for s in sc]
        want = [1 if j == i else 0 for j in range(len(STEP_KEYS))]
        good = all((h > 0) == (w > 0) for h, w in zip(hits, want))
        exclusive = exclusive and good
        detail.append("%s→命中%s" % (key, hits))
    c.ok("三向互斥：每一页只命中自己，别的页 0 命中", exclusive, "；".join(detail))

    # 缩放系数生效 + **反向对照**
    big = data["scaled"]["p1"]                      # 1920 → 2560（1.333 倍）
    sc_big = scores_on(prof, templates, big)
    c.ok("分辨率放大到 2560 宽仍命中（全局缩放系数生效）",
         sc_big[0][0] > 0, "第1步命中=%d 最高分=%.4f" % (sc_big[0][0], sc_big[0][1]))

    rec_big = geometry.point_rel_to_abs(st1["anchor"]["point"], 2560, 1440)
    box_big = geometry.rect_rel_to_abs(st1["anchor"]["box"], 2560, 1440)
    roi_big = picker.search_roi(prof, rec_big, box_big, 2560, 1440)
    fixed = matcher.match_template(big, templates["s01"], roi_big, [1.0, 1.0, 1.0], thr)
    c.ok("**反向对照**：把缩放系数钉死 1.0 → 必然失配（证明这条路径真在起作用）",
         not fixed.ok, "钉死后 score=%.4f" % fixed.score)

    # 抗背景变化
    all_bg = True
    bg_scores = []
    for k, fr in data["bg_variants"].items():
        r = matcher.match_template(
            fr, templates["s01"],
            picker.search_roi(prof, geometry.point_rel_to_abs(st1["anchor"]["point"], *fr.shape[1::-1]),
                              geometry.rect_rel_to_abs(st1["anchor"]["box"], *fr.shape[1::-1]),
                              fr.shape[1], fr.shape[0]), [1.0], thr)
        bg_scores.append("%s=%.3f" % (k, r.score))
        all_bg = all_bg and r.ok
    c.ok("抗背景变化：6 种背景色下全部命中", all_bg, "；".join(bg_scores))

    # ---------------- 色调匹配（背景变、字色不变）----------------
    tone_base = data["tone"]["base"]
    tb = geometry.center_box(210, 156, 200, 70, tone_base.shape[1], tone_base.shape[0])
    tone_tmpl = picker.cut_template(tone_base, tb)
    spec = matcher.ToneSpec(hue_lo=145, hue_hi=180, s_min=80)

    tone_prof = {"reference": {"w": tone_base.shape[1], "h": tone_base.shape[0]},
                 "match": {"scales": [1.0], "threshold": thr, "search_margin_ratio": 0.6}}
    tone_roi = geometry.search_window(210, 156, 200, 70, tone_base.shape[1],
                                      tone_base.shape[0], 0.6)

    ok_all = True
    tone_detail = []
    for k in ("base", "bg_a", "bg_b", "bg_c"):
        img = data["tone"][k]
        r = matcher.match_tone(img, tone_tmpl, tone_roi, spec, [1.0])
        tone_detail.append("%s=%s(score=%.3f iou=%.3f cov=%.3f)"
                           % (k, "命中" if r.ok else "失配", r.score,
                              r.evidence.get("shape_iou", 0), r.evidence.get("coverage", 0)))
        ok_all = ok_all and r.ok
    c.ok("色调匹配：背景换了 3 种，粉字仍全部命中（丢弃亮度 V）", ok_all,
         "；".join(tone_detail))

    rn = matcher.match_tone(data["noise"], tone_tmpl, tone_roi, spec, [1.0])
    c.ok("对抗样本①：纯噪声必须被拒绝", not rn.ok,
         "score=%.3f cov=%.3f iou=%.3f" % (rn.score, rn.evidence.get("coverage", 0),
                                           rn.evidence.get("shape_iou", 0)))

    rs = matcher.match_tone(data["solid"], tone_tmpl, tone_roi, spec, [1.0])
    c.ok("对抗样本②：同色纯色块必须被拒绝（coverage 上界 + 形状证据挡住）", not rs.ok,
         "score=%.3f cov=%.3f iou=%.3f" % (rs.score, rs.evidence.get("coverage", 0),
                                           rs.evidence.get("shape_iou", 0)))

    # ---------------- 帧可用性校验 ----------------
    from src import winio
    c.ok("黑帧被判为不可用（绝不拿错帧让用户去点）",
         not winio.frame_is_usable(
             __import__("numpy").zeros((100, 100, 3), dtype="uint8"))[0])
    c.ok("正常帧判为可用", winio.frame_is_usable(f1)[0])

    # ---------------- 状态机：整链闭环 ----------------
    seq = [data["pages"]["p1"], data["pages"]["p2"],
           data["pages"]["p3"], data["pages"]["p1"]]
    res, host, logs = run_engine(prof, templates, seq, dry=False)
    c.ok("整链：三页走一圈 → 收尾确认回到起点 → 完成",
         res["ok"] and res["terminal"] == "完成",
         "终态=%s 点击=%d 次" % (res["terminal"], len(host.clicks)))
    c.ok("整链的点击次数 = 步骤数（3 步 3 次，没有多余盲点）",
         len(host.clicks) == 3, "实际 %d 次" % len(host.clicks))
    c.ok("收尾校验有显形日志", any("收尾校验" in t for t in logs))

    # 反向对照：入口判别（喂第 2 页的图，必须判成第 2 页）
    h2 = OfflineHost([data["pages"]["p2"]])
    r2 = Runner(prof, h2, templates=templates, dry=True)
    c.ok("**反向对照**：喂第 2 页的图 → 判别结果必须是第 2 页",
         r2.detect(data["pages"]["p2"]) == 1)

    # ---------------- 闸门：点了没反应，不许盲点 ----------------
    still = [data["pages"]["p1"]] * 8
    res_f, host_f, logs_f = run_engine(prof, templates, still, dry=False)
    c.ok("重试闸门：点不动时只重试 retry_max+1 次，然后中止（不盲点）",
         len(host_f.clicks) == int(prof["execution"]["retry_max"]) + 1,
         "点击 %d 次（上限 %d）" % (len(host_f.clicks),
                                   int(prof["execution"]["retry_max"]) + 1))
    c.ok("终态名不冒充成功：中止时必须带「中止」，ok=False",
         (not res_f["ok"]) and "中止" in res_f["terminal"], res_f["terminal"])

    # ---------------- 闸门：已离开原页 → 立即停止重试 ----------------
    def _jump(x, y, n):
        return 2 if n == 1 else None            # 第 1 次点击后画面跑到第 3 页

    host_j = OfflineHost([data["pages"]["p1"], data["pages"]["p2"],
                          data["pages"]["p3"]], on_click=_jump)
    logs_j = []
    rj = Runner(prof, host_j, templates=templates,
                on_event=lambda k, p: logs_j.append(p[0]) if k == "log" else None,
                dry=False)
    rj.run()
    # ⚠ 只数**第 1 步**的点击。不能用 len(host.clicks) —— 那是全程计数，
    #   多步流程里别步也点了，会数多（早先就踩过这个坑：得到 4 不是 1）。
    # 也不能把文案写死成 "第1步：点击" —— 日志已改成 "第1步：单击/双击"，
    #   写死就会静默失效。所以只锚定"第1步"这个**步骤标识**，不锚定动作词。
    first_step_clicks = sum(1 for t in logs_j if t.startswith("第1步："))
    c.ok("已离开原页 → 立刻停止重试（第 1 步只点了一次，没在原地死磕）",
         first_step_clicks == 1, "第1步点击 %d 次" % first_step_clicks)
    c.ok("日志里有「停止重试，转恢复」的显形记录",
         any("停止重试" in t for t in logs_j))

    # ---------------- 门控：画面认不出 → 不许盲跑 ----------------
    res_n, host_n, logs_n = run_engine(prof, templates, [data["noise"]], dry=False)
    c.ok("起跑门控：画面不是任何已知页 → 零点击、直接中止",
         len(host_n.clicks) == 0 and (not res_n["ok"]) and "中止" in res_n["terminal"],
         "%s / 点击 %d 次" % (res_n["terminal"], len(host_n.clicks)))

    # ---------------- 干跑：只识别不点击 ----------------
    res_d, host_d, _ = run_engine(prof, templates, seq, dry=True)
    c.ok("干跑：识别得出来，且**一次鼠标都没点**",
         len(host_d.clicks) == 0 and "干跑" in res_d["terminal"],
         "%s / 点击 %d 次" % (res_d["terminal"], len(host_d.clicks)))

    # ---------------- 数据齐备判定 ----------------
    bad = {"id": "s99", "name": "半成品", "anchor": {}, "action": {}}
    miss = storage.step_missing(bad)
    c.ok("缺数据能点名：识别点/识别框/模板/操作点",
         set(miss) >= {"识别点", "识别框", "模板", "操作点"}, "缺 %s" % miss)

    p2 = storage.default_profile()
    p2["window"]["title_keywords"] = ["x"]
    p2["steps"] = [dict(prof["steps"][0])]
    ready, why = storage.profile_ready(p2)
    c.ok("数据齐了 → 执行模式放行", ready, why)

    p3 = storage.default_profile()
    p3["window"]["title_keywords"] = ["x"]
    ready3, why3 = storage.profile_ready(p3)
    c.ok("一个步骤都没有 → 执行模式必须拦住并说清原因",
         (not ready3) and ("还没有" in why3), why3)

    # ---------------- 全屏模式（不指定窗口）----------------
    from src import winio as winio_mod
    from src.host import ScreenHost

    c.ok("老配置（没有 target 字段）按「认窗口」处理，不会误判成全屏",
         not storage.is_screen_mode({"window": {}}))

    ps = storage.default_profile()
    ps["window"]["target"] = "screen"
    ps["window"]["title_keywords"] = []
    ps["steps"] = [dict(prof["steps"][0])]
    ready_s, why_s = storage.profile_ready(ps)
    c.ok("全屏模式：一个窗口关键字都不填，执行模式照样放行", ready_s, why_s)
    c.ok("全屏模式的目标描述可读", storage.target_desc(ps) == "整个屏幕（不指定窗口）",
         storage.target_desc(ps))

    calls = []
    orig_click = winio_mod.click_abs
    winio_mod.click_abs = lambda x, y, double=False, button="left": calls.append((x, y))
    try:
        sh = ScreenHost()
        sh.click(321, 654)
    finally:
        winio_mod.click_abs = orig_click
    c.ok("全屏模式：帧内坐标**原样**就是屏幕坐标（不做任何偏移换算，这是它比认窗口省事的地方）",
         calls == [(321, 654)], str(calls))

    sw, shh = winio_mod.primary_screen_size()
    c.ok("全屏模式：能取到主屏尺寸（取不到就抓不了屏）", sw > 0 and shh > 0,
         "%dx%d" % (sw, shh))

    full = winio_mod.capture_primary_screen()
    c.ok("全屏模式：真能抓到主屏，且**尺寸以帧为准**（不信 GetSystemMetrics，"
         "DPI 不一致时两者会差一截）",
         full is not None and full.shape[1] >= 800 and full.shape[0] >= 600,
         ("帧 %dx%d，GetSystemMetrics 说 %dx%d" % (full.shape[1], full.shape[0], sw, shh))
         if full is not None else "抓屏返回空")

    # 全屏模式下的整链：宿主换了，状态机不该有任何感觉。
    # 单步 + 弱验证：画面从 p1 变到 p2 = 点动了。（同图序列会被正确地判"点不动"）
    frames_s = [data["pages"]["p1"], data["pages"]["p2"], data["pages"]["p2"]]

    def _route_s(x, y, nn):
        return min(len(frames_s) - 1, nn)

    res_s, host_s, _ = run_engine(ps, {"s01": templates["s01"]},
                                  frames_s, dry=False, on_click=_route_s)
    c.ok("全屏模式也能整链跑通（宿主可替换，引擎无感）",
         res_s["ok"] and len(host_s.clicks) == 1,
         "%s / 点击 %d 次" % (res_s["terminal"], len(host_s.clicks)))

    # ---------------- 编号自动重排（增删/插入/移位后）----------------
    print()
    pr = storage.default_profile()
    pr["window"]["title_keywords"] = ["x"]
    pr["steps"] = [dict(prof["steps"][0]), dict(prof["steps"][1]),
                   dict(prof["steps"][2])]
    ids_before = [s["id"] for s in pr["steps"]]

    # 模拟"删掉中间那一步"，然后重排
    pr["steps"].pop(1)
    changed = storage.renumber_steps(pr)
    names = [s["name"] for s in pr["steps"]]
    c.ok("删掉中间一步 → 编号自动重排为连续的 第1步/第2步",
         names == ["第 1 步", "第 2 步"], "实际 %s（改了 %d 个）" % (names, changed))

    # **反向对照**：不调 renumber 时，编号必然还是旧的不连续值
    pr2 = storage.default_profile()
    pr2["window"]["title_keywords"] = ["x"]
    pr2["steps"] = [dict(prof["steps"][0]), dict(prof["steps"][1]),
                    dict(prof["steps"][2])]
    pr2["steps"].pop(1)
    raw_names = [s["name"] for s in pr2["steps"]]
    c.ok("**反向对照**：跳过重排 → 编号必然残留旧值（证明重排真在起作用）",
         raw_names != ["第 1 步", "第 2 步"], "不重排得到 %s" % raw_names)

    # **反向对照**：id 绝不能被重排动过 —— 动了就等于丢了帧/模板文件
    c.ok("**反向对照**：重排只改显示名，id 保持原样（id 是帧/模板的文件名主键）",
         [s["id"] for s in pr["steps"]] == [ids_before[0], ids_before[2]],
         "id %s → %s" % (ids_before, [s["id"] for s in pr["steps"]]))

    # 模拟"在中间插入一步"，编号同样要重排
    pr["steps"].insert(1, {"id": "s99", "name": "第 9 步", "anchor": {}, "action": {}})
    storage.renumber_steps(pr)
    c.ok("中间插入一步 → 编号自动重排（插入点之后的全部顺延）",
         [s["name"] for s in pr["steps"]] == ["第 1 步", "第 2 步", "第 3 步"],
         str([s["name"] for s in pr["steps"]]))

    # ---------------- 随时预演：不必闭环也能跑 ----------------
    print()
    # ① 单步流程：没法"回到起点"，但必须能跑完并算完成。
    #    帧序列要给"点得动"的画面：p1 → 点一下离开原页到 p2。
    #    （若给同图序列，弱验证会正确地判"画面没动" —— 那条路径由第 48 项专测。）
    p_one = storage.default_profile()
    p_one["window"]["title_keywords"] = ["夹具目标程序"]
    p_one["reference"] = {"w": fx.REF_W, "h": fx.REF_H}
    p_one["execution"].update({"ready_timeout_s": 2.0, "verify_timeout_s": 0.6,
                               "poll_interval_s": 0.1, "retry_max": 2})
    p_one["steps"] = [dict(prof["steps"][0])]
    frames_1 = [data["pages"]["p1"], data["pages"]["p2"],
                data["pages"]["p2"], data["pages"]["p2"]]

    def _route_1(x, y, n):
        return min(len(frames_1) - 1, n)

    res_1, host_1, logs_1 = run_engine(
        p_one, {"s01": templates["s01"]},
        frames_1, dry=False, on_click=_route_1)
    c.ok("**随时预演**：只有 1 步也能真跑，不要求闭环（旧规则会卡死在收尾校验）",
         res_1["ok"] and len(host_1.clicks) == 1,
         "%s / 点击 %d 次" % (res_1["terminal"], len(host_1.clicks)))
    c.ok("单步跑跳过了收尾校验，且日志里有显形说明",
         any("跳过收尾校验" in t for t in logs_1),
         "日志 %d 行" % len(logs_1))

    # ② **反向对照**：收尾回不到起点时，**不许判中止**，只警告 + 算完成。
    #
    # 这里为什么要绕一下：正常流程里"下一步验证"本身就要求回到 nxt，
    # 对紧凑循环的流程来说，最后一步验证通过 ≡ 已经回到起点 —— 收尾必然成立。
    # 所以"收尾失败"只在**验证宽松的边界**出现（比如最后一步设成「不点击」，
    # 于是跳过验证直接走收尾）。那就用这个边界来构造，才是真实可达的场景。
    p_two = storage.default_profile()
    p_two["window"]["title_keywords"] = ["夹具目标程序"]
    p_two["reference"] = {"w": fx.REF_W, "h": fx.REF_H}
    p_two["execution"].update({"ready_timeout_s": 2.0, "verify_timeout_s": 0.6,
                               "poll_interval_s": 0.1, "retry_max": 2,
                               "return_home_wait_s": 0.3})
    s_a = dict(prof["steps"][0])
    s_b = dict(prof["steps"][1])
    s_b["action"] = dict(s_b.get("action") or {})
    s_b["action"]["click"] = False          # 最后一步不点击 → 不走验证，直接进收尾
    p_two["steps"] = [s_a, s_b]
    # 起点 p1 → 点第1步落到 p2 → 第2步不点击，画面仍是 p2 → 收尾要 p1，但画面在 p2
    frames_2 = [data["pages"]["p1"], data["pages"]["p2"], data["pages"]["p2"],
                data["pages"]["p2"]]

    def _route(x, y, n):
        return min(len(frames_2) - 1, n)

    res_2, host_2, logs_2 = run_engine(
        p_two, {"s01": templates["s01"], "s02": templates["s02"]},
        frames_2, dry=False, on_click=_route)
    c.ok("**反向对照**：走完全程但收尾没回起点 → ok=True 且终态点明「未回到起点」"
         "（旧规则这里必判中止）",
         res_2["ok"] and "未回到起点" in res_2["terminal"],
         "%s / ok=%s / 点击 %d 次" % (res_2["terminal"], res_2["ok"], len(host_2.clicks)))
    c.ok("收尾没过时有显形警告，不静默",
         any("收尾校验没过" in t for t in logs_2), "日志 %d 行" % len(logs_2))

    # ③ 未标齐的步骤：能跑，且**点名跳过**（不静默、不触发熔断）
    p_mix = storage.default_profile()
    p_mix["window"]["title_keywords"] = ["夹具目标程序"]
    p_mix["reference"] = {"w": fx.REF_W, "h": fx.REF_H}
    p_mix["execution"].update({"ready_timeout_s": 2.0, "verify_timeout_s": 0.6,
                               "poll_interval_s": 0.1, "retry_max": 2})
    p_mix["steps"] = [dict(prof["steps"][0]),
                      {"id": "s98", "name": "第2步", "anchor": {}, "action": {}}]
    ready_m, why_m = storage.profile_ready(p_mix)
    c.ok("**随时预演**：有步骤没标齐，但至少一步齐全 → 执行模式照样放行",
         ready_m, why_m)
    # 帧序列：起点 p1 → 点一下就离开原页到 p2。
    # 关键：第 2 步未标齐 → 验证目标必须**顺延**（这里只有 1 步标齐 → 走弱验证），
    # 绝不能拿"第 2 步"去等（那个页面永远认不出来 → 旧实现会误报"点不动"）。
    frames_m = [data["pages"]["p1"], data["pages"]["p2"],
                data["pages"]["p2"], data["pages"]["p2"]]

    def _route_m(x, y, n):
        return min(len(frames_m) - 1, n)

    res_m, host_m, logs_m = run_engine(
        p_mix, {"s01": templates["s01"]},
        frames_m, dry=False, on_click=_route_m)
    c.ok("**反向对照**：未标齐的步骤被跳过后，验证目标**自动顺延**",
         res_m["ok"],
         "%s / 点击 %d 次" % (res_m["terminal"], len(host_m.clicks)))
    c.ok("未标齐的步骤被**跳过**且点名播报（不静默跳过）",
         any("还没标齐" in t or "跳过" in t for t in logs_m),
         "%s / 点击 %d 次" % (res_m["terminal"], len(host_m.clicks)))
    c.ok("跳过未标齐步骤不会误触发恢复熔断",
         "熔断" not in res_m["terminal"],
         "%s / 点击 %d 次" % (res_m["terminal"], len(host_m.clicks)))

    # **反向对照**：一步都没标齐 → 必须拦住（放宽不等于放行一切）
    p_none = storage.default_profile()
    p_none["window"]["title_keywords"] = ["x"]
    p_none["steps"] = [{"id": "s97", "name": "第1步", "anchor": {}, "action": {}}]
    ready_n, why_n = storage.profile_ready(p_none)
    c.ok("**反向对照**：一步都没标齐 → 执行模式必须拦住（放宽≠无条件放行）",
         (not ready_n) and ("一步都没标齐" in why_n), why_n)

    # 单步弱验证：画面纹丝不动 —— 新契约是「已执行、未验证」，**不是**「点不动」。
    #   ⚠ 这条断言换过口径，别再照旧版改回去：
    #     只有一步时，「点完画面没变」**不是失败证据**（双击开关 / 弹窗闪关 /
    #     动作本就不改画面，都可能）。旧实现在这里判「点不动」→ 重试 → 中止，
    #     用户标了 1 步点【开始】就直接出错。现在改为：如实放行 + 显形说明。
    #   但**不许退化成「假装成功」**：必须有 warn 显形，且终态不能自称干净跑完。
    p_dead = storage.default_profile()
    p_dead["window"]["title_keywords"] = ["夹具目标程序"]
    p_dead["reference"] = {"w": fx.REF_W, "h": fx.REF_H}
    p_dead["execution"].update({"ready_timeout_s": 2.0, "verify_timeout_s": 0.4,
                                "poll_interval_s": 0.1, "retry_max": 1})
    p_dead["steps"] = [dict(prof["steps"][0])]
    res_d, host_d, logs_d = run_engine(
        p_dead, {"s01": templates["s01"]},
        [data["pages"]["p1"]] * 6, dry=False)     # 画面永远停在原页 → 认不出"动了"
    # 单步流程：**输出完点击就算完成**，画面纹丝不动也不例外。
    #   ⚠ 本组断言换过两次口径，别再照旧版改回去：
    #     ① 最初：判「点不动」→ 用户标 1 步点【开始】直接中止（错得最狠）；
    #     ② 改成：判「跑完（有拍未验证）」→ ok=True 了，但终态仍带"有问题"的暗示，
    #        用户看到以为没跑好；
    #     ③ 现在：**判「完成」** —— 只标一步时，"点击已发出"本身就是这一步的
    #        全部内容，**验证不到 ≠ 没完成**，不该有任何负面措辞。
    _NEG = ("未验证", "失败", "中止", "点不动", "异常", "错误")
    c.ok("单步弱验证下画面纹丝不动 → 判「完成」（输出完点击即完成）",
         res_d["ok"] and res_d["terminal"] == "完成",
         "%s / ok=%s / 点击 %d 次" % (res_d["terminal"], res_d["ok"],
                                      len(host_d.clicks)))
    c.ok("单步完成的终态里**不许出现任何负面词**（不得带'没跑好'的暗示）",
         not any(w in res_d["terminal"] for w in _NEG),
         "%s" % res_d["terminal"])

    # 但**不许退化成"静默"**：日志里要留一句中性的说明（这拍没做页面验证），
    #   语气必须是"正常完成"而不是"放行了/未验证"。用 tag 判断更准：必须是 ok。
    c.ok("单步完成时日志有一句**中性**说明（不静默，且不是 warn 语气）",
         any("点击已发出" in t for t in logs_d),
         "日志 %d 行：%s" % (len(logs_d),
                             " / ".join(t[:28] for t in logs_d[-3:])))
    c.ok("单步弱验证**只点一次不重试**（重试=盲目多点，会误触发）",
         len(host_d.clicks) == 1,
         "点击 %d 次（期望 1）" % len(host_d.clicks))

    # **反向对照**：把 weak 分支改成"判失败"（退回旧病）→ 上面第一条必须失败。
    #   证明那条断言真在盯"单步算完成"这件事，不是碰巧成立。
    import src.engine as _eng
    _orig_cv = _eng.Runner._click_and_verify

    def _fake_cv(self, i, nxt, weak=False):
        r = _orig_cv(self, i, nxt, weak)
        if r == "ok_weak":
            self._weak_unverified = False
            r = "retry_fail"                # 病因：把"单步完成"退回判失败
        return r

    _eng.Runner._click_and_verify = _fake_cv
    try:
        res_f, host_f, _ = run_engine(
            p_dead, {"s01": templates["s01"]},
            [data["pages"]["p1"]] * 6, dry=False)
        c.ok("**反向对照**：把单步完成退回判失败 → 终态必然不是「完成」"
             "（证明断言真在盯此事）",
             not (res_f["ok"] and res_f["terminal"] == "完成"),
             "%s / ok=%s" % (res_f["terminal"], res_f["ok"]))
    finally:
        _eng.Runner._click_and_verify = _orig_cv
    res_g, _, _ = run_engine(
        p_dead, {"s01": templates["s01"]},
        [data["pages"]["p1"]] * 6, dry=False)
    c.ok("摘掉病因后立即恢复判「完成」（对照成立，不是环境的锅）",
         res_g["ok"] and res_g["terminal"] == "完成",
         "%s / ok=%s" % (res_g["terminal"], res_g["ok"]))

    # ================================================================== #
    # P3 识别层可注册表（为 OCR / 特征点留接口）
    # ================================================================== #
    c.ok("识别层注册表已就位，内建 template / tone 两种方法",
         set(matcher.registered_methods()) >= {"template", "tone"},
         "当前：%s" % matcher.registered_methods())

    # 等价性①：match_one(method="template") 与直调 match_template 逐字段相等
    frame_eq = data["pages"]["p1"]
    tmpl_eq = templates["s01"]
    roi_eq = (600, 40, 220, 90)
    r_via = matcher.match_one(frame_eq, tmpl_eq, roi_eq, "template", (1.0,), 0.8)
    r_dir = matcher.match_template(frame_eq, tmpl_eq, roi_eq, (1.0,), 0.8)
    c.ok("注册表**行为等价**：template 经分派与直调结果一致",
         (r_via.ok == r_dir.ok) and abs(r_via.score - r_dir.score) < 1e-12
         and r_via.box == r_dir.box,
         "分派 %.6f / 直调 %.6f" % (r_via.score, r_dir.score))

    # 等价性②：tone 同理（注意原生签名是 spec 而非 threshold，靠适配层抹平）
    r_via_t = matcher.match_one(frame_eq, tmpl_eq, roi_eq, "tone", (1.0,), 0.8)
    r_dir_t = matcher.match_tone(frame_eq, tmpl_eq, roi_eq, matcher.ToneSpec(), (1.0,))
    c.ok("注册表**行为等价**：tone 经分派与直调结果一致",
         (r_via_t.ok == r_dir_t.ok) and abs(r_via_t.score - r_dir_t.score) < 1e-12
         and r_via_t.box == r_dir_t.box,
         "分派 %.6f / 直调 %.6f" % (r_via_t.score, r_dir_t.score))

    # 未知方法：回退 template 且**显式点名**（静默回退比报错更危险）
    r_unk = matcher.match_one(frame_eq, tmpl_eq, roi_eq, "__不存在的方法__", (1.0,), 0.8)
    c.ok("未知方法走回退并**显式点名**（不静默）",
         ("未注册的识别方法" in r_unk.note) and ("__不存在的方法__" in r_unk.note),
         r_unk.note[:70])

    # **反向对照（注入病因）**：注册一个永远返回「不匹配」的假方法，
    # 断言 match_one 确实分派到它 —— 证明**真在查表**，而不是碰巧走了别的分支
    matcher.register("__dead__", lambda *a, **k: matcher.MatchResult(
        False, score=-9.0, method="__dead__", note="实验用 · 恒不匹配"))
    try:
        r_dead = matcher.match_one(frame_eq, tmpl_eq, roi_eq, "__dead__", (1.0,), 0.8)
        c.ok("**反向对照**：注册假方法 __dead__ 后，match_one 确实分派到它"
             "（证明真在查表）",
             (r_dead.method == "__dead__") and (r_dead.score == -9.0) and (not r_dead.ok),
             "method=%s score=%.1f" % (r_dead.method, r_dead.score))

        # 摘掉后必须立刻回退（证明注册表是**活的**，不是一次性快照）
        matcher.unregister("__dead__")
        r_after = matcher.match_one(frame_eq, tmpl_eq, roi_eq, "__dead__", (1.0,), 0.8)
        c.ok("摘掉假方法后立即回退 template（注册表是活的）",
             r_after.method == "template" and "__dead__" not in matcher.registered_methods(),
             "回退后 method=%s" % r_after.method)
    finally:
        matcher.unregister("__dead__")          # 无论如何别把假方法留在全局表里

    # 重复注册必须报错（防静默覆盖）
    try:
        matcher.register("template", r_dir)     # 已存在，应抛
        dup_blocked = False
    except ValueError:
        dup_blocked = True
    c.ok("重复注册同名方法会报错（防静默覆盖）", dup_blocked)

    # ================================================================== #
    # P4 三源旁证
    # ================================================================== #
    # 一致：三源落点相同
    d_ok = geometry.cross_check_point(
        100, 50, client_rect=(0, 0, 800, 600), frame_wh=(800, 600),
        ref_wh=(800, 600), cursor_xy=(100, 50))
    c.ok("三源旁证：三点重合 → 判一致",
         d_ok["consistent"] and d_ok["spread"] == 0,
         d_ok["detail"])

    # **反向对照（注入病因）**：客户区偏移写错 20px → 必须判不一致
    d_bad = geometry.cross_check_point(
        100, 50, client_rect=(20, 20, 800, 600), frame_wh=(800, 600),
        ref_wh=(800, 600), cursor_xy=(100, 50))
    c.ok("**反向对照**：客户区偏移写错 20px → 必须判不一致（旁证真在算）",
         (not d_bad["consistent"]) and d_bad["spread"] == 20,
         d_bad["detail"][:70])

    # 源不足时优雅跳过，不误报
    d_one = geometry.cross_check_point(100, 50, cursor_xy=(100, 50))
    c.ok("三源旁证：可用源不足 2 个时跳过判定（不误报）",
         d_one["consistent"] and "跳过" in d_one["detail"],
         d_one["detail"])

    # ================================================================== #
    # P5 操作类型：单击 / 双击
    # ================================================================== #
    # --- ① 类型收敛：脏值一律当"单击"（这就是向后兼容的保证） ---
    c.ok("操作类型收敛：None / 空 / 垃圾值 一律当「单击」",
         picker.norm_click_type(None) == "single"
         and picker.norm_click_type("") == "single"
         and picker.norm_click_type("__乱填__") == "single")
    c.ok("操作类型收敛：double / 双击 都认成「双击」",
         picker.norm_click_type("double") == "double"
         and picker.norm_click_type("双击") == "double")

    # --- ② 向后兼容：老配置（无 click_type）读入即补 single，行为不变 ---
    legacy = {"steps": [{"id": "s01",
                        "action": {"point": [0.1, 0.1], "radius": 6,
                                   "same_as_anchor": False, "click": True}}]}
    storage.normalize_steps(legacy)
    c.ok("向后兼容：老配置缺 click_type，读入即补 single（不破坏已存数据）",
         legacy["steps"][0]["action"]["click_type"] == "single",
         str(legacy["steps"][0]["action"]))

    legacy2 = {"steps": [{"id": "s02"}]}          # 连 action 都没有的极端老数据
    storage.normalize_steps(legacy2)
    c.ok("向后兼容：连 action 都没有的老数据，也能补出合法 action",
         legacy2["steps"][0]["action"].get("click_type") == "single"
         and legacy2["steps"][0]["action"].get("click") is True,
         str(legacy2["steps"][0]["action"]))

    # --- ③ 前向：engine 真按 click_type 分派（离线宿主记类型流水） ---
    def _one_step(kind):
        p = storage.default_profile()
        p["window"]["target"] = "screen"
        p["window"]["title_keywords"] = []
        p["execution"].update({"ready_timeout_s": 1.0, "verify_timeout_s": 0.2,
                               "settle_s": 0.0, "retry_max": 0,
                               "poll_interval_s": 0.05})
        frame0 = data["pages"]["p1"]
        st0 = {"id": "s01", "name": "一步", "frame": "s01.png",
               "anchor": {}, "action": {}}
        # **走生产同一份实现**建 anchor/action，只把 click_type 换成要测的那种
        picker.apply_to_step(st0, frame0, REC_PT, (1200, 300), BOX, False, 6,
                             click_type=kind)
        p["steps"] = [st0]
        h = OfflineHost([frame0, frame0], advance_every=1)
        tm = picker.cut_template(frame0, picker.build_box(frame0, REC_PT, BOX))
        Runner(p, h, templates={"s01": tm}, dry=False).run()
        return h

    h_s = _one_step("single")
    h_d = _one_step("double")
    c.ok("前向：click_type=single → 宿主收到的就是单击",
         h_s.click_kinds == ["single"], str(h_s.click_kinds))
    c.ok("前向：click_type=double → 宿主收到的就是双击",
         h_d.click_kinds == ["double"], str(h_d.click_kinds))

    # **反向对照（注入病因）**：把分派改成恒单击 → double 必须**收不到**
    # （证明"双击"不是碰巧跑通的，而是真由 click_type 分派出去的）
    _orig_norm = picker.norm_click_type
    picker.norm_click_type = lambda _v: "single"      # 注入病因：类型恒为单击
    try:
        h_ctrl = _one_step("double")
        c.ok("**反向对照**：把类型收敛改成恒「单击」→ double 配置**收不到双击**"
             "（证明分派真在起作用，不是碰巧）",
             h_ctrl.click_kinds == ["single"], str(h_ctrl.click_kinds))
    finally:
        picker.norm_click_type = _orig_norm

    # 复原后必须立刻恢复分派（证明刚才那次失败是注入造成的，不是环境的锅）
    h_restore = _one_step("double")
    c.ok("摘掉病因后立即恢复双击分派（对照成立，不是环境的锅）",
         h_restore.click_kinds == ["double"], str(h_restore.click_kinds))

    # --- ④ 落盘契约：build_action 一定带上 click_type ---
    act_d = picker.build_action(data["pages"]["p1"], (60, 60), REC_PT, False, 6,
                                click_type="double")
    c.ok("落盘契约：action 一定带 click_type，且 double 被原样写进配置",
         act_d.get("click_type") == "double", str(act_d.get("click_type")))
    act_def = picker.build_action(data["pages"]["p1"], (60, 60), REC_PT, False, 6)
    c.ok("落盘契约：不传 click_type 时默认为 single（老调用点一行不改也安全）",
         act_def.get("click_type") == "single", str(act_def.get("click_type")))

    # ================================================================== #
    # evals 式量化指标（**只记录，不参与断言**）
    # ================================================================== #
    # ① 正样本最高分 + 相对阈值的余量（余量薄 = 阈值该降或模板该重标）
    thr_demo = 0.82
    c.metric("正样本最高分（p1 自身）", round(float(r_dir.score), 4), "",
             "阈值 %.2f" % thr_demo)
    c.metric("正样本余量（得分 − 阈值）", round(float(r_dir.score) - thr_demo, 4),
             "", "越大越稳；< 0.05 就该重标模板")

    # ② 负样本分（拿 p1 的模板去 p2/p3 上找 —— 越低说明互斥性越好）
    neg_best = -1.0
    for other in ("p2", "p3"):
        try:
            r_neg = matcher.match_one(data["pages"][other], tmpl_eq, roi_eq,
                                      "template", (1.0,), thr_demo)
            neg_best = max(neg_best, float(r_neg.score))
        except Exception:
            pass
    if neg_best > -1.0:
        c.metric("负样本最高分（p1 模板 × 他页）", round(neg_best, 4), "",
                 "越低越好；> 0.82 会误判")

    # ③ 匹配耗时（定超时/重试次数时用得着）
    import time as _time
    _t0 = _time.perf_counter()
    _N = 20
    for _ in range(_N):
        matcher.match_template(frame_eq, tmpl_eq, roi_eq, (1.0,), thr_demo)
    _dt = (_time.perf_counter() - _t0) / _N * 1000.0
    c.metric("单次模板匹配耗时", round(_dt, 2), " ms",
             "定 verify_timeout / 重试节奏参考")

    return c.summary()


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
