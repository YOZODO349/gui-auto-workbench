# -*- coding: utf-8 -*-
"""窗口、截图、输入注入 —— 三条硬规矩都在这个文件里。

1. **前台激活**只调 `SetForegroundWindow` 会**静默失败**（Windows 只允许当前前台进程
   设置前台窗口）→ 必须借 `AttachThreadInput` 拿到前台线程的输入队列权限，**并且回来校验**。
   不校验的话，程序会带着"截到自己界面"的错误画面一路跑下去。
2. **DPI 感知**要在"建任何窗口之前"声明，而且**每个后台线程入口都得重申** ——
   UI 框架会把线程的 DPI 上下文改回 unaware，主线程明白人不等于后台线程也是。
3. 缺 pywin32 时，窗口查找会**静默返回 None 而不抛 ImportError** —— 开工先验一句 import。

读图/写图一律走 `util.imread_unicode`：`cv2.imread` 遇中文路径返回 None 且不报错。
"""
from __future__ import annotations

import ctypes
import time
from typing import Iterable, Optional, Tuple

import numpy as np

from . import util

try:
    import win32api
    import win32con
    import win32gui
    import win32process
    import win32ui
    HAVE_WIN32 = True
except Exception:                                   # pragma: no cover
    HAVE_WIN32 = False

Rect = Tuple[int, int, int, int]
_SW_RESTORE = 9

# 桌面壳窗口：枚举时要剔掉，否则会把"桌面"当成目标程序
_SHELL_CLASSES = {"Progman", "WorkerW", "Shell_TrayWnd", "Windows.UI.Core.CoreWindow"}


# --------------------------------------------------------------------------- #
# DPI
# --------------------------------------------------------------------------- #
def set_dpi_aware() -> None:
    """**每个**会碰坐标或截图的线程入口都要调一次。"""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)      # PER_MONITOR_DPI_AWARE
        return
    except Exception:
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def require_win32() -> None:
    if not HAVE_WIN32:
        raise RuntimeError(
            "缺 pywin32：窗口查找会静默返回 None，前台激活也做不了。"
            "先跑 .venv\\Scripts\\python.exe -m pip install pywin32"
        )


# --------------------------------------------------------------------------- #
# 窗口
# --------------------------------------------------------------------------- #
def list_visible_windows() -> list:
    """枚举**可见、有标题、无属主**的顶层窗口 → [(hwnd, title, cls)]。"""
    require_win32()
    out = []

    def _cb(hwnd, _):
        try:
            if not win32gui.IsWindowVisible(hwnd):
                return True
            title = win32gui.GetWindowText(hwnd)
            if not title or not title.strip():
                return True
            if win32gui.GetWindow(hwnd, win32con.GW_OWNER):
                return True                              # 有属主 = 对话框/子窗，不是主窗
            cls = win32gui.GetClassName(hwnd)
            if cls in _SHELL_CLASSES:
                return True
            out.append((hwnd, title, cls))
        except Exception:
            pass
        return True

    win32gui.EnumWindows(_cb, None)
    return out


def window_title(hwnd: int) -> str:
    require_win32()
    try:
        return win32gui.GetWindowText(hwnd)
    except Exception:
        return ""


def window_rect(hwnd: int) -> Rect:
    require_win32()
    l, t, r, b = win32gui.GetWindowRect(hwnd)
    return (l, t, r - l, b - t)


def client_rect_screen(hwnd: int) -> Rect:
    """客户区在**屏幕坐标**下的 (x, y, w, h) —— 截图与坐标换算都基于它。"""
    require_win32()
    l, t = win32gui.ClientToScreen(hwnd, (0, 0))
    _, _, w, h = win32gui.GetClientRect(hwnd)
    return (l, t, w, h)


