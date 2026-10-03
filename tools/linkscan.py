# -*- coding: utf-8 -*-
"""扫描：每个 skill 入口是「真目录」还是「Junction 指向总仓」。

为什么要它：`os.path.islink` 认不出 Windows junction；而 junction 和真目录在
资源管理器里长得一模一样。不查清楚，就会出现「以为是两份副本、其实是一份」的误判
（本项目就踩过这个坑）。

判据：GetFileAttributesW 的 FILE_ATTRIBUTE_REPARSE_POINT (0x400) 位。
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


def is_reparse(p: str) -> bool:
    a = GPA(p)
    return a != INVALID and bool(a & FILE_ATTRIBUTE_REPARSE_POINT)


def main(argv) -> int:
    if len(argv) < 1:
        print("用法: linkscan.py <目录> [总仓目录]")
        return 2
    root = os.path.abspath(argv[0])
    repo = os.path.abspath(argv[1]) if len(argv) > 1 else None

    if not os.path.isdir(root):
        print("目录不存在：%s" % root)
        return 1

    real, links, orphan = [], [], []
    for name in sorted(os.listdir(root)):
        full = os.path.join(root, name)
        if not os.path.isdir(full):
            continue
        if is_reparse(full):
            tgt = os.path.realpath(full)
            alive = os.path.isdir(tgt)
            links.append((name, tgt, alive))
            if not alive:
                orphan.append(name)
        else:
            real.append(name)

    print("== 真目录（自己就是正主）%d 个 ==" % len(real))
    for n in real:
        print("   %s" % n)

    print()
    print("== Junction（指向别处）%d 个 ==" % len(links))
    for n, tgt, alive in links:
        flag = "OK " if alive else "断链!"
        print("   [%s] %-34s -> %s" % (flag, n, tgt))

    # 反查：总仓里有哪些 skill 没人指过来
    if repo and os.path.isdir(repo):
        pointed = {os.path.basename(t.rstrip("\\/")) for _n, t, _a in links}
        repodirs = [d for d in sorted(os.listdir(repo))
                    if os.path.isdir(os.path.join(repo, d))]
        missing = [d for d in repodirs if d not in pointed and d not in real]
        print()
        print("== 总仓里有、但入口处没有对应条目的 %d 个 ==" % len(missing))
        for d in missing:
            print("   %s" % d)

    if orphan:
        print()
        print("!! 断链（指向的目标不存在）：%s" % ", ".join(orphan))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
