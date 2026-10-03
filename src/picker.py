# -*- coding: utf-8 -*-
"""编辑模式的核心动作：**用户只给两个点，其余全在这里**。

- 识别框由程序**以识别点为中心**自动裁（用户不拖框 —— 单击比拖框省事，也不会框进大片背景）
- 模板从**刚采集到的那一帧**裁：天生干净，不带任何记号、尺寸天然一致
- 生成落盘用的 anchor / action（**一律相对值 0~1**）
- 顺带给出"当场匹配得分" —— 这就是**边标边验**

`gui.py` 与自测脚本**共用**这一份实现。各写一份必然走偏，
"离线评测 100% 通过、真机一塌糊涂"就是这么来的。
"""
from __future__ import annotations

from typing import Optional, Sequence, Tuple

from . import geometry, matcher, storage


def default_box_px(profile: dict, frame_w: int) -> Tuple[int, int]:
    """识别框的默认尺寸（**按参考分辨率给，实机上等比换算**）。"""
    ref = profile.get("reference", {})
    rw = int(ref.get("w", 0) or 0)
    k = (frame_w / float(rw)) if rw else 1.0
    w = max(storage.MIN_BOX, int(round(storage.DEFAULT_BOX_W * k)))
    h = max(storage.MIN_BOX, int(round(storage.DEFAULT_BOX_H * k)))
    return (w, h)


def build_box(frame, rec_pt: Sequence[float], box_px: Sequence[float]):
    """以识别点为中心裁一个框，夹在画面内。"""
    H, W = frame.shape[:2]
    return geometry.center_box(rec_pt[0], rec_pt[1], box_px[0], box_px[1], W, H)


def cut_template(frame, box):
    """从**这一帧**裁模板。"""
    x, y, w, h = box
    return frame[y:y + h, x:x + w].copy()


def build_anchor(frame, rec_pt: Sequence[float], box, template_name: str,
                 method: str = "template",
                 tone_spec: Optional[dict] = None) -> dict:
    H, W = frame.shape[:2]
    a = {
        "point": [round(v, 6) for v in geometry.point_abs_to_rel(rec_pt, W, H)],
        "box": [round(v, 6) for v in geometry.rect_abs_to_rel(box, W, H)],
        "template": template_name,
        "method": method,
    }
    if method == "tone":
        a["tone_spec"] = tone_spec or {}
    return a


CLICK_SINGLE = "single"
CLICK_DOUBLE = "double"
CLICK_TYPES = (CLICK_SINGLE, CLICK_DOUBLE)


def norm_click_type(value) -> str:
    """把任意输入收敛成合法操作类型。

    **默认 single**：老配置里没有这个字段、或被填了脏值，一律当"单击"——
    这正是本功能"向后兼容、不破坏已存数据"的落点。
    """
    v = str(value or "").strip().lower()
    return CLICK_DOUBLE if v in ("double", "dbl", "2", "双击") else CLICK_SINGLE


def act_point_offset(rec_pt_abs, act_pt_abs, box) -> Tuple[bool, str]:
    """操作点相对识别框的"出格检查"。返回 `(是否在框内, 说明文字)`。

    ★ **为什么需要它**：操作点与识别点**本来就可以不同**（识别点用于"认出这是哪一页"，
      操作点才是"要点的位置"），所以**不禁止两点不一致**。但两者若差得远，
      很可能意味着**操作点落到了目标控件外面** —— 那一下就会点空，
      而"点空"在单步流程里又验证不出来（画面没变化本来就是允许的），
      用户只会看到"跑完了但什么都没发生"，极难自查。
      所以这里只做**提示**，不拦保存。

    判定用**识别框**作参照物：框是程序以识别点为中心自动裁的那块，
    近似"目标控件所在区域"。操作点跑出框，大概率就是没点中控件。

    ⚠ **容差必须按框的尺寸按比例给，不能用固定像素**（这是本函数第一版的 bug）：
      第一版写死 `TOL = 32px`，结果**恰好放过了用户的真实案例**
      （他那个点正在框上方 31px —— 卡在容差内）。而那个框高只有 33px，
      32px 的容差几乎等于整个框高，等于**没在检查**。
      现在改成 `出框容差 = 框该方向尺寸的 0.5 倍`（下限 6px，上限 24px）：
      · 框 119×33 → 垂直容差 16px，用户那 31px 就会被抓到 ✓
      · 小框也不会因为容差为 0 而神经过敏
    """
    if not rec_pt_abs or not act_pt_abs or not box:
        return True, ""
    bx, by, bw, bh = [float(v) for v in box[:4]]
    cx, cy = float(act_pt_abs[0]), float(act_pt_abs[1])

    def _tol(size: float) -> float:
        """按边长的比例给容差，并夹在 [6, 24] 之间。"""
        return max(6.0, min(24.0, float(size) * 0.5))

    tx, ty = _tol(bw), _tol(bh)
    inside = (bx - tx <= cx <= bx + bw + tx) and (by - ty <= cy <= by + bh + ty)
    if inside:
        return True, ""
    dx = cx - (bx + bw / 2.0)
    dy = cy - (by + bh / 2.0)
    tips = []
    if abs(dy) > bh / 2.0 + ty:
        tips.append("在框%s %.0f px" % ("上方" if dy < 0 else "下方", abs(dy) - bh / 2.0))
    if abs(dx) > bw / 2.0 + tx:
        tips.append("在框%s %.0f px" % ("左侧" if dx < 0 else "右侧", abs(dx) - bw / 2.0))
    return False, ("操作点落在识别框外（%s）—— 框只有 %d×%d px。"
                   "这一下**可能点空**，请核对是否对准了目标控件。"
                   % ("，".join(tips) or "超出较多", int(bw), int(bh)))


