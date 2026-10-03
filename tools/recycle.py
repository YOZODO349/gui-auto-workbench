# -*- coding: utf-8 -*-
"""把路径移入**系统回收站**（可还原）。

为什么不用 rm：rm 是物理抹除，删错了没得救。回收站走的是 Windows 的
SHFileOperationW，自带还原入口 —— 对付「老程序目录」这种大件最稳。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import sys

FO_DELETE = 3
FOF_SILENT = 0x0004
FOF_NOCONFIRMATION = 0x0010
FOF_ALLOWUNDO = 0x0040      # 关键：允许撤销 → 进回收站
FOF_NOERRORUI = 0x0400
FOF_WANTNUKEWARNING = 0x4000


class SHFILEOPSTRUCTW(ctypes.Structure):
    _fields_ = [
        ("hwnd", wt.HWND),
        ("wFunc", wt.UINT),
        ("pFrom", wt.LPCWSTR),
        ("pTo", wt.LPCWSTR),
        ("fFlags", ctypes.c_uint16),
        ("fAnyOperationsAborted", wt.BOOL),
        ("hNameMappings", ctypes.c_void_p),
        ("lpszProgressTitle", wt.LPCWSTR),
    ]


def to_recycle_bin(path: str) -> tuple:
    """返回 (成功?, 说明)。"""
    path = os.path.abspath(path)
    if not os.path.exists(path):
        return False, "不存在，跳过"
    # pFrom 必须是**双 \0 结尾**的多字符串
    src = path + "\x00\x00"
    op = SHFILEOPSTRUCTW()
    op.hwnd = None
    op.wFunc = FO_DELETE
    op.pFrom = src
    op.pTo = None
    op.fFlags = (FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT
                 | FOF_NOERRORUI | FOF_WANTNUKEWARNING)
    try:
        r = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    except Exception as exc:
        return False, "调用失败：%r" % (exc,)
    if r != 0:
        return False, "系统返回码 %d" % r
    if op.fAnyOperationsAborted:
        return False, "操作被中止"
    return True, "已移入回收站"


def main(argv) -> int:
    if not argv:
        print("用法: recycle.py <路径> [路径...]")
        return 2
    bad = 0
    for p in argv:
        ok, why = to_recycle_bin(p)
        print("%s  %s  —— %s" % ("[OK] " if ok else "[FAIL]", p, why))
        if not ok:
            bad += 1
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
