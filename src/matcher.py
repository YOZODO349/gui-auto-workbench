# -*- coding: utf-8 -*-
"""识别层：模板匹配 + 色调匹配（四重证据）。

**分层**（铁律 4）：固定按钮走模板匹配；背景会变但字色不变走色调匹配。

两条最贵的经验，都写死在这里：

1. **模板与搜索尺度都要乘全局缩放系数**（见 geometry.global_scale）。
   不然 1920→2560 这种 1.333 倍的缩放会落在 `scales` 之外 → 全军覆没。
2. **色调匹配只有 score + coverage 不够**：实测"纯随机噪声"（score 0.787）和
   "纯色块"（coverage 4.754）都能蒙混过关。必须四重证据一起看，
   其中 `shape_iou`（形状证据）才是抗噪声的核心。

## 识别层扩展点（怎么接 OCR / 特征点匹配）

分派已改成**可注册表**（见文末 `_REGISTRY` / `register`）。
新增一种方法**不用改本文件的分派逻辑**，只要：

```python
# 1) 新建 src/matcher_ocr.py（或任意模块）
from src.matcher import MatchResult, register

def _match_ocr(frame, tmpl, roi, scales, threshold, tone_spec, want_debug):
    ...                    # 你的实现，返回 MatchResult
    return MatchResult(ok=True, score=0.93, method="ocr", box=(x, y, w, h))

# 2) 注册一行
register("ocr", _match_ocr)
```

之后 `match_one(..., method="ocr")` 即可，`picker.spot_check` 与
`engine._match_anchor` **无需改动** —— 它们都只调 `match_one`。

**为什么本工作台暂不实现 OCR**（口径已定）：OCR（PaddleOCR/Tesseract）会引入
重依赖（torch / onnxruntime + 数百 MB 模型），与"本地优先、轻部署"冲突。
先留接口，等真有"文字会变但版面固定"的场景再落地。

**注意**：`MatchResult`（上方 dataclass）是**统一契约，不能改** —— 所有方法
（含未来 OCR）共用同一个返回类型，上层才不用区分。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Sequence, Tuple

import cv2
import numpy as np

Rect = Tuple[int, int, int, int]


@dataclass
class MatchResult:
    ok: bool
    score: float = 0.0
    method: str = "template"
    box: Rect = (0, 0, 0, 0)          # 绝对坐标 (x, y, w, h)
    note: str = ""
    evidence: dict = field(default_factory=dict)

    @property
    def center(self) -> Tuple[int, int]:
        x, y, w, h = self.box
        return (x + w // 2, y + h // 2)


# --------------------------------------------------------------------------- #
# 模板匹配
# --------------------------------------------------------------------------- #
def match_template(frame, tmpl, roi: Rect, scales: Sequence[float] = (1.0,),
                   threshold: float = 0.82, want_debug: bool = False) -> MatchResult:
    """在 `roi` 里找 `tmpl`。

    `roi` 是**搜索窗**（不是全屏 —— 铁律 3：每个按钮/文字配固定 ROI）。
    `scales` 应该已经乘过全局缩放系数。
    """
    if frame is None or tmpl is None:
        return MatchResult(False, note="帧或模板为空")
    H, W = frame.shape[:2]
    x, y, w, h = [int(v) for v in roi]
    if w <= 2 or h <= 2 or x < 0 or y < 0 or x + w > W or y + h > H:
        return MatchResult(False, note="搜索窗越界或太小")
    sub = frame[y:y + h, x:x + w]
    if sub.size == 0:
        return MatchResult(False, note="搜索窗取到空图")

    best = MatchResult(False, score=-1.0)
    tried = []
    for s in scales:
        s = float(s)
        if s <= 0:
            continue
        tw = max(1, int(round(tmpl.shape[1] * s)))
        th = max(1, int(round(tmpl.shape[0] * s)))
        if tw > sub.shape[1] or th > sub.shape[0]:
            tried.append((s, None))
            continue
        t = tmpl if (tw == tmpl.shape[1] and th == tmpl.shape[0]) else \
            cv2.resize(tmpl, (tw, th), interpolation=cv2.INTER_AREA)
        try:
            res = cv2.matchTemplate(sub, t, cv2.TM_CCOEFF_NORMED)
            _mn, mx, _ml, ml = cv2.minMaxLoc(res)
        except cv2.error:
            tried.append((s, None))
            continue
        tried.append((s, float(mx)))
        if mx > best.score:
            best = MatchResult(True, float(mx), "template",
                               (x + int(ml[0]), y + int(ml[1]), tw, th))
    if want_debug:
        best.evidence["scales_tried"] = tried
    best.ok = best.score >= threshold
    if not best.ok:
        best.note = "最高分 %.3f < 阈值 %.2f" % (best.score, threshold)
    return best


# --------------------------------------------------------------------------- #
# 色调匹配（背景变、字色不变）
# --------------------------------------------------------------------------- #
@dataclass
class ToneSpec:
    hue_lo: int = 0
    hue_hi: int = 180
    s_min: int = 90
    score_min: float = 0.85
    cov_lo: float = 0.55
    cov_hi: float = 2.0            # **必须有上界**，否则纯色块也能过
    iou_min: float = 0.45          # 抗噪声关键
    outside_max: float = 0.18


def tone_mask(img, spec: ToneSpec):
    """按色域取二值掩码（只在 H/S 上判，**丢弃亮度 V** —— 背景变色不影响）。"""
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    h, s, _v = cv2.split(hsv)
    if spec.hue_lo <= spec.hue_hi:
        hue_ok = cv2.inRange(h, spec.hue_lo, spec.hue_hi)
    else:                                        # 红色跨 0 首尾相接
        hue_ok = cv2.bitwise_or(cv2.inRange(h, spec.hue_lo, 180),
                                cv2.inRange(h, 0, spec.hue_hi))
    s_ok = cv2.inRange(s, spec.s_min, 255)
    return cv2.bitwise_and(hue_ok, s_ok)


def match_tone(frame, tmpl, roi: Rect, spec: ToneSpec,
               scales: Sequence[float] = (1.0,), want_debug: bool = False) -> MatchResult:
    """色调匹配：先定位，再在最佳位置**算四重证据**。

    | 证据 | 含义 | 判据 |
    |---|---|---|
    | score | H/S 掩码模板相关度 | ≥ score_min |
    | coverage | ROI 内目标色像素 / 模板笔画像素 | cov_lo ~ cov_hi（有上界）|
    | shape_iou | 色域掩码 ∩ 模板掩码 / 并集 | ≥ iou_min |
    | outside_ratio | 模板非笔画区出现同色像素的比例 | ≤ outside_max |
    """
    if frame is None or tmpl is None:
        return MatchResult(False, method="tone", note="帧或模板为空")
    H, W = frame.shape[:2]
    x, y, w, h = [int(v) for v in roi]
    if w <= 2 or h <= 2 or x < 0 or y < 0 or x + w > W or y + h > H:
        return MatchResult(False, method="tone", note="搜索窗越界或太小")
    sub = frame[y:y + h, x:x + w]

    mt_full = tone_mask(tmpl, spec)
    if int(mt_full.sum() // 255) < 12:
        return MatchResult(False, method="tone", note="模板里目标色像素太少（色域没标对）")

    best = None
    for s in scales:
        s = float(s)
        if s <= 0:
            continue
        tw = max(1, int(round(tmpl.shape[1] * s)))
        th = max(1, int(round(tmpl.shape[0] * s)))
        if tw > sub.shape[1] or th > sub.shape[0]:
            continue
        # 掩码缩放必须 INTER_NEAREST —— INTER_AREA 会插值出灰值，形状判据直接报废
        mt = cv2.resize(mt_full, (tw, th), interpolation=cv2.INTER_NEAREST)
        mt_bool = mt > 0
        if mt_bool.sum() < 12:
            continue
        t_hs = cv2.cvtColor(cv2.resize(tmpl, (tw, th), interpolation=cv2.INTER_AREA),
                            cv2.COLOR_BGR2HSV)[:, :, :2].astype(np.float32)
        sub_hs = cv2.cvtColor(sub, cv2.COLOR_BGR2HSV)[:, :, :2].astype(np.float32)

        # 用"模板掩码内的 H/S"做一次带掩码的相关，粗定位
        try:
            tpl_masked = t_hs * mt_bool[:, :, None]
            res = cv2.matchTemplate(sub_hs, tpl_masked, cv2.TM_CCORR_NORMED,
                                    mask=mt.astype(np.float32))
            _mn, mx, _ml, ml = cv2.minMaxLoc(res)
        except cv2.error:
            ml, mx = (0, 0), 0.0

        cx0, cy0 = int(ml[0]), int(ml[1])
        win = sub[cy0:cy0 + th, cx0:cx0 + tw]
        if win.shape[0] != th or win.shape[1] != tw:
            continue
        mc = tone_mask(win, spec) > 0

        inter = int(np.logical_and(mc, mt_bool).sum())
        union = int(np.logical_or(mc, mt_bool).sum())
        shape_iou = inter / float(union) if union else 0.0
        coverage = float(mc.sum()) / float(max(1, mt_bool.sum()))
        not_pen = np.logical_not(mt_bool)
        denom = int(not_pen.sum())
        outside = float(np.logical_and(mc, not_pen).sum()) / float(denom) if denom else 0.0

        ev = {"score": float(mx), "coverage": coverage,
              "shape_iou": shape_iou, "outside_ratio": outside}
        ok = (ev["score"] >= spec.score_min
              and spec.cov_lo <= coverage <= spec.cov_hi
              and shape_iou >= spec.iou_min
              and outside <= spec.outside_max)
        cand = MatchResult(bool(ok), float(mx), "tone",
                           (x + cx0, y + cy0, tw, th), evidence=ev)
        if best is None or (cand.ok and not best.ok) or (cand.ok == best.ok and cand.score > best.score):
            best = cand
    if best is None:
        return MatchResult(False, method="tone", note="没有可用的缩放尺度")
    if want_debug:
        best.evidence = dict(best.evidence)
    if not best.ok:
        ev = best.evidence
        best.note = ("四重证据不过：score=%.3f cov=%.3f iou=%.3f out=%.3f"
                     % (ev.get("score", 0), ev.get("coverage", 0),
                        ev.get("shape_iou", 0), ev.get("outside_ratio", 0)))
    return best


# --------------------------------------------------------------------------- #
# 统一入口（**可注册表**）
# --------------------------------------------------------------------------- #
# 为什么用注册表而不是 if/else：
#   将来接 OCR / SIFT-ORB 特征点匹配时，只需**新增一个文件 + 一行 register**，
#   不必再改 `match_one`、`picker.spot_check`、`engine._match_anchor` 三处。
#   契约固定为：`fn(frame, tmpl, roi, scales, threshold, tone_spec, want_debug) -> MatchResult`
#   —— 比 `match_tone` 的原生签名多收 `threshold`，由注册时的适配层丢弃。
_REGISTRY = {}


def register(method: str, fn, *, override: bool = False) -> None:
    """注册一种识别方法。

    `fn` 必须接受**统一的 7 参**并返回 `MatchResult`：
        fn(frame, tmpl, roi, scales, threshold, tone_spec, want_debug)
    不适用的参数（如色调匹配不用 threshold）在适配层内部忽略即可。

    `override=False` 时重复注册同名方法会抛 ValueError（防静默覆盖）。
    """
    method = str(method)
    if not method:
        raise ValueError("方法名不能为空")
    if method in _REGISTRY and not override:
        raise ValueError("识别方法 %r 已注册（要覆盖请传 override=True）" % method)
    if not callable(fn):
        raise TypeError("识别方法 %r 的处理器必须可调用" % method)
    _REGISTRY[method] = fn


def unregister(method: str) -> bool:
    """摘掉一种方法（主要给测试用）。返回是否真的摘掉了。"""
    return _REGISTRY.pop(str(method), None) is not None


def registered_methods() -> list:
    """已注册的方法名列表（当前内建：template / tone）。"""
    return sorted(_REGISTRY)


def _as_template(frame, tmpl, roi, scales, threshold, tone_spec, want_debug):
    """适配：把统一 7 参映射到 `match_template` 的原生签名。"""
    return match_template(frame, tmpl, roi, scales, threshold, want_debug)


def _as_tone(frame, tmpl, roi, scales, threshold, tone_spec, want_debug):
    """适配：把统一 7 参映射到 `match_tone` 的原生签名（threshold 不适用，丢弃）。"""
    return match_tone(frame, tmpl, roi, tone_spec or ToneSpec(),
                      scales, want_debug)


def match_one(frame, tmpl, roi: Rect, method: str = "template",
              scales: Sequence[float] = (1.0,), threshold: float = 0.82,
              tone_spec: Optional[ToneSpec] = None, want_debug: bool = False) -> MatchResult:
    """统一分派到已注册的识别方法。

    未注册的 method **不抛异常**（跑流程时抛异常会让整条链断掉），
    而是**回退到 template 并在 note 里显式点名** —— 静默回退比报错更危险。
    """
    key = str(method or "template")
    fn = _REGISTRY.get(key)
    if fn is None:
        r = _REGISTRY["template"](frame, tmpl, roi, scales, threshold,
                                  tone_spec, want_debug)
        tip = "未注册的识别方法 %r，已回退 template；可用：%s" % (
            key, "、".join(registered_methods()))
        r.note = (r.note + "；" + tip) if r.note else tip
        return r
    return fn(frame, tmpl, roi, scales, threshold, tone_spec, want_debug)


# 内建两种方法 —— 注册在定义处，行为与原 if/else 完全等价
register("template", _as_template)
register("tone", _as_tone)


def explain_score(score: float) -> str:
    """**置信度落在哪个区间，是区分"画面不对"和"模板不对"最快的办法**（见 3.1）。

    - 0.05~0.35  画面根本不是目标页（闪屏/加载/弹窗）—— 这是**正确行为**，去修起跑门控
    - 0.75~0.82  画面对了，但模板脏了或阈值偏高
    - >= 0.90    正常命中
    """
    if score < 0:
        return "没跑起来（搜索窗/模板有问题）"
    if score < 0.35:
        return "画面不是目标页（随机相关度区间）—— 修起跑门控，别改阈值"
    if score < 0.5:
        return "很低：要么画面不对，要么分辨率缩放没生效"
    if score < 0.82:
        return "像模板或阈值问题（边缘失配区间），但也可能画面相似"
    if score < 0.9:
        return "差不多能命中，但余量薄"
    return "正常命中"


def diagnose(frame, tmpl, roi: Rect, method: str = "template",
             scales: Sequence[float] = (1.0,), threshold: float = 0.82,
             tone_spec: Optional[ToneSpec] = None) -> str:
    """自检动作：抓一帧 → 逐锚点打印「置信度 + 用了哪种方法」。
    这一步能省掉大半猜测（见 3.1）。
    """
    r = match_one(frame, tmpl, roi, method, scales, threshold, tone_spec, want_debug=True)
    lines = ["方法=%s  最高分=%.4f  判定=%s" % (r.method, r.score, "命中" if r.ok else "未命中")]
    lines.append("读法：" + explain_score(r.score))
    if r.evidence:
        lines.append("证据：" + ", ".join("%s=%.4f" % (k, v) for k, v in r.evidence.items()
                                          if isinstance(v, (int, float))))
    if r.note:
        lines.append("备注：" + r.note)
    return "\n".join(lines)
