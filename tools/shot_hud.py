# -*- coding: utf-8 -*-
"""实拍取点遮罩上的中央提示（用于人工核对"够不够醒目"）。

用法：  .venv\\Scripts\\python.exe tools\\shot_hud.py
"""
import os
import subprocess
import sys
import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("AWB_NO_EXEC", "1")

import gui  # noqa: E402

OUT = os.path.join(ROOT, "说明文档", "UI_20261003")
os.makedirs(OUT, exist_ok=True)


def fake_screen(w, h):
    """造一张"像截图"的假帧：左半浅底深字、右半深底浅字 —— 一张图看清两种背景。"""
    img = np.zeros((h, w, 3), dtype=np.uint8)
    img[:, : w // 2] = (240, 240, 238)          # 浅
    img[:, w // 2:] = (34, 36, 42)              # 深
    for i in range(0, h, 24):
        img[i:i + 2, : w // 2] = (205, 205, 202)
        img[i:i + 2, w // 2:] = (52, 55, 62)
    # 几块"按钮/文字条"，让画面不至于纯色
    img[h // 4:h // 4 + 46, w // 8: w // 8 + 300] = (60, 60, 66)
    img[h // 2: h // 2 + 26, w // 8: w // 8 + 420] = (120, 120, 128)
    img[h // 3: h // 3 + 40, w - w // 8 - 260: w - w // 8] = (90, 200, 140)
    img[h - h // 4: h - h // 4 + 40, w - w // 8 - 320: w - w // 8] = (224, 85, 107)
    return img


app = gui.App()
app.update()
frame = fake_screen(app.winfo_screenwidth(), app.winfo_screenheight())
ov = gui.PickOverlay(app, frame, (0, 0, app.winfo_screenwidth(),
                                  app.winfo_screenheight()),
                     "第 1 步", False, lambda a, b: None, lambda: None)
ov.update()
time.sleep(0.8)
for _ in range(6):
    ov.update()

vw, vh = ov.vw, ov.vh
print("虚拟屏 %dx%d，提示键 = %s" % (vw, vh, ov.hud_hotkey))


def shoot(path, cw=1000, ch=380):
    x, y = vw // 2 - cw // 2, vh // 2 - ch // 2
    ps = ("Add-Type -AssemblyName System.Drawing;"
          "$bmp = New-Object System.Drawing.Bitmap(%d,%d);"
          "$g = [System.Drawing.Graphics]::FromImage($bmp);"
          "$g.CopyFromScreen(%d,%d,0,0,$bmp.Size);"
          "$bmp.Save('%s');" % (cw, ch, x, y, path.replace("\\", "/")))
    subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                   capture_output=True)
    print("  已存", os.path.basename(path),
          os.path.getsize(path) if os.path.exists(path) else "失败")


shoot(os.path.join(OUT, "取点提示_新版_显示.png"))
ov._toggle_hud()
ov.update()
time.sleep(0.4)
for _ in range(4):
    ov.update()
shoot(os.path.join(OUT, "取点提示_新版_隐藏.png"))
ov._teardown()
app.destroy()