def build_action(frame, act_pt: Optional[Sequence[float]], rec_pt: Sequence[float],
                 same_as_anchor: bool, radius: int = 6, click: bool = True,
                 click_type: str = CLICK_SINGLE) -> dict:
    H, W = frame.shape[:2]
    base = rec_pt if (same_as_anchor or act_pt is None) else act_pt
    return {
        "point": [round(v, 6) for v in geometry.point_abs_to_rel(base, W, H)],
        "radius": int(radius),
        "same_as_anchor": bool(same_as_anchor),
        "click": bool(click),
        # 单击还是双击。与 click(bool) 正交：click 管"点不点"，click_type 管"点几下"。
        "click_type": norm_click_type(click_type),
    }


def _scales(profile: dict, cw: int, ch: int) -> list:
    ref = profile.get("reference", {})
    scale = geometry.global_scale(cw, ch, int(ref.get("w", 0) or 0),
                                  int(ref.get("h", 0) or 0))
    return [float(s) * scale for s in profile.get("match", {}).get("scales", [1.0])]


def search_roi(profile: dict, rec_pt_abs, box, cw: int, ch: int):
    margin = float(profile.get("match", {}).get("search_margin_ratio", 0.6))
    return geometry.search_window(rec_pt_abs[0], rec_pt_abs[1], box[2], box[3],
                                  cw, ch, margin)


def spot_check(frame, tmpl, rec_pt_abs, box, profile: dict) -> matcher.MatchResult:
    """**当场复核**：拿刚裁的模板去匹配这一帧 —— 0.97 还是 0.42 一眼看穿，
    不用等整批跑完才发现框到了别的目标。
    """
    cw, ch = frame.shape[1], frame.shape[0]
    roi = search_roi(profile, rec_pt_abs, box, cw, ch)
    return matcher.match_template(frame, tmpl, roi,
                                  _scales(profile, cw, ch),
                                  float(profile.get("match", {}).get("threshold", 0.82)))


def negative_score(profile: dict, tmpl, cur_step_id: str,
                   load_frame=None) -> Optional[float]:
    """再拿这块模板去**别的已标步骤的帧**上跑一遍，取最高分（越低越好）。

    离阈值太近就**换元素**，别调阈值硬撑（见 skill 3.4）。
    """
    from . import storage as _st
    loader = load_frame or _st.load_step_frame
    thr = float(profile.get("match", {}).get("threshold", 0.82))
    best = None
    for st in profile.get("steps", []):
        if st.get("id") == cur_step_id:
            continue
        a = st.get("anchor") or {}
        if not a.get("point") or not a.get("box"):
            continue
        other = loader(st["id"])
        if other is None:
            continue
        H, W = other.shape[:2]
        bx = geometry.rect_rel_to_abs(a["box"], W, H)
        px, py = geometry.point_rel_to_abs(a["point"], W, H)
        roi = search_roi(profile, (px, py), bx, W, H)
        try:
            r = matcher.match_template(other, tmpl, roi, _scales(profile, W, H), thr)
        except Exception:
            continue
        if r.score > 0:
            best = r.score if best is None else max(best, r.score)
    return best


def apply_to_step(step: dict, frame, rec_pt, act_pt, box_px,
                  same_as_anchor: bool, radius: int = 6,
                  method: str = "template", tone_spec: Optional[dict] = None,
                  click_type: str = CLICK_SINGLE):
    """一步到位：裁框 → 裁模板 → 写回 step 的 anchor / action。

    返回 (anchor, action, box, template)。
    """
    box = build_box(frame, rec_pt, box_px)
    tmpl = cut_template(frame, box)
    if tmpl.size == 0:
        raise ValueError("裁出来的模板是空的（识别点太贴边？）")
    sid = step.get("id") or ""
    anchor = build_anchor(frame, rec_pt, box, "%s.png" % sid, method, tone_spec)
    action = build_action(frame, act_pt, rec_pt, same_as_anchor, radius,
                          click_type=click_type)
    step["anchor"] = anchor
    step["action"] = action
    step["frame"] = "%s.png" % sid
    return anchor, action, box, tmpl
