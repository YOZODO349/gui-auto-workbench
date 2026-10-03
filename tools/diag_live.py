# -*- coding: utf-8 -*-
"""实跑诊断：完整走 `_live_run()`，看它到底卡/中止在哪一步。

做法：用**假 host** 顶替真实宿主 ——
  · `activate()` 恒 True（不真切窗口）
  · `capture()` 返回**真实抓一帧**（这样识别结果是真的）
  · `click()` 只记录、不动鼠标
然后跑 Runner，把 terminal / reason 打出来。

用途：用户说"点开始就出错"时，先用它把"中止在哪一环"钉死，
再对症下药 —— 比猜快得多。
"""
from __future__ import annotations

import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from src import storage, winio, engine  # noqa: E402


class DiagHost:
    """假宿主：抓真帧、不真点、不真切窗口。

    `frames=None` 时抓**真实屏幕**（用于复现"用户此刻屏幕上那个现场"）；
    给了 `frames` 就用固定图序列循环喂（用于**离线专测某条分支** ——
    比如"画面纹丝不动"时的弱验证行为，不必真去开目标程序）。
    """

    def __init__(self, log, frames=None):
        self.log = log
        self.clicks = []
        self.click_kinds = []
        self._frame = None
        self._frames = list(frames) if frames else None
        self._i = 0

    def activate(self):
        return True

    def capture(self):
        if self._frames:
            f = self._frames[min(self._i, len(self._frames) - 1)]
            self._i += 1
            return f
        if self._frame is None:
            winio.set_dpi_aware()
            self._frame = winio.capture_primary_screen()
        return self._frame

    def click(self, x, y, double=False):
        self.clicks.append((x, y))
        self.click_kinds.append("double" if double else "single")
        self.log("  [假点击] (%.0f, %.0f) %s" % (x, y, "双击" if double else "单击"))
        return True

    def sleep(self, s):
        import time as _t
        _t.sleep(min(s, 0.05))          # 加速：不真等

    def client_size(self):
        f = self.capture()
        return (f.shape[1], f.shape[0]) if f is not None else (0, 0)

    def save_frame(self, tag=""):
        self.log("  [假存图] %s" % tag)
        return None

    def frame_size(self):
        return self.client_size()

    def window_rect(self):
        return None

    def is_alive(self):
        return True

    def release(self):
        pass


def main():
    # `--still` = 离线专测「画面纹丝不动」时的弱验证分支：
    #   用夹具图序列（永远喂同一张 p1），不看真实屏幕、不依赖目标程序开着。
    still = "--still" in sys.argv

    if still:
        from tools import make_fixtures as fx          # noqa: E402
        from src import picker                         # noqa: E402
        data = fx.build_all()
        BOX = (storage.DEFAULT_BOX_W, storage.DEFAULT_BOX_H)
        REC_PT = (700, 82)
        frame = data["pages"]["p1"]
        st = {"id": "s01", "name": "第1步", "frame": "s01.png",
              "anchor": {}, "action": {}}
        picker.apply_to_step(st, frame, REC_PT, fx.PAGE_SPECS[0]["btn_pos"],
                             BOX, False, 6)
        st["action"]["click_type"] = "double"      # 模拟用户配的双击
        prof = storage.default_profile()
        prof["window"]["title_keywords"] = ["夹具目标程序"]
        prof["reference"] = {"w": fx.REF_W, "h": fx.REF_H}
        prof.setdefault("execution", {}).update({
            "ready_timeout_s": 2.0, "verify_timeout_s": 0.4,
            "poll_interval_s": 0.1, "retry_max": 1})
        prof["steps"] = [st]                       # 只有一步（用户的实际配置）
        tpls = {"s01": picker.cut_template(
            frame, picker.build_box(frame, REC_PT, BOX))}
        # 画面永远停在原页 → 弱验证"认不出动了"
        frames = [data["pages"]["p1"]] * 40
        print("模式 = 离线·画面纹丝不动（专测弱验证）")
    else:
        prof = storage.load_profile()
        tpls = storage.load_all_templates(prof)
        frames = None
        print("模式 = 真实屏幕")

    print("载入步骤 %d 个，模板 %d 个" % (len(prof.get("steps", [])), len(tpls)))
    print("目标 = %s　参考分辨率 = %s" % (
        storage.target_desc(prof), prof.get("reference")))
    print("-" * 60)

    def log(t, tag="info"):
        print("  [%s] %s" % (tag, t))

    host = DiagHost(log, frames=frames)

    # ⚠ 把门控超时调到 3 秒，别真等 60 秒
    prof.setdefault("execution", {})["ready_timeout_s"] = 3.0

    def on_event(kind, payload):
        if kind == "log":
            t, tag = payload
            print("  [%s] %s" % (tag, t))
        else:
            print("  <事件 %s> %s" % (kind, payload))

    r = engine.Runner(prof, host, templates=tpls, dry=False,
                      on_event=on_event)
    res = r.run()

    print("-" * 60)
    print("终态   :", res.get("terminal"))
    print("ok     :", res.get("ok"))
    print("原因   :", res.get("reason"))
    print("完成的步数:", res.get("done"), "/", res.get("total"))
    print("假点击数 :", len(host.clicks), host.click_kinds)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