def find_window(keywords: Iterable[str], mode: str = "exact",
                exclude: Iterable[str] = ()) -> Optional[int]:
    """按标题找窗口。

    `mode="exact"` 精确匹配（**推荐**）；`"contains"` 包含匹配。

    ⚠️ 关键字**别写自家程序的名字**（见 skill 4.2）：否则会命中自己的控制窗口，
    以及开在项目目录下的文件资源管理器 —— 两个都污染窗口选择。
    """
    kws = [k.strip() for k in keywords if k and k.strip()]
    exs = [e.strip() for e in exclude if e and e.strip()]
    if not kws:
        return None
    fallback = None
    for hwnd, title, _cls in list_visible_windows():
        if any(e in title for e in exs):
            continue
        if mode == "exact":
            hit = any(title.strip() == k for k in kws)
        else:
            hit = any(k in title for k in kws)
        if not hit:
            continue
        _x, _y, w, h = window_rect(hwnd)
        if w >= 200 and h >= 150:                        # 主窗不会太小
            return hwnd
        if fallback is None:
            fallback = hwnd
    return fallback


def is_iconic(hwnd: int) -> bool:
    require_win32()
    try:
        return bool(win32gui.IsIconic(hwnd))
    except Exception:
        return False


def minimize_self_window(hwnd: int, settle_s: float = 0.12) -> None:
    """点【开始】或采集画面之前，**先把自己收起来** —— 否则截到的是自己。"""
    require_win32()
    try:
        win32gui.ShowWindow(hwnd, win32con.SW_MINIMIZE)
    except Exception:
        return
    time.sleep(settle_s)


def activate(hwnd: int, timeout_s: float = 0.8) -> bool:
    """把目标窗口置前台。**返回值必须看** —— 系统仍可能静默拒绝。"""
    require_win32()
    if not hwnd or not win32gui.IsWindow(hwnd):
        return False

    # 1) 最小化态先还原，否则截到的是缩略图/黑帧
    if win32gui.IsIconic(hwnd):
        win32gui.ShowWindow(hwnd, _SW_RESTORE)
        time.sleep(0.12)

    # 2) 已经是前台就直接过
    if win32gui.GetForegroundWindow() == hwnd:
        return True

    # 3) 借用前台线程的输入队列权限（常规操作，不是 hack）
    t_fg = win32process.GetWindowThreadProcessId(win32gui.GetForegroundWindow())[0]
    t_me = win32api.GetCurrentThreadId()
    attached = False
    try:
        if t_fg and t_fg != t_me:
            win32process.AttachThreadInput(t_me, t_fg, True)
            attached = True
        win32gui.BringWindowToTop(hwnd)                  # Z 序
        win32gui.SetForegroundWindow(hwnd)               # 激活
    except Exception:
        pass
    finally:
        if attached:
            try:
                win32process.AttachThreadInput(t_me, t_fg, False)   # 必须摘掉
            except Exception:
                pass

    # 4) 校验 —— 不能省
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if win32gui.GetForegroundWindow() == hwnd:
            return True
        time.sleep(0.05)
    return win32gui.GetForegroundWindow() == hwnd


# --------------------------------------------------------------------------- #
# 截图
# --------------------------------------------------------------------------- #
def frame_is_usable(img) -> Tuple[bool, str]:
    """这张帧能不能交给用户去点？不能就说清为什么。"""
    if img is None:
        return False, "抓帧返回空（窗口可能不可见或已关闭）"
    if img.ndim != 3 or img.shape[2] != 3:
        return False, "帧格式不对（不是三通道彩色）"
    h, w = img.shape[:2]
    if w < 32 or h < 32:
        return False, "帧太小 %dx%d（窗口可能被最小化）" % (w, h)
    if float(img.std()) < 1.0:
        return False, "整帧近似纯色 —— 典型的黑帧/被遮挡"
    if float(img.mean()) < 3.0:
        return False, "整帧几乎全黑（D3D 游戏 BitBlt 的老毛病）"
    return True, ""


def _grab_screen(rect: Rect):
    """屏幕抓取（首选）。对 D3D/游戏窗口比 PrintWindow 靠谱。"""
    try:
        from PIL import ImageGrab
        import cv2
        l, t, w, h = rect
        if w <= 0 or h <= 0:
            return None
        im = ImageGrab.grab(bbox=(l, t, l + w, t + h), all_screens=True)
        arr = np.array(im)                       # RGB
        return cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
    except Exception:
        return None


