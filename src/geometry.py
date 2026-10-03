# -*- coding: utf-8 -*-
"""坐标换算：**对外一律存相对值 0~1，对内才用像素**。

参考分辨率 = 采集到的第一帧尺寸（程序自己量的，不问用户）。
运行时按「实机客户区 / 参考分辨率」算一个**全局缩放系数**，
透传给模板匹配器与色调匹配器 —— 换机器、换窗口大小全靠它。

为什么记这么死：模板默认只在配置的 `scales`（如 ±10%）里搜索，
1920→2560 需要 1.333 倍，落在范围外就**全军覆没**。
"""
from __future__ import annotations

from typing import Iterable, Sequence, Tuple

Rect = Tuple[int, int, int, int]      # (x, y, w, h)
Point = Tuple[float, float]


def global_scale(client_w: int, client_h: int, ref_w: int, ref_h: int) -> float:
    """全局缩放系数 = x/y 两个方向的平均。唯一出处，别到处自己算。"""
    if ref_w <= 0 or ref_h <= 0:
        return 1.0
    sx = client_w / float(ref_w)
    sy = client_h / float(ref_h)
    return (sx + sy) / 2.0


def clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else (hi if v > hi else v)


def point_abs_to_rel(pt: Sequence[float], ref_w: int, ref_h: int) -> Point:
    """像素 → 相对值（存盘用这个）。"""
    if ref_w <= 0 or ref_h <= 0:
        return (0.0, 0.0)
    return (clamp(pt[0] / float(ref_w), 0.0, 1.0),
            clamp(pt[1] / float(ref_h), 0.0, 1.0))


def point_rel_to_abs(pt: Sequence[float], w: int, h: int) -> Tuple[int, int]:
    """相对值 → 像素（按当前实机尺寸换回来）。"""
    return (int(round(float(pt[0]) * w)), int(round(float(pt[1]) * h)))


def rect_abs_to_rel(r: Sequence[float], ref_w: int, ref_h: int) -> Tuple[float, float, float, float]:
    if ref_w <= 0 or ref_h <= 0:
        return (0.0, 0.0, 0.0, 0.0)
    x, y, w, h = r
    return (clamp(x / float(ref_w), 0.0, 1.0),
            clamp(y / float(ref_h), 0.0, 1.0),
            clamp(w / float(ref_w), 0.0, 1.0),
            clamp(h / float(ref_h), 0.0, 1.0))


def rect_rel_to_abs(r: Sequence[float], w: int, h: int) -> Rect:
    x, y, rw, rh = r
    return (int(round(float(x) * w)), int(round(float(y) * h)),
            int(round(float(rw) * w)), int(round(float(rh) * h)))


def center_box(cx: float, cy: float, bw: float, bh: float,
               W: int, H: int) -> Rect:
    """**以点为中心**裁一个框 —— 用户只给一个点，框由程序自己定（新 skill 6.5 ③）。

    返回框会被夹在画面内（贴边时向里推，而不是裁成半截）。
    """
    x = int(round(cx - bw / 2.0))
    y = int(round(cy - bh / 2.0))
    w = int(round(bw))
    h = int(round(bh))
    if w > W:
        w = W
    if h > H:
        h = H
    if x < 0:
        x = 0
    if y < 0:
        y = 0
    if x + w > W:
        x = W - w
    if y + h > H:
        y = H - h
    return (max(0, x), max(0, y), w, h)


def search_window(cx: int, cy: int, bw: int, bh: int,
                  W: int, H: int, margin_ratio: float = 0.6) -> Rect:
    """识别点周围的**搜索窗**（铁律 3：不全屏搜索）。

    窗 = 模板尺寸各向外扩 `margin_ratio` 倍，再夹进画面。
    """
    mw = int(round(bw * (1.0 + margin_ratio * 2)))
    mh = int(round(bh * (1.0 + margin_ratio * 2)))
    x = cx - mw // 2
    y = cy - mh // 2
    w = min(mw, W)
    h = min(mh, H)
    x = int(clamp(x, 0, max(0, W - w)))
    y = int(clamp(y, 0, max(0, H - h)))
    return (x, y, w, h)


def rect_inside(r: Rect, W: int, H: int) -> bool:
    x, y, w, h = r
    return x >= 0 and y >= 0 and w > 0 and h > 0 and x + w <= W and y + h <= H


