# -*- coding: utf-8 -*-
"""落盘：配置表、采集帧、模板。

**一切对外存相对值 0~1**（见 geometry），换机器、换窗口大小全靠它。
路径规则：
    config/profile.json      配置表（换目标程序只改这一张）
    data/frames/f01.png      这一步骤**程序采集到的帧**（裁模板、做互斥比对的底稿）
    assets/templates/s01.png 识别点裁出来的模板
"""
from __future__ import annotations

import copy
import json
import os
from typing import Optional

from . import util

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CONFIG_DIR = os.path.join(ROOT, "config")
FRAMES_DIR = os.path.join(ROOT, "data", "frames")
TEMPLATES_DIR = os.path.join(ROOT, "assets", "templates")
LOGS_DIR = os.path.join(ROOT, "logs")
PROFILE_PATH = os.path.join(CONFIG_DIR, "profile.json")

# 识别点裁框的默认尺寸（**以识别点为中心**，参考分辨率下）
DEFAULT_BOX_W = 160
DEFAULT_BOX_H = 44
MIN_BOX = 24                      # 比这还小就拒绝（点太偏/太贴边）

APP_NAME = "GUI 自动化工作台"


def default_profile() -> dict:
    return {
        "app": APP_NAME,
        "window": {
            # target: "window" = 认窗口（按下面关键字精确匹配）
            #         "screen" = **整个屏幕**，不认窗口
            #                    （全屏游戏 / 无边框程序 / 一个流程跨好几个窗口）
            "target": "window",
            # ⚠️ 填目标程序**真实标题**，精确匹配。别写自家程序名（见 skill 4.2）
            "title_keywords": [],
            "match_mode": "exact",
            "exclude": [APP_NAME],
        },
        "reference": {"w": 0, "h": 0},          # 第一张采集帧的尺寸，程序自己量
        "execution": {
            "mode": "dry",                       # dry = 只识别不点击（默认）
            "poll_interval_s": 0.3,
            "ready_timeout_s": 60.0,             # 起跑门控
            "verify_timeout_s": 3.0,
            "settle_s": 0.25,
            "retry_max": 2,                      # 换落点重试次数
            "back_max": 1,                       # 退回上一步的封顶
            "recover_limit": 5,                  # 恢复熔断
            "click_radius": 6,
            "return_home_wait_s": 15.0,
            "return_home_retries": 1,
        },
        "match": {
            "scales": [0.9, 1.0, 1.1],
            "threshold": 0.82,
            "search_margin_ratio": 0.6,
            "tone": {
                "hue_lo": 0, "hue_hi": 180, "s_min": 90,
                "score_min": 0.85, "cov_lo": 0.55, "cov_hi": 2.0,
                "iou_min": 0.45, "outside_max": 0.18,
            },
        },
        "steps": [],
    }


def _deep_default_fill(user: dict, base: dict) -> dict:
    """用默认值补齐缺失字段（老配置照样能读）。"""
    out = copy.deepcopy(base)
    if not isinstance(user, dict):
        return out
    for k, v in user.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_default_fill(v, out[k])
        else:
            out[k] = v
    return out


def normalize_steps(profile: dict) -> dict:
    """把 steps 里每条 action 的字段补齐、口径收敛（**老配置向后兼容的地方**）。

    为什么不能只靠 `_deep_default_fill`：它只递归**字典**，而 `steps` 是**列表**，
    列表会被整体替换 —— 于是"老配置缺 click_type"这种情况它补不到。
    所以单开一道，按 step 逐条规整。

    规则：
      · 缺 `action` → 造一个空 action
      · 缺 `click`  → 补 True（沿用旧语义：默认会点）
      · 缺 `click_type` 或值非法 → 收敛成 "single"（即旧行为 = 单击）
    这样"老数据照样读、行为完全不变"，是本功能不破坏已存数据的保证。
    """
    from . import picker
    for st in (profile.get("steps") or []):
        if not isinstance(st, dict):
            continue
        act = st.get("action")
        if not isinstance(act, dict):
            act = {}
            st["action"] = act
        act.setdefault("click", True)
        act["click_type"] = picker.norm_click_type(act.get("click_type"))
    return profile