def _print_window_client(hwnd: int):
    """PrintWindow 兜底（PW_RENDERFULLCONTENT），裁出客户区。"""
    try:
        import cv2
        cl, ct = win32gui.ClientToScreen(hwnd, (0, 0))
        wl, wt, wr, wb = win32gui.GetWindowRect(hwnd)
        win_w, win_h = wr - wl, wb - wt
        cw, ch = client_rect_screen(hwnd)[2:]
        if win_w <= 0 or win_h <= 0 or cw <= 0 or ch <= 0:
            return None

        hwnd_dc = win32gui.GetWindowDC(hwnd)
        mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
        save_dc = mfc_dc.CreateCompatibleDC()
        bmp = win32ui.CreateBitmap()
        bmp.CreateCompatibleBitmap(mfc_dc, win_w, win_h)
        save_dc.SelectObject(bmp)
        try:
            win32gui.PrintWindow(hwnd, save_dc.GetSafeHdc(), 2)
            buf = bmp.GetBitmapBits(True)
            full = np.frombuffer(buf, dtype=np.uint8).reshape(win_h, win_w, 4)
            full = cv2.cvtColor(full, cv2.COLOR_BGRA2BGR)
        finally:
            try:
                win32gui.DeleteObject(bmp.GetHandle())
            except Exception:
                pass
            save_dc.DeleteDC()
            mfc_dc.DeleteDC()
            win32gui.ReleaseDC(hwnd, hwnd_dc)

        ox, oy = cl - wl, ct - wt                 # 客户区在整窗图里的偏移
        sub = full[oy:oy + ch, ox:ox + cw]
        if sub.size == 0:
            return None
        return sub.copy()
    except Exception:
        return None


def capture_client(hwnd: int, method: str = "auto"):
    """抓目标窗口**客户区**一帧。

    `method`: `"auto"` 屏幕抓取优先、PrintWindow 兜底 / `"grab"` 只要屏幕抓取 /
    `"print"` 只用 PrintWindow。
    """
    require_win32()
    rect = client_rect_screen(hwnd)
    if rect[2] <= 0 or rect[3] <= 0:
        return None
    if method in ("auto", "grab"):
        img = _grab_screen(rect)
        if img is not None and frame_is_usable(img)[0]:
            return img
        if method == "grab":
            return None
    if method in ("auto", "print"):
        img = _print_window_client(hwnd)
        if img is not None and frame_is_usable(img)[0]:
            return img
    return None


def capture_screen_full():
    """整屏抓一帧（枚举窗口时逐个比对用）。"""
    try:
        from PIL import ImageGrab
        import cv2
        im = ImageGrab.grab(all_screens=True)
        return cv2.cvtColor(np.array(im), cv2.COLOR_RGB2BGR)
    except Exception:
        return None


def primary_screen_size() -> Tuple[int, int]:
    """主显示器尺寸（**仅供参考/兜底**）。

    ⚠️ 未声明 DPI 时 `GetSystemMetrics` 返回的是**逻辑尺寸**（缩放后），
    与抓屏拿到的**物理像素**不一致 —— 所以真正要尺寸时，
    **以抓到的帧为准**（见 `ScreenHost.client_size`），别拿这个去算坐标。
    """
    try:
        u = ctypes.windll.user32
        return (int(u.GetSystemMetrics(0)), int(u.GetSystemMetrics(1)))
    except Exception:
        return (0, 0)


def capture_primary_screen():
    """抓主显示器整屏 —— **全屏模式（不指定窗口）**用这个。

    尺寸**以抓到的图为准**（不由 GetSystemMetrics 推），这样即使 DPI 上下文有出入，
    帧尺寸与点击坐标也是同一套像素。

    为什么不做成"选桌面窗口（Progman）"：桌面壳窗口的客户区不是你以为的那块画面，
    它由 Explorer 托管、随时被别的窗口盖住，抓出来往往是壁纸或空白。
    直接抓屏幕才是"所见即所得"。
    """
    try:
        from PIL import ImageGrab
        import cv2
        im = ImageGrab.grab(all_screens=False)          # 主屏，边界交给 PIL 自己算
        return cv2.cvtColor(np.array(im), cv2.COLOR_RGB2BGR)
    except Exception:
        w, h = primary_screen_size()
        if w <= 0 or h <= 0:
            return None
        return _grab_screen((0, 0, w, h))


