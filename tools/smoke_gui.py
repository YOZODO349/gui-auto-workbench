# -*- coding: utf-8 -*-
"""冒烟：真的把窗口开起来，确认没有构造期异常，再自己关掉。

注意：**GUI 进程的标准输出重定向不可靠**，所以这里把子进程输出落成 UTF-8 文件再读，
不指望从管道里捞（沙箱与编码两头都容易失手）。
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main(wait: float = 5.0) -> int:
    log = os.path.join(ROOT, "logs", "smoke_gui.txt")
    os.makedirs(os.path.dirname(log), exist_ok=True)
    p = subprocess.Popen([sys.executable, os.path.join(ROOT, "gui.py")],
                         cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    t0 = time.time()
    while time.time() - t0 < wait:
        if p.poll() is not None:
            break
        time.sleep(0.2)
    alive = p.poll() is None
    out = b""
    if alive:
        p.terminate()
        try:
            out = p.communicate(timeout=5)[0] or b""
        except Exception:
            out = b""
    else:
        try:
            out = p.stdout.read() if p.stdout else b""
        except Exception:
            out = b""
    text = (out or b"").decode("utf-8", "replace")
    with open(log, "w", encoding="utf-8") as f:
        f.write(text)
    print("窗口活着 %.1f 秒，%s" % (time.time() - t0,
                                  "仍在运行（已由测试关掉）" if alive else "自己退出了"))
    if text.strip():
        print("—— 子进程输出 ——")
        print(text[-3000:])
    return 0 if alive else 1


if __name__ == "__main__":
    sys.exit(main())
