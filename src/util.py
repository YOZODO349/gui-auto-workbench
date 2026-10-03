# -*- coding: utf-8 -*-
"""小工具：非 ASCII 路径读写图、目录保证、时间戳。

**为什么单独拎出来**：`cv2.imread` 在中文/非 ASCII 路径上直接返回 None，
而且不报错 —— 静默失败最耗人。全项目读图只走这里。
"""
from __future__ import annotations

import os
import time

import cv2
import numpy as np


def ensure_dir(path: str) -> str:
    if path:
        os.makedirs(path, exist_ok=True)
    return path


def imread_unicode(path: str):
    """读图（支持中文路径）。读不到返回 None。"""
    if not path or not os.path.exists(path):
        return None
    try:
        data = np.fromfile(path, dtype=np.uint8)
    except OSError:
        return None
    if data.size == 0:
        return None
    return cv2.imdecode(data, cv2.IMREAD_COLOR)


def imwrite_unicode(path: str, img) -> bool:
    """写图（支持中文路径）。"""
    if img is None:
        return False
    ext = os.path.splitext(path)[1] or ".png"
    ok, buf = cv2.imencode(ext, img)
    if not ok:
        return False
    ensure_dir(os.path.dirname(os.path.abspath(path)))
    try:
        buf.tofile(path)
    except OSError:
        return False
    return True


def stamp() -> str:
    return time.strftime("%Y%m%d_%H%M%S")


def human_size(n: int) -> str:
    for unit in ("B", "KB", "MB"):
        if n < 1024:
            return f"{n:.0f}{unit}"
        n /= 1024.0
    return f"{n:.1f}GB"
