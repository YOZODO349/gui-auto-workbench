# -*- coding: utf-8 -*-
"""界面偏好持久化：主题等"跟人走、不跟数据走"的开关。

## 为什么单开一个文件，不塞进 `config/profile.json`

`profile.json` 是**工作台草稿**（步骤 / 目标窗口 / 匹配参数），
是"能跑的自动化数据"；主题是**界面口味**。
混在一起有两个坏处（P7 已经把仓库与草稿分开过一次，同一条教训）：

1. 换一次主题 = 动一次工作台配置 → `verify_all.py` 的"落盘零污染"契约直接失效；
2. 用户清掉工作台数据（或换台机器搬脚本）时，把界面口味也一并带走。

所以：`config/ui.json` 只管界面，**什么都不读 `profile.json`**（有 `tokenize` 断言守着）。
"""
from __future__ import annotations

import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "config", "ui.json")

DEFAULTS = {"theme": "light", "hud_hotkey": "Escape"}


def load() -> dict:
    """读界面偏好；文件坏了/没有都**退回默认值**，绝不抛 —— 启动不能被它拦住。"""
    data = dict(DEFAULTS)
    try:
        with open(PATH, encoding="utf-8") as f:
            got = json.load(f)
        if isinstance(got, dict):
            for k in DEFAULTS:
                if k in got:
                    data[k] = got[k]
    except Exception:                                           # noqa: BLE001
        pass
    return data


def save(data: dict) -> bool:
    """原子写（先临时文件再替换）：写一半崩了不会留下半截 json。"""
    try:
        out = dict(DEFAULTS)
        out.update({k: v for k, v in (data or {}).items() if k in DEFAULTS})
        os.makedirs(os.path.dirname(PATH), exist_ok=True)
        tmp = PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        os.replace(tmp, PATH)
        return True
    except Exception:                                           # noqa: BLE001
        return False