def ring_points(cx: int, cy: int, radius: int, count: int = 5) -> list:
    """复点换落点：在中心附近取几个备选点轮着打（别总戳同一个像素）。"""
    if radius <= 0 or count <= 1:
        return [(cx, cy)]
    import math
    pts = [(cx, cy)]
    for i in range(1, count):
        ang = 2.0 * math.pi * (i - 1) / max(1, count - 1)
        pts.append((int(round(cx + radius * math.cos(ang))),
                    int(round(cy + radius * math.sin(ang)))))
    return pts


def near_screen_edge(x: int, y: int, W: int, H: int, margin: int = 3) -> bool:
    return x < margin or y < margin or x > W - margin - 1 or y > H - margin - 1


def cross_check_point(x: int, y: int, client_rect=None, frame_wh=None,
                      ref_wh=None, cursor_xy=None, tol: int = 4) -> dict:
    """**三源旁证**：同一个点，三条独立路径各算一遍，看落点是否重合。

    为什么需要它：屏幕坐标算错时，表现是「点偏了几个像素」—— 界面上看不出来，
    但点十次就有一次没打中按钮。三条路径分别是：

    | 源 | 路径 | 何时最可信 |
    |---|---|---|
    | `cursor` | 系统 `GetCursorPos`（用户实拍的点） | 用户刚亲手点过，最真 |
    | `frame` | 帧内相对值 → 像素（识别框那条路） | 抓帧链路正确时 |
    | `client` | 客户区左上偏移 + 帧内坐标 | 目标窗口有边框/标题栏时 |

    三者**不重合** = 抓帧链路 / 客户区偏移 / DPI 三者中至少一个不对。

    参数都可选，给了几个源就算几个源（不足 2 个则跳过判定）。
    `client_rect` = 客户区在屏幕上的 (x, y, w, h)；`frame_wh` = 帧尺寸；
    `ref_wh` = 参考分辨率；`cursor_xy` = 系统光标当前位置。

    返回 dict（**不抛异常、不阻断** —— 旁证只告警，决定权在调用方）：
        {"consistent": bool, "spread": int, "tol": int,
         "sources": {name: (x, y)}, "detail": str}

    纯计算，不 import winio（避免循环依赖）；`cursor_xy` 由调用方用 winio 取好传入。
    """
    x, y = int(x), int(y)
    sources = {}

    # 源①：客户区偏移 —— 帧内坐标 + 客户区左上角在屏幕上的位置
    if isinstance(client_rect, (tuple, list)) and len(client_rect) >= 2:
        sources["client"] = (int(client_rect[0]) + x, int(client_rect[1]) + y)

    # 源②：帧内换算 —— 帧与参考分辨率同尺寸时，帧内坐标即屏幕坐标；
    #        不同尺寸则按全局缩放系数换算（与运行时同一条公式）
    if (isinstance(frame_wh, (tuple, list)) and len(frame_wh) >= 2
            and isinstance(ref_wh, (tuple, list)) and len(ref_wh) >= 2):
        fw, fh = int(frame_wh[0]), int(frame_wh[1])
        rw, rh = int(ref_wh[0]), int(ref_wh[1])
        if rw > 0 and rh > 0:
            if fw == rw and fh == rh:
                sources["frame"] = (x, y)
            else:
                s = global_scale(fw, fh, rw, rh)
                if s > 0:
                    sources["frame"] = (int(round(x * s)), int(round(y * s)))

    # 源③：系统光标（调用方传入；没有就跳过）
    if isinstance(cursor_xy, (tuple, list)) and len(cursor_xy) >= 2:
        sources["cursor"] = (int(cursor_xy[0]), int(cursor_xy[1]))

    if len(sources) < 2:
        return {"consistent": True, "spread": 0, "tol": tol, "sources": sources,
                "detail": "可用源不足 2 个（%d 个），跳过旁证" % len(sources)}

    xs = [p[0] for p in sources.values()]
    ys = [p[1] for p in sources.values()]
    spread = max(max(xs) - min(xs), max(ys) - min(ys))
    ok = spread <= tol
    detail = ("三源一致（最大偏差 %dpx ≤ %dpx）" % (spread, tol) if ok else
              "⚠ 三源不一致：最大偏差 %dpx > %dpx —— 抓帧链路/客户区偏移/DPI "
              "至少一个不对；各源落点 %s" % (spread, tol, sources))
    return {"consistent": ok, "spread": spread, "tol": tol,
            "sources": sources, "detail": detail}
