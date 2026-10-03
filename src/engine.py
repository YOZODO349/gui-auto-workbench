# -*- coding: utf-8 -*-
"""状态机：起跑门控 → 判别页面 → 各步骤 → 收尾确认 → 完成。

**三道闸门**（最关键的一段逻辑，见 skill 5.2）：

| 情况 | 行为 |
|---|---|
| 下一步出现 | 成功，继续 |
| 还在原页、下一步没出现 | **允许重试**（换个落点再点），上限 retry_max |
| **已不在原页**（跑到别处了） | **立刻停止重试**，转恢复流程重新判别 |
| 重试 N 次仍不成功 | **中止并报错** |
| 只有一步（弱验证）、画面没变 | **只点一次、不重试**，判 **「完成」**（点击已发出即完成） |

⚠ **弱验证那行是例外，别按上面那行改**：只标一步时没有"下一页"可作证据，
  "点完画面没变"**不是失败证据**（双击开关 / 弹窗闪关 / 动作本就不改画面，
  都可能）—— 程序无权断定失败；但也不能重试，重试就成了**盲目多点**。
  ★ **只标一步时，"输出完点击"本身就是这一步的全部内容 → 直接算完成。**
    "无法验证"不是"未完成"，不该在任何终态名/提示语里带负面暗示；
    日志里留一句中性说明即可（给排查用）。

铁律：
- 起跑门控**只等不点**（登录页、弹窗得靠人，见 1.5）
- 收尾确认**只认画面、不发点击**：回到起点才算闭环
- 熔断计数**只在动作成功时清零**，否则"失败→回退→重试→再失败"永远归零，熔断永不触发
- 终态返回**实际终态名** —— 拿 "done" 冒充成功是最危险的失效模式
"""
from __future__ import annotations

import math
import time
from typing import Callable, Optional

from . import geometry, matcher, picker

DEFAULT_TONE = dict(matcher.ToneSpec().__dict__)


def _tone_spec(raw) -> matcher.ToneSpec:
    d = dict(DEFAULT_TONE)
    if isinstance(raw, dict):
        d.update({k: v for k, v in raw.items() if k in d})
    return matcher.ToneSpec(**d)


