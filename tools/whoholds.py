# -*- coding: utf-8 -*-
"""查 python 进程的可执行路径与命令行，用来分清「老程序的残留进程」。

wmic 在这台机器上没输出（可能被安全策略拦），改用 PowerShell 的 CIM 接口，
但 **不经过 COM 实例化**，走的是 Get-CimInstance 命令 —— 与 WScript.Shell 那条路不同。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

PS = r"""
$out = @()
Get-CimInstance Win32_Process -Filter "Name like '%python%'" | ForEach-Object {
  $out += [pscustomobject]@{
    pid = $_.ProcessId
    name = $_.Name
    exe = $_.ExecutablePath
    cmd = $_.CommandLine
  }
}
$out | ConvertTo-Json -Depth 3
"""


def main() -> int:
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", PS],
                       capture_output=True, text=True, timeout=90)
    print("RET:", r.returncode)
    out = (r.stdout or "").strip()
    err = (r.stderr or "").strip()
    if err:
        print("ERR:", err[:800])
    if not out:
        print("（无输出）")
        return 1
    try:
        data = json.loads(out)
    except Exception:
        print("原始输出：")
        print(out[:3000])
        return 1
    if isinstance(data, dict):
        data = [data]
    for d in data:
        print("PID %-7s %-12s" % (d.get("pid"), d.get("name")))
        print("    exe: %s" % d.get("exe"))
        print("    cmd: %s" % (d.get("cmd") or "")[:220])
    return 0


if __name__ == "__main__":
    sys.exit(main())
