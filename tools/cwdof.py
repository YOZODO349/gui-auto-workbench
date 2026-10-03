# -*- coding: utf-8 -*-
"""查进程的**当前工作目录**（cwd）—— 用来分清哪个 gui.py 属于哪个项目。

命令行只写了 `gui.py`（相对路径），光看命令行分不出来。
cwd 藏在 PEB 里：OpenProcess → NtQueryInformationProcess → PEB → ProcessParameters
→ CurrentDirectory。有点绕，但比"猜"可靠得多。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import sys

PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010

k32 = ctypes.windll.kernel32
ntdll = ctypes.windll.ntdll


class UNICODE_STRING(ctypes.Structure):
    _fields_ = [("Length", wt.USHORT),
                ("MaximumLength", wt.USHORT),
                ("Buffer", ctypes.c_void_p)]


class CURDIR(ctypes.Structure):
    _fields_ = [("DosPath", UNICODE_STRING),
                ("Handle", wt.HANDLE)]


class RTL_USER_PROCESS_PARAMETERS(ctypes.Structure):
    _fields_ = [
        ("MaximumLength", wt.ULONG),
        ("Length", wt.ULONG),
        ("Flags", wt.ULONG),
        ("DebugFlags", wt.ULONG),
        ("ConsoleHandle", wt.HANDLE),
        ("ConsoleFlags", wt.ULONG),
        ("StandardInput", wt.HANDLE),
        ("StandardOutput", wt.HANDLE),
        ("StandardError", wt.HANDLE),
        ("CurrentDirectory", CURDIR),
        ("DllPath", UNICODE_STRING),
        ("ImagePathName", UNICODE_STRING),
        ("CommandLine", UNICODE_STRING),
        ("Environment", ctypes.c_void_p),
    ]


class PROCESS_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [("Reserved1", ctypes.c_void_p),
                ("PebBaseAddress", ctypes.c_void_p),
                ("Reserved2", ctypes.c_void_p * 2),
                ("UniqueProcessId", ctypes.c_void_p),
                ("Reserved3", ctypes.c_void_p)]


def read_mem(h, addr, size):
    buf = ctypes.create_string_buffer(size)
    n = ctypes.c_size_t()
    ok = k32.ReadProcessMemory(h, ctypes.c_void_p(addr), buf, size, ctypes.byref(n))
    if not ok:
        return None
    return buf.raw


def cwd_of(pid: int):
    h = k32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
    if not h:
        return None, "打不开进程（权限不足或已退出）"
    try:
        pbi = PROCESS_BASIC_INFORMATION()
        r = ntdll.NtQueryInformationProcess(h, 0, ctypes.byref(pbi),
                                            ctypes.sizeof(pbi), None)
        if r != 0 or not pbi.PebBaseAddress:
            return None, "拿不到 PEB（错误码 %d）" % r
        # PEB->ProcessParameters 偏移：64 位 = 0x20
        pp_ptr = read_mem(h, pbi.PebBaseAddress + 0x20, 8)
        if not pp_ptr:
            return None, "读不到 ProcessParameters 指针"
        pp_addr = int.from_bytes(pp_ptr, "little")
        raw = read_mem(h, pp_addr, ctypes.sizeof(RTL_USER_PROCESS_PARAMETERS))
        if not raw:
            return None, "读不到 ProcessParameters 内容"
        params = RTL_USER_PROCESS_PARAMETERS.from_buffer_copy(raw)
        us = params.CurrentDirectory.DosPath
        if not us.Buffer or not us.Length:
            return None, "cwd 为空"
        data = read_mem(h, us.Buffer, us.Length)
        if not data:
            return None, "读不到 cwd 字符串"
        return data.decode("utf-16-le", "replace"), None
    finally:
        k32.CloseHandle(h)


def main(argv) -> int:
    if not argv:
        print("用法: cwdof.py <pid> [pid...]")
        return 2
    for a in argv:
        pid = int(a)
        cwd, err = cwd_of(pid)
        print("PID %-7d cwd = %s%s" % (pid, cwd if cwd else "?", ("  (%s)" % err) if err else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
