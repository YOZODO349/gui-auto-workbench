# -*- coding: utf-8 -*-
"""异常黑匣子 —— 让「点了按钮没反应」变成**可复现的证据**。

## 为什么需要它

Tk 的回调异常默认只是往 stderr 打一行（无窗口环境下连这行都看不见），
**界面照旧、什么也不提示** —— 用户看到的现象就是「按钮点了没反应」。
测试脚本早就接管了 `report_callback_exception`（见 `tools/verify_stepops.py:31`），
**但生产窗口漏了**。本模块补上这一环。

## 三件事

1. `install_crash_handler(root, log_fn)` —— 接管 `tk.Tk.report_callback_exception`：
   任何 Tk 回调（按钮点击、事件绑定、`after` 回调）里逃逸的异常，
   都**带着时间戳、当前模式、完整 traceback** 追加写入 `logs/crash.txt`，
   同时回显到界面日志（标红）。
2. `guarded(fn)` —— 把任意回调包一层 try/except，异常时走同一条落盘 + 回显通道。
   用于**按钮 command**：即使 `report_callback_exception` 被别的东西覆盖，也兜得住。
3. `read_crash_tail(n)` —— 读最近若干行，供界面/自检脚本查看。

## 纪律

- **本模块不 import 任何项目内模块**（尤其不 import gui）—— 防循环依赖。
  它只依赖标准库 `os/sys/time/traceback/inspect`。
- **追加不覆盖**：`crash.txt` 持续累加（用户口径 1），但加体积上限保护，
  超过 `MAX_BYTES` 时**滚动**成 `crash.txt.1`，避免无上限膨胀。
- **不吞异常语义**：`guarded` 记完盘会返回一个哨兵值，调用方若要感知失败可自行判断；
  默认返回 `None`，与普通回调一致。
- **幂等**：重复安装/重复包裹不会重复写盘（哨兵属性）。
"""
from __future__ import annotations

import os
import sys
import time
import traceback

# `logs/` 与本模块同级目录的上一级（项目根）
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CRASH_DIR = os.path.join(_ROOT, "logs")
CRASH_PATH = os.path.join(CRASH_DIR, "crash.txt")
MAX_BYTES = 512 * 1024            # 512KB 后滚动，避免无限膨胀

_GUARD_FLAG = "__safeui_guarded__"  # guarded 幂等哨兵
_MAX_TRACEBACK = 8000              # 单条 traceback 上限（防超长刷屏）


# --------------------------------------------------------------------------- #
# 落盘
# --------------------------------------------------------------------------- #
def _rotate_if_needed() -> None:
    """超过上限就把 crash.txt 滚成 crash.txt.1（覆盖旧 .1）。"""
    try:
        if os.path.exists(CRASH_PATH) and os.path.getsize(CRASH_PATH) > MAX_BYTES:
            bak = CRASH_PATH + ".1"
            if os.path.exists(bak):
                os.remove(bak)
            os.replace(CRASH_PATH, bak)
    except OSError:
        pass                      # 滚动失败不能反过来把主流程炸了


def write_crash(text: str, context: str = "", title: str = "界面回调异常") -> str:
    """追加写一条崩溃记录，返回写入的正文（便于调用方直接回显）。

    格式：一条分隔线 + 时间戳/模式/上下文 + traceback。

    `title` 用来区分异常来源 —— **别让后台线程的异常顶着一张"界面回调"的脸**：
      后台执行异常（worker 线程）走的是消息队列，不是 Tk 回调；
      标成"界面回调异常"会让人排查时往错误的方向找。
    """
    try:
        os.makedirs(CRASH_DIR, exist_ok=True)
        _rotate_if_needed()
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        head = "=" * 72
        body = "%s\n[%s] 【%s】%s\n%s\n" % (
            head, stamp, title, ("模式=" + context) if context else "", text.rstrip())
        with open(CRASH_PATH, "a", encoding="utf-8") as f:
            f.write(body)
        return body
    except OSError:
        # 连盘都写不下去时，至少留下 stderr 痕迹
        sys.stderr.write("[safeui] 写 crash.txt 失败：\n%s\n" % text)
        return ""