# --------------------------------------------------------------------------- #
# 输入注入
# --------------------------------------------------------------------------- #
ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", ctypes.c_long), ("dy", ctypes.c_long),
                ("mouseData", ctypes.c_ulong), ("dwFlags", ctypes.c_ulong),
                ("time", ctypes.c_ulong), ("dwExtraInfo", ULONG_PTR)]


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", ctypes.c_ushort), ("wScan", ctypes.c_ushort),
                ("dwFlags", ctypes.c_ulong), ("time", ctypes.c_ulong),
                ("dwExtraInfo", ULONG_PTR)]


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", ctypes.c_ulong), ("wParamL", ctypes.c_ushort),
                ("wParamH", ctypes.c_ushort)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", _MOUSEINPUT), ("ki", _KEYBDINPUT), ("hi", _HARDWAREINPUT)]


class _INPUT(ctypes.Structure):
    _fields_ = [("type", ctypes.c_ulong), ("u", _INPUTUNION)]


_INPUT_MOUSE = 1
_MOVE = 0x0001
_LEFTDOWN = 0x0002
_LEFTUP = 0x0004
_RIGHTDOWN = 0x0008
_RIGHTUP = 0x0010
_ABSOLUTE = 0x8000

# ⚠ 这个句柄专门用来拿**准确的** GetLastError：
#   `ctypes.windll.user32` 那种句柄下 `ctypes.get_last_error()` **恒为 0**，
#   拿它写错误提示会把人带偏（报"错误码 0"等于没报）。
try:
    _USER32_ERR = ctypes.WinDLL("user32", use_last_error=True)
    _SEND_OK = True
except Exception:                      # pragma: no cover
    _USER32_ERR = None
    _SEND_OK = False


def _send_input(count, ptr, size):
    """走带 `use_last_error` 的句柄调 SendInput；拿不到就退回 windll。"""
    if _SEND_OK:
        return _USER32_ERR.SendInput(count, ptr, size)
    return ctypes.windll.user32.SendInput(count, ptr, size)


# --------------------------------------------------------------------------- #
# ★ 鼠标注入的**降级链路**（真实事故：SendInput 返回 1 但光标纹丝不动）
# --------------------------------------------------------------------------- #
# 事故现场（2026-09-29）：
#   在用户机器上实测 —— 同一进程、同一权限下：
#     SetCursorPos        → 精确生效
#     mouse_event（相对/绝对）→ 生效
#     SendInput（相对/绝对）→ **返回 1（成功），但光标一动不动**
#   窗口站是 `WinSta0`、桌面是 `Default`（正常交互式桌面），也没有第三方安全软件挂钩。
#   也就是说：**`SendInput` 这台机器上被系统策略性忽略了**，而它的返回值
#   是骗人的（照样报 1）。光看返回值根本发现不了。
#
# 由此定下两条铁律（下面的 `_send_mouse` 严格执行）：
#   ① **不能只看返回值** —— 必须"注入后读一次光标来校验"（见 `_verify_move`）。
#   ② **必须有降级路径** —— `SendInput` 不灵就自动改走 `mouse_event`。
#      ▸ 移动类事件：走 `SetCursorPos`（最稳，且不受指针加速影响，落点更准）。
#      ▸ 按键类事件：走 `mouse_event` 的 DOWN/UP（保留完整的事件语义，
#        目标程序照样能收到 WM_LBUTTONDOWN / WM_LBUTTONUP）。
#      ⚠ 为什么移动不走 `mouse_event`：它受**指针加速曲线**影响
#        （实测 (500,500) 相对 +200,+100 落到了 (753,627)），落点会飘，不能用于精确定位。
_MOUSEEVENTF_MOVE = 0x0001
_MOUSEEVENTF_ABSOLUTE = 0x8000

# 上次注入是否发现"SendInput 无效" → 记住它，后续直接抄近路（省一次无用调用）。
# 只作**性能旁路**用，不改变正确性：即便标记为 True，也仍会走校验。
_USE_SENDINPUT = True


