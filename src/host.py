# -*- coding: utf-8 -*-
"""宿主（Host）：把"跟真实系统打交道的那几件事"抽出来。

引擎**只认这个接口**，于是同一套状态机既能跑真机，也能在离线帧序列上整链自测 ——
这是能不能做"反向对照"的前提（见 skill 第 7 章）：
没有离线宿主，就没法证明"喂第 2 页的图，判别结果必须判成第 2 页"。

接口：capture / click / activate / client_size / sleep / save_frame
"""
from __future__ import annotations

import os
import time
from typing import Callable, List, Optional

from . import storage, util, winio


class BaseHost:
    def capture(self):
        raise NotImplementedError

    def click(self, x: int, y: int, double: bool = False) -> None:
        raise NotImplementedError

    def activate(self) -> bool:
        return True

    def client_size(self):
        """(宽, 高) —— 相对坐标换算的依据。"""
        raise NotImplementedError

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)

    def now(self) -> float:
        """时间源。真机就是墙上时钟；离线宿主会换成**虚拟时间**，测试才跑得快。"""
        return time.time()

    def save_frame(self, name: str, img) -> str:
        path = storage.logs_path(name)
        util.imwrite_unicode(path, img)
        return path


class LiveHost(BaseHost):
    """真机：认窗口 → 置前台 → 抓客户区 → 注入点击。"""

    def __init__(self, hwnd: int, log: Optional[Callable] = None):
        winio.require_win32()
        winio.set_dpi_aware()               # 线程入口重申 DPI：不然尺寸/坐标会错位
        self.hwnd = hwnd
        self._log = log or (lambda *_a, **_k: None)

    # --- 窗口 ---
    def activate(self) -> bool:
        ok = winio.activate(self.hwnd)
        if not ok:
            self._log("切不到目标窗口前台 —— 请手动点一下它", "err")
        return ok

    def window_title(self) -> str:
        return winio.window_title(self.hwnd)

    def client_size(self):
        _x, _y, w, h = winio.client_rect_screen(self.hwnd)
        return (w, h)

    # --- 画面 ---
    def capture(self):
        img = winio.capture_client(self.hwnd)
        if img is None:
            ok, why = winio.frame_is_usable(None)
            self._log("抓帧失败：%s" % why, "warn")
        return img

    def capture_checked(self, require_front: bool = True):
        """抓一帧并校验 —— 采集/识别都走这个，**绝不拿一张错帧往下走**。"""
        if require_front:
            fg = winio.activate(self.hwnd)
            if not fg:
                return None, "切不到目标窗口前台"
        img = self.capture()
        ok, why = winio.frame_is_usable(img)
        return (img, "") if ok else (None, why)

    # --- 输入 ---
    def click(self, x: int, y: int, double: bool = False) -> None:
        # 点击前校验窗口几何：窗口被移过/缩过就拒绝点（见 4.5）
        rect = winio.client_rect_screen(self.hwnd)
        cx, cy = rect[0] + x, rect[1] + y
        winio.click_abs(cx, cy, double=double)

    def click_screen(self, sx: int, sy: int, double: bool = False) -> None:
        winio.click_abs(sx, sy, double=double)