def load_profile() -> dict:
    if not os.path.exists(PROFILE_PATH):
        p = default_profile()
        save_profile(p)
        return p
    try:
        with open(PROFILE_PATH, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception:
        raw = {}
    return normalize_steps(_deep_default_fill(raw, default_profile()))


def save_profile(profile: dict) -> bool:
    util.ensure_dir(CONFIG_DIR)
    tmp = PROFILE_PATH + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(profile, f, ensure_ascii=False, indent=2)
        os.replace(tmp, PROFILE_PATH)
        return True
    except Exception:
        return False


# --------------------------------------------------------------------------- #
# 帧与模板
# --------------------------------------------------------------------------- #
def frame_path(step_id: str) -> str:
    return os.path.join(FRAMES_DIR, "%s.png" % step_id)


def template_path(step_id: str) -> str:
    return os.path.join(TEMPLATES_DIR, "%s.png" % step_id)


def save_step_frame(step_id: str, img) -> bool:
    util.ensure_dir(FRAMES_DIR)
    return util.imwrite_unicode(frame_path(step_id), img)


def load_step_frame(step_id: str):
    return util.imread_unicode(frame_path(step_id))


def save_step_template(step_id: str, img) -> bool:
    util.ensure_dir(TEMPLATES_DIR)
    return util.imwrite_unicode(template_path(step_id), img)


def load_step_template(step_id: str):
    return util.imread_unicode(template_path(step_id))


def load_all_templates(profile: dict) -> dict:
    """{step_id: 模板图}。缺模板的步骤在自检里要能被发现。"""
    out = {}
    for st in profile.get("steps", []):
        sid = st.get("id")
        if not sid:
            continue
        img = load_step_template(sid)
        if img is not None:
            out[sid] = img
    return out


def next_step_id(profile: dict) -> str:
    """s01 / s02 / …（按现有最大号 +1，删掉中间步骤也不会撞号）。"""
    mx = 0
    for st in profile.get("steps", []):
        sid = str(st.get("id") or "")
        if sid.startswith("s") and sid[1:].isdigit():
            mx = max(mx, int(sid[1:]))
    return "s%02d" % (mx + 1)


def renumber_steps(profile: dict) -> int:
    """按当前列表顺序重排每个步骤的**显示编号**（第 1 步/第 2 步…）。

    为什么只动名字、不动 id：
    - `id`（s01/s02…）是数据主键 —— 帧文件、模板文件都按它命名，
      动了 id 就等于把已标好的素材全丢连接。
    - 用户看到的顺序只由**列表位置**决定。所以删除/插入之后，
      重排的应当是名字，而不是 id。

    返回改了几个名字。
    """
    changed = 0
    for i, st in enumerate(profile.get("steps", []), 1):
        want = "第 %d 步" % i
        if st.get("name") != want:
            st["name"] = want
            changed += 1
    return changed


def step_missing(step: dict) -> list:
    """这一步还缺什么（缺识别点或操作点都算不齐）。"""
    miss = []
    a = step.get("anchor") or {}
    if not a.get("point"):
        miss.append("识别点")
    if not a.get("box"):
        miss.append("识别框")
    if not a.get("template"):
        miss.append("模板")
    act = step.get("action") or {}
    if act.get("click", True) and not act.get("point"):
        miss.append("操作点")
    return miss


def is_screen_mode(profile: dict) -> bool:
    """**全屏模式**：不认窗口，抓主屏、坐标直接就是屏幕坐标。

    什么时候用：全屏游戏、无边框窗口、或者一个流程要跨好几个窗口。
    代价：屏幕上别的窗口会被一起抓进去 —— 跑之前自己把桌面收拾干净。
    """
    return (profile.get("window", {}).get("target") or "window") == "screen"


def target_desc(profile: dict) -> str:
    if is_screen_mode(profile):
        return "整个屏幕（不指定窗口）"
    kws = profile.get("window", {}).get("title_keywords") or []
    return "窗口「%s」" % kws[0] if kws else "（还没选目标）"


def profile_ready(profile: dict) -> tuple:
    """执行模式能不能开？（不能就指出缺哪一步）

    **放宽规则（随时预演）**：不再要求"全部步骤都标齐"——只要
    ① 有目标（认窗口或全屏）② **至少一步**数据齐全，就允许真跑。
    没标齐的步骤会被跳过并在日志里点名，而不是把整个执行模式锁死。
    理由见 skill：用户常常想先拿一两步试跑手感，硬卡"全齐"等于逼他先标完。
    """
    steps = profile.get("steps", [])
    if not steps:
        return False, "还没有任何步骤 —— 请先去编辑模式标一步"
    if not is_screen_mode(profile) and \
            not (profile.get("window", {}).get("title_keywords") or []):
        return False, "还没选目标窗口（或改用「整个屏幕」模式）"
    ready_n = sum(1 for st in steps if not step_missing(st))
    if ready_n == 0:
        bad = []
        for i, st in enumerate(steps, 1):
            m = step_missing(st)
            if m:
                bad.append("第%d步缺%s" % (i, "、".join(m)))
        return False, "一步都没标齐 —— " + "；".join(bad)
    return True, ""


def unfit_steps(profile: dict) -> list:
    """哪些步骤还没标齐（跑的时候会被跳过）。返回 [(序号, 缺什么), ...]。"""
    out = []
    for i, st in enumerate(profile.get("steps", []), 1):
        m = step_missing(st)
        if m:
            out.append((i, m))
    return out


def logs_path(name: str) -> str:
    util.ensure_dir(LOGS_DIR)
    return os.path.join(LOGS_DIR, name)