def _mouse_event(dwFlags: int, dx: int = 0, dy: int = 0,
                 data: int = 0, extra: int = 0) -> int:
    """`mouse_event` 兼容路径（SendInput 被忽略时的降级用）。"""
    try:
        return int(ctypes.windll.user32.mouse_event(
            ctypes.c_ulong(dwFlags), ctypes.c_ulong(dx), ctypes.c_ulong(dy),
            ctypes.c_ulong(data), ctypes.c_void_p(extra)))
    except Exception:                                   # pragma: no cover
        return 0


def _set_cursor_pos(x: int, y: int) -> bool:
    """移动光标的**最稳**路径（不受指针加速影响）。"""
    try:
        return bool(ctypes.windll.user32.SetCursorPos(int(x), int(y)))
    except Exception:                                   # pragma: no cover
        return False


def _is_move(flags: int) -> bool:
    return bool(flags & _MOVE)


def _send_mouse(flags: int, x: int = 0, y: int = 0) -> None:
    """绝对坐标注入 —— 屏幕左上为原点。

    这是**所有真实点击的唯一出口**。它踩过两个真实事故，都已在此处封死：

    ⚠⚠ **事故一（崩溃）：`dwExtraInfo` 必须传整数 `0`，绝不能传 `None`**。
      `dwExtraInfo` 的类型是 `ULONG_PTR`（64 位下 = `c_ulonglong`），
      ctypes 的整数类型**不接受 `None`** —— 传进去会当场抛
      `TypeError: 'NoneType' object cannot be interpreted as an integer`。
      而这一抛，就发生在**真实点击**的第一条指令上（先 `_MOVE`），
      于是：窗口消失 → 立刻抛错 → 后台线程兜住 → 界面弹回并显示红字，
      整个过程只要几毫秒（所以看起来"根本没识别就报错"）。
      **干跑永远不会触发**（它不调 click），所以这个问题只在勾上真实点击时暴露。

    ⚠⚠ **事故二（静默失效）：`SendInput` 返回 1，光标却纹丝不动**。
      本机实测：同进程同权限下 `SetCursorPos` / `mouse_event` 都生效，唯独
      `SendInput` 被系统忽略，**而且它照样返回 1**。所以：
        ▸ **不能只看返回值** —— 移动类事件必须"注入后读一次光标"来校验；
        ▸ **必须有降级路径** —— 不灵就自动改走 `SetCursorPos` / `mouse_event`。
      详见文件上方 `_USE_SENDINPUT` 一带的注释。

    ⚠ **归一化用 `GetSystemMetrics` 前必须确保进程是 DPI 感知的**：
      非感知上下文里它给的是**逻辑尺寸**（如 2560×1600 缩放 150% 时返回 1707×1067），
      拿它去归一化，鼠标会飞到**约 1.5 倍远处**（甚至飞出屏幕）。
      `set_dpi_aware()` 已在进程入口与后台线程入口调用，这里再兜一次。
    """
    global _USE_SENDINPUT
    set_dpi_aware()                     # 兜底：宁可多调一次（幂等）

    if _is_move(flags):
        # ---- 移动：优先 SendInput，但**必须校验**，不灵就改 SetCursorPos ----
        if _USE_SENDINPUT:
            user32 = ctypes.windll.user32
            vx = user32.GetSystemMetrics(0)
            vy = user32.GetSystemMetrics(1)
            nx = int(x * 65535 / max(1, vx - 1))
            ny = int(y * 65535 / max(1, vy - 1))
            inp = _INPUT(type=_INPUT_MOUSE,
                         u=_INPUTUNION(
                             mi=_MOUSEINPUT(nx, ny, 0, flags | _ABSOLUTE, 0, 0)))
            sent = _send_input(1, ctypes.byref(inp), ctypes.sizeof(_INPUT))
            if not sent:
                # 返回 0 = 一个事件都没进去。错误码要拿 use_last_error 的句柄才准。
                err = ctypes.get_last_error() if _SEND_OK else 0
                raise OSError("SendInput 注入失败（返回 0%s）："
                              "目标程序可能以更高权限运行（试试以管理员身份启动本程序）；"
                              "或当前桌面会话不可交互。"
                              % ("，GetLastError=%s" % err if err else ""))
            # ★ 关键一步：**别信返回的 1**，读一次光标看它到底动没动。
            if not _verify_move(x, y):
                # SendInput 在这台机器上被系统忽略了 → 永久改走兼容路径
                _USE_SENDINPUT = False
            else:
                return
        # 降级：SetCursorPos —— 最稳，且不受指针加速曲线影响（落点更准）
        _set_cursor_pos(x, y)
        return

    # ---- 按键（DOWN/UP）：也先 SendInput，无效则改 mouse_event ----
    # 注意：按键事件**无法用光标校验**（光标不动是正常的），所以这里是
    # "一次无效就换路"的启发式 —— 以 `_USE_SENDINPUT` 标记为准（移动校验会先把它置 False）。
    if _USE_SENDINPUT:
        user32 = ctypes.windll.user32
        vx = user32.GetSystemMetrics(0)
        vy = user32.GetSystemMetrics(1)
        nx = int(x * 65535 / max(1, vx - 1))
        ny = int(y * 65535 / max(1, vy - 1))
        inp = _INPUT(type=_INPUT_MOUSE,
                     u=_INPUTUNION(mi=_MOUSEINPUT(nx, ny, 0, flags | _ABSOLUTE, 0, 0)))
        sent = _send_input(1, ctypes.byref(inp), ctypes.sizeof(_INPUT))
        if sent:
            return
        _USE_SENDINPUT = False          # SendInput 连按键都进不去 → 换路
    # 降级：mouse_event。保留 MOVE 标记时的坐标语义（先移到位再按键）。
    if flags & _MOVE:
        # 绝对归一化是 mouse_event 的说法；但它有加速曲线，
        # 所以这里只用它保证"光标已在目标上"，最终位置仍由 SetCursorPos 兜定。
        _set_cursor_pos(x, y)
        flags = flags & ~_MOVE
    _mouse_event(flags)