class Runner:
    """跑一轮。`dry=True` 只识别不点击（默认）；`dry=False` 才真的动鼠标。"""

    def __init__(self, profile: dict, host, templates: Optional[dict] = None,
                 on_event: Optional[Callable] = None, dry: bool = True,
                 start_index: Optional[int] = None, stop_event=None):
        self.profile = profile
        self.host = host
        if templates is None:
            from . import storage
            templates = storage.load_all_templates(profile)
        self.templates = templates
        self.on_event = on_event or (lambda *_a, **_k: None)
        self.dry = bool(dry)
        self.start_index = start_index
        self.stop_event = stop_event

        self.cfg = profile.get("execution", {})
        self.mcfg = profile.get("match", {})
        self.steps = profile.get("steps", [])

        # 熔断计数：**只在动作成功时清零**
        self.recover_fail = 0
        self.back_count = 0
        self._last_frame = None
        self._last_detail = []
        self._skipped = False       # 是否已经播报过"跳过未标齐步骤"
        # 本次跑里有没有"点了但没做页面验证"的拍。
        # ★ 这是**中性的事实记录**（只影响终态附注与日志措辞），
        #   **不是失败标记** —— 单步流程里它照样判「完成」。
        self._weak_unverified = False

    def _unfitted_names(self) -> list:
        """没标齐的步骤名（跑的时候会被跳过）。"""
        try:
            from . import storage
            return ["第%d步" % i for i, _m in storage.unfit_steps(self.profile)]
        except Exception:
            return []

    def _announce_skips(self) -> None:
        """跑之前**点名**要跳过哪些步骤 —— 不静默跳，否则用户以为跑了却没动。"""
        if self._skipped:
            return
        self._skipped = True
        names = self._unfitted_names()
        if names:
            self._log("以下步骤还没标齐，本次会**跳过**：%s"
                      "（点【采集本步画面】补上即可纳入）" % "、".join(names), "warn")

    def _is_fitted(self, step: dict) -> bool:
        """这一步标齐了吗？（缺识别点/框/模板/操作点任一即为未齐）"""
        try:
            from . import storage
            return not storage.step_missing(step)
        except Exception:
            a = step.get("anchor") or {}
            act = step.get("action") or {}
            return bool(a.get("point") and a.get("box") and a.get("template")
                        and (not act.get("click", True) or act.get("point")))

    def _next_fitted(self, i: int):
        """从 i 往后找**下一个已标齐、且不是它自己**的步骤下标（跳过未标齐的）。

        为什么必须有它：验证"点完第 i 步是否到了下一步"时，如果下一步压根没标，
        它永远认不出来 —— 于是验证必然失败、重试耗尽、误报"点不动"。
        跳过未标齐的步骤，验证目标就该顺延到下一个**真能认出来**的页面。

        ⚠️ **绝不能绕一圈找到它自己**：只有一步标齐时，若返回 i 本身，
        验证就变成"等第 i 页出现" —— 而画面本来就在第 i 页，立刻假通过。
        所以范围取 `k in 1..n-1`（不含 n），一圈走完没有第二个就返回 None。
        """
        n = len(self.steps)
        if n <= 1:
            return None
        for k in range(1, n):               # 不含 n：那会绕回自己
            j = (i + k) % n
            if self._is_fitted(self.steps[j]):
                return j
        return None

    def _fitted_count(self) -> int:
        return sum(1 for st in self.steps if self._is_fitted(st))

    # ------------------------------------------------------------------ #
    # 事件
    # ------------------------------------------------------------------ #
    def _emit(self, kind: str, payload) -> None:
        try:
            self.on_event(kind, payload)
        except Exception:
            pass

    def _log(self, text: str, tag: str = "info") -> None:
        self._emit("log", (text, tag))

    def _status(self, text: str) -> None:
        self._emit("status", text)

    def _check_stop(self) -> bool:
        return bool(self.stop_event is not None and self.stop_event.is_set())

    def _now(self) -> float:
        """时间源走宿主 —— 离线宿主给的是虚拟时间，测试不必真等。"""
        fn = getattr(self.host, "now", None)
        try:
            return float(fn()) if callable(fn) else time.time()
        except Exception:
            return time.time()

    # ------------------------------------------------------------------ #
    # 识别
    # ------------------------------------------------------------------ #
    def _match_anchor(self, frame, step: dict, tmpl, cw: int, ch: int,
                      scale: float) -> matcher.MatchResult:
        a = step.get("anchor") or {}
        box = geometry.rect_rel_to_abs(a.get("box", [0, 0, 0, 0]), cw, ch)
        px, py = geometry.point_rel_to_abs(a.get("point", [0, 0]), cw, ch)
        margin = float(self.mcfg.get("search_margin_ratio", 0.6))
        roi = geometry.search_window(px, py, box[2], box[3], cw, ch, margin)
        scales = [float(s) * scale for s in self.mcfg.get("scales", [1.0])]
        return matcher.match_one(
            frame, tmpl, roi,
            method=a.get("method", "template"),
            scales=scales,
            threshold=float(self.mcfg.get("threshold", 0.82)),
            tone_spec=_tone_spec(a.get("tone_spec")),
        )

    def page_scores(self, frame) -> list:
        """**单帧**同时问所有页面检查器（不额外花时间，见 5.5）。

        返回 [(hits, best_score, [每锚点结果]), ...]，与 steps 同序。
        """
        if frame is None:
            return []
        cw, ch = frame.shape[1], frame.shape[0]
        ref = self.profile.get("reference", {})
        scale = geometry.global_scale(cw, ch, int(ref.get("w", 0)), int(ref.get("h", 0)))
        out = []
        for st in self.steps:
            a = st.get("anchor") or {}
            hits, best = 0, 0.0
            rows = []
            tmpl = self.templates.get(st.get("id"))
            if tmpl is not None and a.get("point") and a.get("box"):
                r = self._match_anchor(frame, st, tmpl, cw, ch, scale)
                rows.append(r)
                if r.ok:
                    hits += 1
                best = max(best, r.score)
            out.append((hits, best, rows))
        return out

    def detect(self, frame=None, want_detail: bool = False):
        """判别当前在第几页（0 基）。判不出来返回 None。"""
        if frame is None:
            frame = self.host.capture()
        self._last_frame = frame
        scores = self.page_scores(frame)
        if not scores:
            return (None, None, []) if want_detail else None
        best_i, best_key = None, None
        for i, (hits, score, _rows) in enumerate(scores):
            if hits <= 0:
                continue
            key = (hits, score)
            if best_key is None or key > best_key:
                best_i, best_key = i, key
        if want_detail:
            return best_i, frame, scores
        return best_i

    def _wait_for_page(self, idx: int, timeout_s: float) -> bool:
        poll = float(self.cfg.get("poll_interval_s", 0.3))
        deadline = self._now() + max(0.0, timeout_s)
        while self._now() < deadline:
            if self._check_stop():
                return False
            got = self.detect()
            if got == idx:
                return True
            self.host.sleep(poll)
        return self.detect() == idx

    # ------------------------------------------------------------------ #
    # 入口
    # ------------------------------------------------------------------ #
    def run(self) -> dict:
        n = len(self.steps)
        if n == 0:
            return self._finish("中止：没有任何步骤", False, "请先去编辑模式标一步")
        self._emit("running", True)
        try:
            if self.dry:
                return self._dry_run()
            return self._live_run()
        finally:
            self._emit("running", False)
            if self._check_stop():
                self._log("收到停止指令", "warn")

    # ------------------------------------------------------------------ #
    # 干跑：只识别不点击，给一张"每步认到了没"的报告
    # ------------------------------------------------------------------ #
    def _dry_run(self) -> dict:
        self._status("干跑 · 只识别不点击")
        self._log("干跑开始：抓一帧，同时对全部 %d 步做识别体检" % len(self.steps))
        if not self.host.activate():
            self._log("切不到目标窗口前台，画面可能不准", "warn")
        frame = self.host.capture()
        if frame is None:
            return self._finish("中止：抓不到画面", False, "窗口可能被最小化或被遮挡")
        ok, why = (True, "")
        try:
            from . import winio
            ok, why = winio.frame_is_usable(frame)
        except Exception:
            pass
        if not ok:
            return self._finish("中止：帧不可用", False, why)

        idx, _f, scores = self.detect(frame, want_detail=True)
        self._log("帧尺寸 %dx%d" % (frame.shape[1], frame.shape[0]))
        for i, (hits, score, rows) in enumerate(scores, 1):
            st = self.steps[i - 1]
            tag = "ok" if hits > 0 else "warn"
            self._log("第%d步「%s」：命中 %d 个锚点，最高分 %.4f —— %s"
                      % (i, st.get("name", st.get("id", "")), hits, score,
                         "认到了" if hits > 0 else "没认到"), tag)
            if rows and rows[0].note:
                self._log("    ↳ %s" % rows[0].note, "muted")
        if idx is None:
            self._finish("干跑结束：一步都没认出来", False,
                         "画面不是任何已标步骤（或在加载/弹窗上）")
            return {"terminal": "干跑结束：一步都没认出来", "ok": False, "done": 0,
                    "total": len(self.steps), "dry": True, "reason": "一步都没认出来"}
        st = self.steps[idx]
        self._log("判别结果：当前画面 = 第%d步「%s」" % (idx + 1, st.get("name", "")), "ok")
        self._log("提示：干跑只证明**识别层**认得出，不证明点击链路能走通；"
                  "确认无误后再开真实点击。", "warn")
        self._emit("step", (idx, len(self.steps)))
        return {"terminal": "干跑完成（只识别，未点击）", "ok": True, "done": 0,
                "total": len(self.steps), "dry": True,
                "reason": "判别为第%d步" % (idx + 1), "detected": idx}

    # ------------------------------------------------------------------ #
    # 实跑
    # ------------------------------------------------------------------ #
    def _live_run(self) -> dict:
        cfg = self.cfg
        n = len(self.steps)
        self._announce_skips()

        # 1) 起跑门控：**只等，不点**。闪屏/加载/登录页在这里被挡掉。
        self._status("起跑门控 · 等待画面就绪")
        self._log("起跑门控：只等待、不点击（登录/弹窗需人工处理）")
        ready_timeout = float(cfg.get("ready_timeout_s", 60.0))
        if not self._gate(ready_timeout):
            if self._check_stop():
                return self._finish("已停止", False, "用户点了停止")
            return self._finish("中止：等不到可用画面", False,
                                "%.0f 秒内没有任何页面达标（程序还没开好？）" % ready_timeout)

        # 2) 判别入口（**不硬编码起始页**，见 5.5）
        idx = self.detect()
        if idx is None:
            return self._finish("中止：判别不出当前页面", False, "画面不在任何已知步骤上")
        if self.start_index is not None and self.start_index != idx:
            self._log("调试跑：实际在第%d步，按指定从第%d步起跑"
                      % (idx + 1, self.start_index + 1), "warn")
            idx = max(0, min(n - 1, self.start_index))
        i = idx
        self._log("当前在第%d步「%s」，开始执行" % (i + 1, self.steps[i].get("name", "")), "ok")
        self._emit("step", (i, n))

        last_i = (i - 1) % n
        done = 0
        guard = 0
        fitted_n = self._fitted_count()
        if fitted_n == 0:
            return self._finish("中止：没有一步标齐", False,
                                "所有步骤都缺数据 —— 先标齐至少一步再跑")
        # 只有一步标齐时无从用"下一步"做验证（没有下一个可认的页面），
        # 改用**原页仍在意**做弱验证：点完还在原页 = 没点动。
        single = (fitted_n == 1)

        while True:
            guard += 1
            if guard > n * 8 + 20:
                return self._finish("中止：循环次数异常", False, "疑似状态机打转")

            if self._check_stop():
                return self._finish("已停止", False, "用户点了停止")

            st = self.steps[i]
            # nxt / last_i 都取**已标齐**的邻居 —— 未标齐的跳过，
            # 否则拿一个永远认不出的页面去验证，必然误报"点不动"。
            nxt = self._next_fitted(i)
            prev_fit = None
            for k in range(1, n + 1):
                j = (i - k) % n
                if self._is_fitted(self.steps[j]):
                    prev_fit = j
                    break
            last_i = prev_fit if prev_fit is not None else (i - 1) % n

            # **跳过未标齐的步骤**：它既认不出也不该点，硬留在循环里会触发
            # 恢复熔断（明明只是没标，却被判成"页面丢了"）。所以先跳过去。
            if not self._is_fitted(st):
                self._log("第%d步「%s」还没标齐，跳过（不判别、不点击）"
                          % (i + 1, st.get("name", "")), "muted")
                i = nxt
                self._emit("step", (i, n))
                if i == 0 or (self.start_index is not None and i == self.start_index):
                    break
                continue

            # 确认真的站在第 i 页上（可能被上一次失败带偏）
            if self.detect() != i:
                new = self._recover()
                if new is None:
                    return self._finish("中止：恢复熔断", False,
                                        "连续 %d 次都判别不出页面" % self.recover_fail)
                i = new
                self._emit("step", (i, n))
                continue

            if st.get("action", {}).get("click", True):
                self._status("第%d步 · %s" % (i + 1, st.get("name", "")))
                self._emit("step", (i, n))
                res = self._click_and_verify(i, nxt, weak=single)
                if res in ("ok", "ok_weak"):
                    done += 1
                    if res == "ok_weak":
                        # 中性记录：这一拍没做页面验证（不判失败、不算异常）
                        self._weak_unverified = True
                    self.back_count = 0
                    i = nxt if nxt is not None else (i + 1) % n
                    self._emit("step", (i, n))
                    if single or i == 0 or \
                            (self.start_index is not None and i == self.start_index):
                        break
                    continue
                if res == "offpage":
                    new = self._recover()
                    if new is None:
                        return self._finish("中止：恢复熔断", False,
                                            "连续 %d 次判别不出页面" % self.recover_fail)
                    i = new
                    self._emit("step", (i, n))
                    continue
                # retry_fail：退回上一步试试（封顶）
                if self._try_back(i, last_i):
                    i = last_i
                    self._emit("step", (i, n))
                    continue
                return self._finish("中止：第%d步点不动" % (i + 1), False,
                                    "重试 %d 次仍未到下一步" % int(cfg.get("retry_max", 2)))
            else:
                # 不点击的步骤：跳过（但仍要在画面上认得它）
                self._log("第%d步设为「不点击」，跳过" % (i + 1), "muted")
                i = nxt if nxt is not None else (i + 1) % n
                self._emit("step", (i, n))
                if single or i == 0:
                    break
                continue

        # 3) 收尾确认：**只认画面、不发点击**。回到起点才算闭环。
        #    ⚠️ 但**不许它拦住"预演"** —— 用户标了 1 步也想立刻试跑，
        #    硬要闭环就等于"必须先跑通一整轮才能跑"。所以：
        #    ① 单步流程（n == 1）根本无从"回到起点"，直接跳过收尾校验；
        #    ② 校验没过只**警告**，照常判定完成 —— 把"闭环成立"降级为一条提示，
        #       而不是一道闸门。这样"编辑好至少一步即可真跑"成立。
        self._status("收尾校验")
        self._log("收尾校验开始：只认画面，不发点击")
        if n == 1:
            self._log("只有 1 步：无从对照起点，跳过收尾校验", "muted")
            home_ok, home_note = True, "单步流程"
        else:
            home_ok = self._confirm_home(last_i)
            home_note = "已回到起点页，闭环成立" if home_ok else "没回到起点页"
        if not home_ok:
            self._log("收尾校验没过：%s。"
                      "这不影响本次预演算作完成（随时预演不强制闭环）。" % home_note, "warn")
        self._status("完成")
        debug_run = (self.start_index is not None and self.start_index != 0)
        if debug_run:
            return self._finish("调试跑完（未记完成）", True,
                                "从第%d步起跑，不算完整一轮" % (self.start_index + 1))
        # ★ 单步流程：**输出完点击就算完成**。
        #   只标一步时，"点击已发出"本身就是这一步的全部内容 ——
        #   没有下一页可验证，"验证不到"不该被算成任何形式的未完成/异常。
        #   （早先这里返回"跑完（有拍未验证）"，虽然 ok=True，但终态仍带着
        #     "有问题"的暗示，用户看到会以为没跑好。改成直接判「完成」。）
        #   注意：这句只影响**终态名**；日志里仍如实记一笔"未做页面验证"，
        #   那是给排查用的信息，不是失败标记。
        if getattr(self, "_weak_unverified", False):
            return self._finish("完成", True,
                                "只有一步：点击已发出即算完成"
                                "（该步无从用下一页验证，已如实记入日志）",
                                done=done)
        if home_ok:
            return self._finish("完成", True, "已回到起点页，闭环成立", done=done)
        return self._finish("预演完成（未回到起点）", True,
                            "走完全程；收尾时不在起点页 —— 可能最后一下点空了，"
                            "看日志核对", done=done)

    # ------------------------------------------------------------------ #
    # 门控 / 闸门 / 恢复 / 收尾
    # ------------------------------------------------------------------ #
    def _gate(self, timeout_s: float) -> bool:
        """纯等待状态：反复截到某一页达标才放行。**只等，不点。**"""
        poll = float(self.cfg.get("poll_interval_s", 0.3))
        deadline = self._now() + max(0.0, timeout_s)
        last_note = self._now()
        while self._now() < deadline:
            if self._check_stop():
                return False
            if not self.host.activate():
                self.host.sleep(poll)
                continue
            frame = self.host.capture()
            if frame is not None and self.detect(frame) is not None:
                return True
            now = self._now()
            if now - last_note > 5.0:
                self._log("起跑门控：还在等画面就绪…（只等不点）", "muted")
                last_note = now
            self.host.sleep(poll)
        return False

    def _click_and_verify(self, i: int, nxt, weak: bool = False) -> str:
        """点第 i 步，并拿**下一步的识别点**做正向验证。

        `nxt` 可能为 None（一圈里只有它一个标齐的步骤）——此时无从等"下一步出现"。
        `weak=True` 时改用**弱验证**：点完只要"离开了原页"就算点动（不再要求认出某页）。
        这是为「只标一步就想试跑」准备的 —— 没有下一步可等，只能看原页还在不在。

        ⚠ **weak 模式不许把"猜不出来"判成"失败"**（这是用户报"点开始就出错"的根因）：
          只有一步时，"点完画面没变"完全可能是**正常**的 —— 比如双击的是个开关、
          弹窗闪一下就关、或者这个动作本来就不改画面。程序**没有任何办法**证明
          点击没生效，那就不能断定失败。
          所以 weak 模式重试完仍判"没动"时，返回 `ok_weak`。
          ★ 返回 `ok_weak` 的含义是**「完成」**：只标一步时，"输出完点击"本身就是
            这一步的全部内容（用户要的就是"点下去"），**验证不到 ≠ 没完成**。
            终态照判「完成」，日志里只留一句**中性**的说明给排查用。
          真正的验证留给"有下一步可比"的多步流程 —— 那时证据是硬的。
        """
        cfg = self.cfg
        st = self.steps[i]
        act = st.get("action", {})
        cw, ch = self.host.client_size()
        px, py = geometry.point_rel_to_abs(act.get("point", [0, 0]), cw, ch)
        radius = int(act.get("radius", cfg.get("click_radius", 6)))
        retry_max = int(cfg.get("retry_max", 2))
        settle = float(cfg.get("settle_s", 0.25))
        verify_timeout = float(cfg.get("verify_timeout_s", 3.0))

        # ⚠ **弱验证模式只点一次，不重试**（安全红线）：
        #   重试的前提是"上一次判定失败了"。而弱验证里"画面没变"**不是失败证据**
        #   （见函数开头说明）—— 那就成了**盲目多点**：
        #   双击可能被连点成"打开又关闭"、点到菜单弹了又收，甚至误触发别的东西。
        #   宁可"点了但没验证到"（如实告知用户），也不能靠反复点去碰运气。
        attempts = 1 if weak else retry_max + 1

        # 复点要换位置：中心 + 周围几个点轮着打
        pts = geometry.ring_points(px, py, radius, count=attempts)

        # 单击还是双击：从 action 里取，**缺省当单击**（老配置没有这字段，
        # 行为必须与从前一模一样）。复点重试时同样沿用这个类型。
        click_type = picker.norm_click_type(act.get("click_type"))
        is_double = (click_type == picker.CLICK_DOUBLE)
        type_cn = "双击" if is_double else "单击"

        for attempt in range(attempts):
            if self._check_stop():
                return "retry_fail"
            pt = pts[min(attempt, len(pts) - 1)]
            self._log("第%d步：%s (%d, %d)%s"
                      % (i + 1, type_cn, pt[0], pt[1],
                         "" if attempt == 0 else "（第 %d 次重试，换落点）" % attempt))
            self.host.click(pt[0], pt[1], double=is_double)
            self.host.sleep(settle)

            # ① 正常路径：等"下一步"出现
            if nxt is not None and self._wait_for_page(nxt, verify_timeout):
                self._log("第%d步 → 第%d步 验证通过 ✓" % (i + 1, nxt + 1), "ok")
                self.recover_fail = 0              # 动作成功才清零
                return "ok"

            now = self.detect()

            # ② 弱验证（只有一步标齐时）：点完不必"认出下一页"，
            #    只要**不再停在原页**就算点动。注意三种情形要分清：
            #      detect() == i      → 还赖在原页，没点动（重试）
            #      detect() == 别的页 → 明显动了（通过）
            #      detect() is None   → **认不出任何已知页面** —— 在"只标了一步"
            #                           的前提下，这同样是"画面变了"的证据，必须算通过。
            #    （早先的写法要求 `now is not None` 才通过，于是 None 被误判成
            #      "没动" —— 而恰恰是 None 最常见。这是个语义陷阱，别再踩。）
            if weak:
                if now != i:
                    self._log("第%d步：已离开原页（弱验证通过 —— 只标了一步，"
                              "认不出目标页；画面已改变即算点动）" % (i + 1), "ok")
                    self.recover_fail = 0
                    return "ok"
                # ⚠ **弱验证不重试**（见上面 attempts=1）：这里只记一笔就走，
                #   别写"允许重试" —— 那会把读者带偏（weak 下压根没有第二次）。
                self._log("第%d步：点击已发出（画面未变，单步流程无需再验证）"
                          % (i + 1), "info")
                continue

            if now is not None and now != i:
                self._log("已不在原页（现在像第%d步）→ **停止重试**，转恢复流程"
                          % (now + 1), "warn")
                return "offpage"
            self._log("下一步没出现、原页还在 → 允许重试（换落点）", "warn")

        # ③ 重试都用完了
        if weak:
            # **只标一步**：没有"下一页"可作证据，程序无权断定点击失败。
            # 坐标已经点到了 —— 这一步该做的就做完了，即判定完成。
            # ★ 措辞纪律：这是**正常完成**，不是"放行"、不是"未验证"。
            #   日志里留一句"未做页面验证"是给排查用的信息，语气必须中性。
            self._log("第%d步：点击已发出 —— 单步流程到此即算完成"
                      "（只有一步，无从用下一页验证；若画面本该变化，"
                      "可目视确认落点是否对准了目标控件）" % (i + 1), "ok")
            self.recover_fail = 0
            return "ok_weak"
        return "retry_fail"

    def _try_back(self, i: int, last_i: int) -> bool:
        """一步失败时，除了看下一步，**还要看上一步的画面还在不在** ——
        还在就说明压根没点进去，退回上一步重点（封顶，别无限回退）。
        """
        cap = int(self.cfg.get("back_max", 1))
        if last_i == i or self.back_count >= cap:
            return False
        if self.detect() != last_i:
            return False
        self.back_count += 1
        self._log("第%d步没点进去（第%d步画面还在）→ 退回第%d步重点（第 %d/%d 次）"
                  % (i + 1, last_i + 1, last_i + 1, self.back_count, cap), "warn")
        return True

    def _recover(self):
        """恢复的目标是"判别状态"（不是某个具体页），连续 N 次进不去就熔断。

        ⚠ **熔断计数清零纪律（本轮实测校准后固化）**：
        `self.recover_fail` **只允许在"动作成功"时清零** —— 本文件里就是两处：
          - `_click_and_verify` 正常路径验证通过（`_wait_for_page(nxt, ...)` 为真）
          - 弱验证下 `now != i`（确认已离开原页）
        **绝不能在"重试开始 / 进入恢复 / 循环下一拍"处清零** ——
        那样一旦页面永远回不来，计数被反复抹掉，熔断**永远触发不了**，
        程序会一直空转到超时，用户看到的是"卡住了"。
        这是"熔断要有意义"与"计数别被顺手清零"两件事的边界，改动本处务必连带核对那两处。
        """
        limit = int(self.cfg.get("recover_limit", 5))
        self.recover_fail += 1
        if self.recover_fail >= limit:
            self._log("恢复流程连续 %d 次都没能判出页面 → 熔断中止" % self.recover_fail, "err")
            self._save_scene("fail_recover")
            return None
        self._log("进入恢复流程（第 %d/%d 次）：重新判别当前页面"
                  % (self.recover_fail, limit), "warn")
        self.host.sleep(float(self.cfg.get("settle_s", 0.25)))
        return self.detect()

    def _confirm_home(self, last_i: int) -> bool:
        """收尾确认：只认画面、不发点击；超时但末步画面仍在 → 退回末步重按一次。"""
        wait = float(self.cfg.get("return_home_wait_s", 15.0))
        retries = int(self.cfg.get("return_home_retries", 1))
        if self._wait_for_page(0, wait):
            self._log("收尾校验通过：已回到起点页（第1步）✓", "ok")
            return True
        self._log("收尾校验未通过：%.0f 秒内没回到起点页" % wait, "warn")
        for r in range(retries):
            if self._check_stop():
                return False
            if self.detect() != last_i:
                continue
            self._log("末步画面仍在 → 退回末步重按一次（换落点）", "warn")
            self._click_and_verify(last_i, 0)
            if self._wait_for_page(0, wait):
                self._log("收尾校验通过：重按后回到起点页 ✓", "ok")
                return True
        self._save_scene("fail_return_home")
        return False

    # ------------------------------------------------------------------ #
    # 收场
    # ------------------------------------------------------------------ #
    def _save_scene(self, prefix: str) -> Optional[str]:
        """出错现场存盘 —— 用户能直接把这张图发回来。"""
        if self._last_frame is None:
            return None
        from . import storage, util
        name = "%s_%s.png" % (prefix, util.stamp())
        try:
            path = self.host.save_frame(name, self._last_frame)
            if path and not path.startswith(("C:", "/")):
                path = storage.logs_path(path)
            self._emit("save_frame", path)
            self._log("出错现场已存：%s" % path, "warn")
            return path
        except Exception:
            return None

    def _finish(self, terminal: str, ok: bool, reason: str = "", done: int = 0) -> dict:
        """**终态返回实际终态名** —— 不许拿 "done" 冒充成功（见 5.4）。"""
        payload = {"terminal": terminal, "ok": bool(ok), "reason": reason,
                   "done": done, "total": len(self.steps), "dry": self.dry}
        tag = "ok" if ok else "err"
        self._log("终态：%s%s" % (terminal, ("（%s）" % reason) if reason else ""), tag)
        self._emit("finish", payload)
        return payload