class ScreenHost(BaseHost):
    """**全屏模式**：不认窗口，抓主屏、直接注入屏幕坐标。

    帧内坐标**就是**屏幕坐标（左上角为 (0,0)），所以 click 不需要任何偏移换算 ——
    这也是它比"认窗口"省事的地方。

    适合：全屏游戏、无边框窗口、一个流程跨好几个窗口。
    代价：屏幕上别的窗口会被一起抓进去，跑之前自己把桌面收拾干净。

    为什么不去抓"桌面窗口（Progman）"：那个窗口的客户区不是你以为的画面，
    它由 Explorer 托管、随时被盖住，抓出来往往是壁纸或空白。直接抓屏幕才所见即所得。
    """

    def __init__(self, log: Optional[Callable] = None):
        winio.require_win32()
        winio.set_dpi_aware()               # 线程入口重申 DPI：不然尺寸/坐标会错位
        self._log = log or (lambda *_a, **_k: None)
        self._size = None

    def activate(self) -> bool:
        # 没有目标窗口要激活 —— 靠的是"自己先最小化"让开画面
        return True

    def window_title(self) -> str:
        return "（整个屏幕）"

    def client_size(self):
        """**以实际抓到的帧为准**（缓存一次）。

        不能拿 `GetSystemMetrics` 去算：DPI 上下文有出入时它给的是**逻辑尺寸**，
        和抓屏的**物理像素**不是同一套 —— 那样换算出来的落点就会偏。
        """
        if self._size is None:
            f = self.capture()
            self._size = ((f.shape[1], f.shape[0]) if f is not None
                          else winio.primary_screen_size())
        return self._size

    def capture(self):
        img = winio.capture_primary_screen()
        if img is None:
            self._log("抓屏失败（屏幕尺寸取不到？）", "warn")
        return img

    def capture_checked(self, require_front: bool = True):
        img = self.capture()
        ok, why = winio.frame_is_usable(img)
        return (img, "") if ok else (None, why)

    def click(self, x: int, y: int, double: bool = False) -> None:
        winio.click_abs(x, y, double=double)

    def click_screen(self, sx: int, sy: int, double: bool = False) -> None:
        winio.click_abs(sx, sy, double=double)


def create_live_host(profile: dict, log: Optional[Callable] = None):
    """按配置造宿主：**全屏模式** 或 **认窗口模式**。

    返回 None 表示"认窗口模式但找不到那个窗口"（全屏模式永远能造出来）。
    """
    if storage.is_screen_mode(profile):
        return ScreenHost(log)
    w = profile.get("window", {})
    hwnd = winio.find_window(w.get("title_keywords", []),
                             w.get("match_mode", "exact"),
                             w.get("exclude", []))
    if not hwnd:
        return None
    return LiveHost(hwnd, log)


class OfflineHost(BaseHost):
    """离线：按脚本喂帧、记录点击。**整链自测与反向对照都靠它**。

    `frames`  — 帧序列（ndarray）
    `advance_every` — 点几次才推进一帧（>1 用来模拟"点了没反应"，测重试闸门）
    `on_click` — 可选回调 (x, y, n) 决定推进几张；返回 None 用默认
    """

    def __init__(self, frames: List, advance_every: int = 1,
                 activate_ok: bool = True, on_click: Optional[Callable] = None,
                 tick: float = 0.05):
        self.frames = list(frames)
        self.idx = 0
        self.clicks: List[tuple] = []      # (x, y)，只记落点（老口径，别动）
        self.click_kinds: List[str] = []   # 与 clicks 一一对应："single"/"double"
        self.advance_every = max(1, int(advance_every))
        self.activate_ok = activate_ok
        self.on_click = on_click
        self._click_n = 0
        self._t = 0.0
        self.tick = float(tick)          # 每次问时间就往前走一点 —— 超时逻辑照样成立

    # --- 虚拟时间：离线测试别真等几十秒 ---
    def now(self) -> float:
        self._t += self.tick
        return self._t

    def sleep(self, seconds: float) -> None:
        self._t += max(0.0, float(seconds))

    # --- 画面 ---
    def capture(self):
        if not self.frames:
            return None
        return self.frames[min(self.idx, len(self.frames) - 1)]

    def client_size(self):
        f = self.capture()
        if f is None:
            return (0, 0)
        return (f.shape[1], f.shape[0])

    # --- 输入 ---
    def click(self, x: int, y: int, double: bool = False) -> None:
        self._click_n += 1
        self.clicks.append((x, y))
        # 单独记一条类型流水：自测要能反证"双击确实分派出去了"，
        # 只记落点的话，单击/双击在离线宿主里长得一模一样。
        self.click_kinds.append("double" if double else "single")
        if self.on_click is not None:
            nxt = self.on_click(x, y, self._click_n)
            if nxt is not None:
                self.idx = max(0, min(len(self.frames) - 1, int(nxt)))
                return
        if self._click_n % self.advance_every == 0:
            self.idx = min(len(self.frames) - 1, self.idx + 1)

    def activate(self) -> bool:
        return self.activate_ok

    def push_frame(self, frame) -> None:
        self.frames.append(frame)

    def goto(self, i: int) -> None:
        self.idx = max(0, min(len(self.frames) - 1, int(i)))

    def save_frame(self, name: str, img) -> str:
        return name
