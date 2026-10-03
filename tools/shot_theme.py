# -*- coding: utf-8 -*-
"""换肤验收：浅色 → 设置栏 → 深色，各拍一张实拍图 + 采样像素。

用法：  .venv\\Scripts\\python.exe tools\\shot_theme.py
为什么不用肉眼看图：颜色这类事 `cget` 会说谎（见 `_scrollbar` 的注释 ——
经典滚动条在 Windows 上颜色被 UxTheme 静默吞掉）。所以**采像素**才算数。
"""
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("AWB_NO_EXEC", "1")

import gui  # noqa: E402
from src import tokens  # noqa: E402

OUT = os.path.join(ROOT, "说明文档", "UI_20261003")
os.makedirs(OUT, exist_ok=True)


def shoot(win, path):
    win.update_idletasks()
    for _ in range(10):
        win.update()
    time.sleep(0.6)
    for _ in range(6):
        win.update()
    x, y = win.winfo_rootx(), win.winfo_rooty()
    w, h = win.winfo_width(), win.winfo_height()
    ps = ("Add-Type -AssemblyName System.Drawing;"
          "$bmp = New-Object System.Drawing.Bitmap(%d,%d);"
          "$g = [System.Drawing.Graphics]::FromImage($bmp);"
          "$g.CopyFromScreen(%d,%d,0,0,$bmp.Size);"
          "$bmp.Save('%s');" % (w, h, x, y, path.replace("\\", "/")))
    subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                   capture_output=True)
    st = os.stat(path)
    print("  已存 %s（%dx%d，%d 字节）" % (os.path.basename(path), w, h, st.st_size))
    return path


def px(path, pct_x, pct_y):
    """采一个像素的 RGB（用 PowerShell 读位图，避免额外依赖）。"""
    from PIL import Image
    im = Image.open(path).convert("RGB")
    return im.getpixel((int(im.width * pct_x), int(im.height * pct_y)))


app = gui.App()
app.geometry("1240x880")
app.update_idletasks()
app.lift()
app.attributes("-topmost", True)

print("① 启动主题 = %s（config/ui.json 决定）" % tokens.THEME_NAME)
p1 = shoot(app, os.path.join(OUT, "主题_浅色_主窗.png"))

print("② 打开设置栏")
win = gui.SettingsWindow(app)
win.lift()
win.attributes("-topmost", True)
p2 = shoot(win, os.path.join(OUT, "主题_设置栏_浅色.png"))

print("③ 点【深色】单选（走真实控件，不是直接调函数）")
win.var_theme.set("dark")
win._pick("dark")
app.lift()
app.attributes("-topmost", True)
p3 = shoot(app, os.path.join(OUT, "主题_深色_主窗.png"))
win.lift()
win.attributes("-topmost", True)
p4 = shoot(win, os.path.join(OUT, "主题_设置栏_深色.png"))

print("④ 像素采样（画布 / 面板 / 日志底）")
print("   浅色画布 %s  面板 %s  日志 %s" % (px(p1, 0.01, 0.13), px(p1, 0.5, 0.06), px(p1, 0.5, 0.9)))
print("   深色画布 %s  面板 %s  日志 %s" % (px(p3, 0.01, 0.13), px(p3, 0.5, 0.06), px(p3, 0.5, 0.9)))

print("⑤ 切回浅色（把用户偏好留回默认，不给下次启动埋惊喜）")
app._set_theme("light")
print("   config/ui.json 现在 =", tokens.THEME_NAME)
win.close()
app.destroy()