def _verify_move(x: int, y: int, tol: int = 2) -> bool:
    """注入后读一次光标，判断"到底动没动到目标附近"。

    ★ 存在的理由：`SendInput` 会**返回 1 却毫无效果**（本机实测）。
      没有这个校验，"点了但没生效"会一路装成成功，最后表现成
      "程序说点了、鼠标没动"这种无从排查的怪象。

    `tol` 给 2px 容差：系统对绝对坐标的取整可能差 1px。
    """
    try:
        import ctypes.wintypes as wt
        pt = wt.POINT()
        ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
        return abs(int(pt.x) - int(x)) <= tol and abs(int(pt.y) - int(y)) <= tol
    except Exception:                                   # pragma: no cover
        # 读不到坐标就别据此做判断（宁可当作成功，避免误判成"注入无效"而乱降级）
        return True


def click_abs(x: int, y: int, double: bool = False, button: str = "left") -> None:
    """在**屏幕绝对坐标**点一下。

    ⚠ 流程是 **先移到位、再按、再抬**，且**每次按键都不带 `_MOVE`**：
      带 `_MOVE` 的话，DOWN/UP 时系统又要按绝对坐标重算一遍位置 ——
      既多余，又会在"注入被降级"时把光标来回拽（用户会看到鼠标抽搐）。
      先把位置坐稳（`move_abs`，带校验与降级），后面只发纯按键事件。
    """
    if button == "right":
        down, up = _RIGHTDOWN, _RIGHTUP
    else:
        down, up = _LEFTDOWN, _LEFTUP
    move_abs(x, y)                      # ① 先到点（内部含校验 + 降级）
    time.sleep(0.02)
    _send_mouse(down)                   # ② 按下
    time.sleep(0.03)
    _send_mouse(up)                     # ③ 抬起
    if double:
        # ⚠ 双击的两次间隔：太短会被系统合成"单击"（丢掉双击语义），
        #   太长则超过默认双击时限（GetDoubleClickTime，通常 500ms）。
        #   60ms 落在两者之间，且必须在**同一位置**（所以不再移动）。
        time.sleep(0.06)
        _send_mouse(down)
        time.sleep(0.03)
        _send_mouse(up)


def move_abs(x: int, y: int) -> None:
    """把光标移到屏幕绝对坐标（含注入校验与降级，见 `_send_mouse`）。"""
    _send_mouse(_MOVE, x, y)


