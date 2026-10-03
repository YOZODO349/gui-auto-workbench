# -*- coding: utf-8 -*-
"""实拍主窗（用于 UI 前后对照）。

用法：  .venv\\Scripts\\python.exe tools\\shot_main.py [宽 高] [输出文件]
"""
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("AWB_NO_EXEC", "1")

import gui  # noqa: E402

W = int(sys.argv[1]) if len(sys.argv) > 1 else 1240
H = int(sys.argv[2]) if len(sys.argv) > 2 else 880
OUT = sys.argv[3] if len(sys.argv) > 3 else os.path.join(ROOT, "logs", "_shot_main.png")

app = gui.App()
app.geometry("%dx%d" % (W, H))
app.update_idletasks()
app.lift()
app.attributes("-topmost", True)
for _ in range(10):
    app.update()
time.sleep(1.0)
for _ in range(6):
    app.update()

x, y = app.winfo_rootx(), app.winfo_rooty()
w, h = app.winfo_width(), app.winfo_height()
print("窗口几何 x=%d y=%d w=%d h=%d" % (x, y, w, h))

ps = (
    "Add-Type -AssemblyName System.Drawing;"
    "$bmp = New-Object System.Drawing.Bitmap(%d,%d);"
    "$g = [System.Drawing.Graphics]::FromImage($bmp);"
    "$g.CopyFromScreen(%d,%d,0,0,$bmp.Size);"
    "$bmp.Save('%s');" % (w, h, x, y, OUT.replace("\\", "/"))
)
r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True)
print("截图退出码", r.returncode)
print("已存" if os.path.exists(OUT) else "失败", OUT)
app.destroy()