def read_crash_tail(n: int = 40) -> str:
    """读 crash.txt 最后 n 行（文件不存在返回空串）。"""
    try:
        with open(CRASH_PATH, "r", encoding="utf-8", errors="replace") as f:
            return "".join(f.readlines()[-n:])
    except OSError:
        return ""


def crash_size() -> int:
    """crash.txt 当前字节数（不存在返回 0）—— 验证脚本用它断言「有没有增长」。"""
    try:
        return os.path.getsize(CRASH_PATH)
    except OSError:
        return 0


# --------------------------------------------------------------------------- #
# 安装 / 回显
# --------------------------------------------------------------------------- #
def install_crash_handler(root, log_fn=None, status_fn=None, mode_fn=None) -> bool:
    """接管 `tk.Tk.report_callback_exception`。

    - `log_fn(text, tag="err")` —— 回显到界面日志（标红）。
    - `status_fn()` —— 可选；异常时把底栏状态打红（如 `lambda: ...`）。
    - `mode_fn()` —— 可选；返回当前模式字符串，写进 crash.txt 抬头，便于定位。

    返回 True 表示已安装；重复安装是安全的（会替换为本模块的处理器）。
    """
    try:
        import tkinter as tk
    except Exception:
        return False

    def _repr_exc(exc, val, tb):        # noqa: ANN001
        try:
            text = "".join(traceback.format_exception(exc, val, tb))
        except Exception:
            text = "%r / %r" % (exc, val)
        if len(text) > _MAX_TRACEBACK:
            text = text[:_MAX_TRACEBACK] + "\n...（traceback 过长已截断）"
        ctx = ""
        if callable(mode_fn):
            try:
                ctx = str(mode_fn() or "")
            except Exception:
                ctx = ""
        body = write_crash(text, ctx)
        if callable(log_fn):
            try:
                log_fn("【界面回调异常 · 已写入 logs/crash.txt】\n%s" % text, "err")
            except Exception:
                pass
        if callable(status_fn):
            try:
                status_fn()
            except Exception:
                pass
        return body

    # 装在类上 —— 覆盖所有 Tk 实例；若别处已装（如验证脚本），本模块**后装生效**
    tk.Tk.report_callback_exception = _repr_exc
    return True


def is_installed() -> bool:
    """当前 `tk.Tk.report_callback_exception` 是不是本模块装的。"""
    try:
        import tkinter as tk
        fn = getattr(tk.Tk, "report_callback_exception", None)
        return getattr(fn, "__name__", "") == "_repr_exc"
    except Exception:
        return False


# --------------------------------------------------------------------------- #
# 装饰器
# --------------------------------------------------------------------------- #
def guarded(fn, context: str = "", log_fn=None):
    """把回调包一层：异常时落盘 + 回显，**不让异常再往外逃**。

    - 幂等：同一个函数被包两次，第二次直接返回原对象（哨兵属性）。
    - 返回 `None`（与 Tk 回调常态一致）；`fn` 正常返回值**原样透传**。
    """
    if getattr(fn, _GUARD_FLAG, False):
        return fn

    name = getattr(fn, "__name__", repr(fn))

    def _wrapped(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:                       # noqa: BLE001
            text = "".join(traceback.format_exception(
                type(exc), exc, exc.__traceback__))
            if len(text) > _MAX_TRACEBACK:
                text = text[:_MAX_TRACEBACK] + "\n...（traceback 过长已截断）"
            body = write_crash(text, context or name)
            cb = log_fn
            if cb is not None:
                try:
                    cb("【按钮回调异常 · 已写入 logs/crash.txt】\n%s" % text, "err")
                except Exception:
                    pass
            else:
                sys.stderr.write(body or text)
            return None

    try:
        _wrapped.__name__ = name
        _wrapped.__doc__ = getattr(fn, "__doc__", None)
    except Exception:
        pass
    setattr(_wrapped, _GUARD_FLAG, True)
    return _wrapped