def cursor_pos() -> Tuple[int, int]:
    pt = ctypes.wintypes.POINT() if hasattr(ctypes, "wintypes") else None
    try:
        import ctypes.wintypes as wt
        pt = wt.POINT()
        ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
        return (pt.x, pt.y)
    except Exception:
        return (0, 0)


def probe_injection() -> dict:
    """**动手前先探一次**：鼠标注入链路到底通不通、用的哪条路。

    返回 `{ok, how, detail}`：
      `ok`     —— 注入是否真的生效（以"光标是否动到目标"为准，不看返回值）
      `how`    —— 生效的方式：`"SendInput"` / `"SetCursorPos"` / `"none"`
      `detail` —— 给用户看的一句话说明

    ★ **为什么要探**：`SendInput` 会**返回成功却毫无效果**（本机实测）。
      不探的话，用户会看到"程序说点了、鼠标没动"，只能靠猜。
      探一次（先存光标 → 注入 → 读回 → 还原光标）只要几毫秒，
      却能提前把"链路不通"这种环境问题**摆到台面上**。

    ⚠ 探测会**真的动一下鼠标**（几十毫秒内还原），且**不产生点击**（只移动）。
    """
    set_dpi_aware()
    try:
        ox, oy = cursor_pos()
    except Exception:                                   # pragma: no cover
        ox, oy = 0, 0

    # 选一个离当前位置够远的落点，避免"本来就在那"造成假阳性
    vx = ctypes.windll.user32.GetSystemMetrics(0)
    vy = ctypes.windll.user32.GetSystemMetrics(1)
    tx = 40 if ox > vx // 2 else max(0, vx - 40)
    ty = 40 if oy > vy // 2 else max(0, vy - 40)

    how, ok = "none", False
    try:
        # ① 先单独试 SendInput（绕开降级），看它是否真生效
        global _USE_SENDINPUT
        keep = _USE_SENDINPUT
        _USE_SENDINPUT = True
        nx = int(tx * 65535 / max(1, vx - 1))
        ny = int(ty * 65535 / max(1, vy - 1))
        user32 = ctypes.windll.user32
        inp = _INPUT(type=_INPUT_MOUSE,
                     u=_INPUTUNION(
                         mi=_MOUSEINPUT(nx, ny, 0, _MOVE | _ABSOLUTE, 0, 0)))
        sent = _send_input(1, ctypes.byref(inp), ctypes.sizeof(_INPUT))
        time.sleep(0.03)
        px, py = cursor_pos()
        si_ok = bool(sent) and abs(px - tx) <= 2 and abs(py - ty) <= 2
        _USE_SENDINPUT = si_ok          # 探明结果直接写进全局标记
        if si_ok:
            how, ok = "SendInput", True
        elif not keep:
            # 本来就已经是降级模式，不必再试
            pass
        # ② SendInput 不灵 → 试 SetCursorPos（降级路径）
        if not si_ok:
            _set_cursor_pos(tx, ty)
            time.sleep(0.03)
            px, py = cursor_pos()
            if abs(px - tx) <= 2 and abs(py - ty) <= 2:
                how, ok = "SetCursorPos", True
    except Exception:                                   # pragma: no cover
        pass
    finally:
        # 还原光标（别把用户的鼠标留在角落里）
        try:
            _set_cursor_pos(ox, oy)
        except Exception:                               # pragma: no cover
            pass

    if ok and how == "SendInput":
        detail = "鼠标注入正常（SendInput）"
    elif ok:
        detail = ("鼠标注入可用，但系统的 SendInput 被忽略、已自动改走 SetCursorPos —— "
                  "功能不受影响，可正常使用。")
    else:
        detail = ("**鼠标注入不可用**：程序发出的点击到不了系统。"
                  "常见原因：目标程序以管理员权限运行而本程序不是（或反之）。"
                  "可试试以管理员身份启动本程序；或先手动点一下目标窗口再执行。")
    return {"ok": ok, "how": how, "detail": detail}


def hotkey_pressed(vk: int) -> bool:
    """热键是否按下（用于 F8 抓帧兜底）。"""
    try:
        return bool(ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000)
    except Exception:
        return False
