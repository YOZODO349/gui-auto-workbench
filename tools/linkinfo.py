# -*- coding: utf-8 -*-
"""查一个路径是不是符号链接/junction，以及它指向哪。

`os.path.islink` 认不出 Windows 的 junction（那是 reparse point，另一种东西），
所以用 `GetFileAttributesW` 直接读 reparse 标志位 + `FILE_ATTRIBUTE_DIRECTORY`。
"""
from __future__ import annotations

import ctypes
import os
import sys

FILE_ATTRIBUTE_REPARSE_POINT = 0x0400
FILE_ATTRIBUTE_DIRECTORY = 0x0010

GPA = ctypes.windll.kernel32.GetFileAttributesW
GPA.argtypes = [ctypes.c_wchar_p]
GPA.restype = ctypes.c_uint32
INVALID = 0xFFFFFFFF


def describe(p: str):
    p = os.path.abspath(p)
    print("路径   : %s" % p)
    print("存在   : %s" % os.path.exists(p))
    a = GPA(p)
    if a == INVALID:
        print("  属性读取失败（可能不存在）")
        return
    print("  属性 : 0x%08X" % a)
    print("  目录 : %s" % bool(a & FILE_ATTRIBUTE_DIRECTORY))
    print("  重解析点(链接): %s" % bool(a & FILE_ATTRIBUTE_REPARSE_POINT))
    print("  os.path.islink : %s" % os.path.islink(p))
    print("  解析后真实路径 : %s" % os.path.realpath(p))
    # junction/symlink 目标探测
    try:
        import subprocess
        r = subprocess.run(["cmd", "/c", "dir", "/al", os.path.dirname(p)],
                           capture_output=True, text=True, timeout=30,
                           encoding="gbk", errors="replace")
        base = os.path.basename(p)
        for line in (r.stdout or "").splitlines():
            if base in line:
                print("  dir /al 行: %s" % line.strip())
    except Exception as exc:
        print("  dir 探测失败: %r" % (exc,))
    print()


def main(argv) -> int:
    for p in (argv or []):
        describe(p)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
