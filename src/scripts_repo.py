# -*- coding: utf-8 -*-
"""脚本仓库 —— 已编好的自动化脚本的**集中存放处**。

## 它和 `storage.py` 是什么关系

两者都是"落盘层"，但**管的是两件不同的事**，所以分开放：

    storage.py        管 **当前正在编辑的那一套步骤**（config/profile.json）
                      —— 它是"工作台台面"，同一时刻只有一份
    scripts_repo.py   管 **攒下来的历史脚本**（config/scripts.json）
                      —— 它是"抽屉"，可以有很多份，随时取出来接着改

★ **为什么必须是两个文件**（而不是塞进 profile.json 一个数组里）：
  1. `profile.json` 是执行引擎的**唯一输入**，字段口径被引擎、`verify_*` 一堆
     质量门盯着（它一被改动，`verify_all.py` 的"零污染"校验就会红）。
     往里塞一个仓库数组，等于把"台面"和"抽屉"焊死，任何一次仓库读写
     都会变成"改了执行配置"。
  2. 仓库是**用户资产**，工作台是**临时状态**。分开存才能做到
     "清掉工作台不影响仓库、换台机器只搬仓库"。
  → 所以本模块**只写 `config/scripts.json`**，一次都不写 `profile.json`。
    （`_copy_assets` 里 import `storage` 只是借用帧/模板的**路径计算**，
      不读也不写 profile —— 帧与模板是仓库自己的素材。）

## 数据结构

```json
{
  "version": 2,
  "scripts": [
    {
      "id": "sc01",                    # 主键，仓库内部唯一，删了不复用
      "name": "每日签到",               # 显示名（用户可改，重名允许）
      "desc": "打开游戏→点签到→领奖",   # 简介，列表里那一行
      "steps": [                       # ★ 脚本正文 = **步骤列表**（点选生成，不用手写）
        {
          "id": "sc01-01",             # 步骤主键，带脚本前缀（见下）
          "name": "第 1 步",
          "frame": "sc01-01.png",      # 采集帧：data/frames/sc01-01.png
          "anchor": {"point": [0.5, 0.3], "box": [...], "template": "sc01-01.png", ...},
          "action": {"point": [...], "radius": 6, "same_as_anchor": false,
                     "click": true, "click_type": "single"}
        }
      ],
      "cfg": {                         # 脚本自带的参数（参考分辨率 / 匹配阈值 …）
        "reference": {"w": 1920, "h": 1080},
        "match": {"scales": [0.9, 1.0, 1.1], "threshold": 0.82, "search_margin_ratio": 0.6},
        "execution": {"click_radius": 6}
      },
      "created_at": "2026-09-29 20:05:00",
      "updated_at": "2026-09-29 20:31:00"
    }
  ]
}
```

## ★ 为什么脚本正文是"步骤列表"而不是一段文本

用户**不写代码**：一条脚本 = 若干步，每一步都是"程序抓帧 → 在静止的帧上点识别点
和操作点 → 当场复核 → 保存"这条**点选链路**生成的。步骤里存的全是相对值（0~1）
和模板文件名，跟主窗工作台的步骤**同一个结构** —— 所以"取出来接着改"不用做任何
格式转换。

## ★ 步骤 id 为什么带脚本前缀（`sc01-01`）

帧文件与模板文件都按步骤 id 命名（`data/frames/<id>.png`）。两条脚本若都从 `s01`
开始编号，第二条会把第一条的素材**直接覆盖** —— 用户看到的是"我改了 A，B 也变了"。
带上脚本前缀，各脚本的素材天然隔离。
（`id` 前缀同时也解释了为什么"另存为"必须重编步骤 id：副本若不换 id，
  编辑副本就是在改原脚本的素材。）

## 三条纪律

1. **时间戳一律字符串 `YYYY-MM-DD HH:MM:SS`**（本地时间）。
   不用 `time.time()` 数字：数字在 JSON 里人不可读，用户手翻配置文件时看不懂；
   而且排序要的只是"同一个格式能比大小"，字符串比大小完全够用。
   ★ 格式固定成**定长零填充**，所以 `sorted()` 直接就是时间序 —— 不需要解析。
2. **id 分配只增不复用**（`sc01`/`sc02`… 取"历史最大号 + 1"，删除不回收号）。
   复用号会让"用户以为删干净了、其实新脚本顶着旧号"—— 排查时极易看错。
   ⚠ 这跟 `storage.next_step_id` 的策略**故意不同**：那边是步骤主键，
   跟帧文件绑定，按"现有最大号"取号能防止误删后编号打架；这边是纯文本记录，
   没有任何外部文件按 id 命名，所以"只增不复用"更安全。
3. **写盘用"临时文件 + `os.replace`"原子替换**（与 `storage.save_profile` 同一手法）。
   直接覆写时若中途断电/被杀，会留下半截 JSON —— 那等于用户资产全丢。
"""
from __future__ import annotations

import copy
import json
import os
import shutil
import time
from typing import Optional

from . import util

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_DIR = os.path.join(ROOT, "config")
REPO_PATH = os.path.join(CONFIG_DIR, "scripts.json")

# 时间戳格式：**定长零填充**，所以字符串序 == 时间序（见模块头纪律 1）
TS_FMT = "%Y-%m-%d %H:%M:%S"

# 排序方式（界面下拉用）
SORT_UPDATED_DESC = "updated_desc"
SORT_UPDATED_ASC = "updated_asc"
SORT_CREATED_DESC = "created_desc"
SORT_NAME_ASC = "name_asc"

SORT_LABELS = [
    ("最近更新在前", SORT_UPDATED_DESC),
    ("最早更新在前", SORT_UPDATED_ASC),
    ("最近创建在前", SORT_CREATED_DESC),
    ("按名称 A→Z", SORT_NAME_ASC),
]
SORT_LABEL_BY_KEY = dict((k, v) for v, k in SORT_LABELS)

# ⚠ 只在"老记录 / 坏记录缺 cfg"时兜底：正常记录在建的时候会从工作台拷一份过来。
#   这里的数值必须**与 `storage.default_profile()` 的对应字段一致** ——
#   不直接 `from . import storage` 取，是为了守住"本模块只写 scripts.json"这条线；
#   代价是要人工保持同步，所以在此处写明出处，改那边时记得改这里。
DEFAULT_CFG = {
    "reference": {"w": 0, "h": 0},
    "match": {"scales": [0.9, 1.0, 1.1],
              "threshold": 0.82,
              "search_margin_ratio": 0.6},
    "execution": {"click_radius": 6},
}


def now_ts() -> str:
    return time.strftime(TS_FMT, time.localtime())


def default_repo() -> dict:
    return {"version": 2, "scripts": []}


# --------------------------------------------------------------------------- #
# 读写
# --------------------------------------------------------------------------- #
def load_repo() -> dict:
    """读仓库。**永不抛异常** —— 文件坏了就当空的（宁可少给，不可崩界面）。

    ⚠ 与 `storage.load_profile()` 有意不同：那边读坏了会拿默认值覆盖写回，
      这边**不写回** —— 仓库是用户资产，读失败时贸然覆盖会把残留数据抹掉。
      （真坏了应该让用户自己看到文件、或者从备份恢复，而不是被我们悄悄清空。）

    ⚠ `content` 是**老版本**的"手写正文"字段：新脚本不再产生它，但老记录里可能
      有用户真打出来的内容，所以**照读、照存、绝不丢**（界面里作为只读备注显示）。
    """
    if not os.path.exists(REPO_PATH):
        return default_repo()
    try:
        with open(REPO_PATH, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception:
        return default_repo()
    if not isinstance(raw, dict):
        return default_repo()
    out = default_repo()
    out["version"] = raw.get("version", 2)
    scripts = raw.get("scripts")
    if isinstance(scripts, list):
        # 逐条规整：缺字段补上、类型不对的丢掉（老/坏数据照样能读）
        for s in scripts:
            if not isinstance(s, dict) or not s.get("id"):
                continue
            steps = s.get("steps")
            out["scripts"].append({
                "id": str(s["id"]),
                "name": str(s.get("name") or "（未命名）"),
                "desc": str(s.get("desc") or ""),
                "content": str(s.get("content") or ""),   # 老字段，只读保留
                "steps": [st for st in steps if isinstance(st, dict)]
                         if isinstance(steps, list) else [],
                "cfg": dict(s["cfg"]) if isinstance(s.get("cfg"), dict) else {},
                "created_at": str(s.get("created_at") or ""),
                "updated_at": str(s.get("updated_at") or ""),
            })
    return out


def save_repo(repo: dict) -> bool:
    """原子写盘（临时文件 + replace）。返回是否成功。"""
    util.ensure_dir(CONFIG_DIR)
    tmp = REPO_PATH + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(repo, f, ensure_ascii=False, indent=2)
        os.replace(tmp, REPO_PATH)
        return True
    except Exception:
        return False


# --------------------------------------------------------------------------- #
# 查询
# --------------------------------------------------------------------------- #
def next_script_id(repo: dict) -> str:
    """`sc01` / `sc02` / … —— 取**历史最大号 + 1**，删除不回收号（见模块头纪律 2）。"""
    mx = 0
    for s in repo.get("scripts", []):
        sid = str(s.get("id") or "")
        if sid.startswith("sc") and sid[2:].isdigit():
            mx = max(mx, int(sid[2:]))
    return "sc%02d" % (mx + 1)


def find(repo: dict, sid: str) -> Optional[dict]:
    """按 id 找一条。找不到返回 `None`（**不抛异常** —— 列表可能刚被别处改过）。"""
    for s in repo.get("scripts", []):
        if s.get("id") == sid:
            return s
    return None


def query(repo: dict, keyword: str = "", sort: str = SORT_UPDATED_DESC) -> list:
    """按名称**搜索** + **排序**，返回新列表（不改原仓库）。

    `keyword`：空 = 不过滤；否则按**名称**做不区分大小写的子串匹配。
      ★ 只搜名称、**不搜正文**：需求写的是"按名称搜索"。
        搜正文会把"内容里恰好提过这个词"的全捞出来，反而让用户找不到他要的那条
        （而且正则/敏感词都进正文会变慢），语义也跑偏了。
    `sort`：见 `SORT_*` 常量。

    ⚠ 排序前**先复制**（`list(...)`）—— 直接 `sort` 原列表会把仓库的顺序改掉，
      随后一次落盘用户就发现"文件里的顺序莫名其妙变了"。
    """
    items = list(repo.get("scripts", []))
    kw = (keyword or "").strip().lower()
    if kw:
        items = [s for s in items if kw in (s.get("name") or "").lower()]

    if sort == SORT_UPDATED_ASC:
        items.sort(key=lambda s: s.get("updated_at") or "")
    elif sort == SORT_CREATED_DESC:
        items.sort(key=lambda s: s.get("created_at") or "", reverse=True)
    elif sort == SORT_NAME_ASC:
        # 名称序用 `lower()` 兜底：中文按 Unicode 排（够用），
        # 英文大小写混排时不出现"A a B"这种反直觉顺序。
        items.sort(key=lambda s: (s.get("name") or "").lower())
    else:                                   # 默认 = SORT_UPDATED_DESC
        items.sort(key=lambda s: s.get("updated_at") or "", reverse=True)
    return items


def brief(script: dict, width: int = 40) -> str:
    """列表里显示的"简介"那一行。兜底顺序：**简介 → 老正文首行 → 步骤概况**。

    ★ 为什么要兜底：用户新建脚本时常常懒得写简介 —— 那一行要是空的，
      列表就变成"只有名字"的光秃秃一串，认不出内容。
      截正文首行 / 给"共 N 步"都比留空有用得多。截断加 `…` 表明"后面还有"。
    """
    d = (script.get("desc") or "").strip()
    if not d:
        for line in (script.get("content") or "").splitlines():
            line = line.strip()
            if line:
                d = line
                break
    if not d:
        steps = script.get("steps") or []
        d = ("共 %d 步" % len(steps)) if steps else "（还没标步骤）"
        return d
    d = d.replace("\n", " ").replace("\r", " ").strip()
    return d if len(d) <= width else d[:width - 1] + "…"


def count(repo: dict) -> int:
    return len(repo.get("scripts", []))


# --------------------------------------------------------------------------- #
# 步骤（脚本正文）
# --------------------------------------------------------------------------- #
def steps_of(script: dict) -> list:
    """脚本的步骤列表（**原地**：返回的就是这个脚本自己的 list，改它即改脚本）。

    ★ 必须返回**同一个 list 对象**，不能返回副本：调用方（采集链路）会往里
      `append` / 改元素，副本上的改动永远不会出现在脚本里 ——
      那正是"改了没生效"这种最难查的 bug。
    """
    out = script.get("steps")
    if not isinstance(out, list):
        out = []
        script["steps"] = out
    return out


def cfg_of(script: dict) -> dict:
    """给采集 / 复核用的"参数视图"：`steps` + `reference` + `match` + `execution`。

    ★ 为什么每份脚本要**自带**一份 cfg（而不是借用工作台那份）：
      参考分辨率是"这条脚本第一张采集帧"量出来的 —— 写进 `profile.json`
      就是污染执行配置；不写、每次临时借工作台的，则两台不同分辨率的机器上
      标出来的框会互相打架。

    ⚠ 返回的是**浅拷贝**：`out["reference"]` 与 `script["cfg"]["reference"]`
      是**同一个 dict 对象**，复核时往里写的参考分辨率会**留在脚本里**，
      下次读盘还在。写成深拷贝就没有这个效果（改完丢了，框会越裁越偏）。
    """
    cfg = script.get("cfg")
    if not isinstance(cfg, dict):
        cfg = {}
        script["cfg"] = cfg
    for k, v in DEFAULT_CFG.items():
        if not isinstance(cfg.get(k), dict):
            cfg[k] = copy.deepcopy(v)
    out = dict(cfg)
    out["steps"] = steps_of(script)
    return out


def next_step_id(script: dict) -> str:
    """`<脚本id>-01` / `-02` … —— 带脚本前缀，各脚本素材天然隔离。"""
    prefix = "%s-" % (script.get("id") or "sc")
    mx = 0
    for st in steps_of(script):
        sid = str(st.get("id") or "")
        if sid.startswith(prefix) and sid[len(prefix):].isdigit():
            mx = max(mx, int(sid[len(prefix):]))
    return "%s%02d" % (prefix, mx + 1)


def new_step(script: dict, idx: int = None) -> dict:
    """造一个**空步骤**（还没标识别点）。名字随后由 `renumber_steps` 统一重排。"""
    n = len(steps_of(script)) + 1 if idx is None else idx
    sid = next_step_id(script)
    return {
        "id": sid,
        "name": "第 %d 步" % n,
        "frame": "%s.png" % sid,
        "anchor": {},
        "action": {"point": None, "radius": 6,
                   "same_as_anchor": False, "click": True},
    }


def add_step(script: dict, at: int = None) -> dict:
    """追加（或插入）一步并重排名字，返回这一步。"""
    steps = steps_of(script)
    at = len(steps) if at is None else max(0, min(len(steps), at))
    st = new_step(script, at + 1)
    steps.insert(at, st)
    renumber_steps(script)
    return st


def renumber_steps(script: dict) -> int:
    """按当前顺序重排步骤的**显示编号**（第 1 步/第 2 步…）。

    ⚠ 只动名字、**不动 id**：id 是数据主键，帧文件与模板文件都按它命名，
      动了 id 就等于把已标好的素材全丢连接。
    """
    changed = 0
    for i, st in enumerate(steps_of(script), 1):
        want = "第 %d 步" % i
        if st.get("name") != want:
            st["name"] = want
            changed += 1
    return changed


def _copy_assets(old_id: str, new_id: str) -> int:
    """另存为时把帧 / 模板**复制**一份到新 id（返回复制了几个文件）。

    ★ 为什么必须复制：副本的步骤 id 是新编的，若仍指向原脚本的素材文件，
      那么"在副本里重标第 1 步"会**覆盖原脚本的模板** ——
      用户看到的是"我改了副本，原脚本的识别也变了"。
    素材不存在就跳过（那一步本来就没标过），不报错。
    """
    from . import storage                    # 只借**路径计算**，不碰 profile
    n = 0
    for src, dst in ((storage.frame_path(old_id), storage.frame_path(new_id)),
                     (storage.template_path(old_id), storage.template_path(new_id))):
        try:
            if os.path.exists(src):
                util.ensure_dir(os.path.dirname(dst))
                shutil.copy2(src, dst)
                n += 1
        except Exception:
            pass
    return n


# --------------------------------------------------------------------------- #
# 增 / 改 / 删（**都在传进来的 repo 上原地改，由调用方决定何时落盘**）
# --------------------------------------------------------------------------- #
def _adopt_steps(item: dict, src_steps) -> int:
    """把一批**外来步骤**收编进 `item`：逐条重编 id、改素材文件名、复制素材。

    「外来」= 不是这条脚本原有的步骤。两个调用方：
      · `duplicate`（另存为）—— 来源是同仓库另一条脚本的步骤
      · `import_steps`（工作台存入仓库）—— 来源是 `profile.json` 里的步骤

    ★ 为什么抽成一个函数：这两条路的正确性**全押在两个动作上** ——
      「重编 id」与「复制素材」。各写一份的话早晚有一处被改漏，
      而漏掉的后果（两条脚本共用同名素材，改一个动两个）要过很久才暴露，
      正是 `_copy_assets` 注释里记下的那类事故。
    ★ `copy.deepcopy` 在**函数内部**做：调用方把自己的步骤对象传进来是安全的，
      收编过程中怎么改都不会回写调用方 —— 工作台的台面原样不动。
    """
    n = 0
    for st in copy.deepcopy(list(src_steps or [])):
        if not isinstance(st, dict):
            continue
        old_id = st.get("id")
        new_id = next_step_id(item)
        st["id"] = new_id
        st["frame"] = "%s.png" % new_id
        anchor = st.get("anchor")
        if isinstance(anchor, dict) and anchor.get("template"):
            anchor["template"] = "%s.png" % new_id
        steps_of(item).append(st)
        if old_id:
            _copy_assets(old_id, new_id)
        n += 1
    return n


def create(repo: dict, name: str, desc: str = "", content: str = "",
           cfg: dict = None) -> dict:
    """新建一条并加入仓库，返回它（**不落盘** —— 由调用方 `save_repo`）。

    `cfg`：脚本自带的参数（参考分辨率 / 匹配阈值）。**由界面从工作台拷一份进来** ——
      本模块不认识工作台，也不该认识。
    """
    ts = now_ts()
    item = {
        "id": next_script_id(repo),
        "name": (name or "").strip() or "（未命名脚本）",
        "desc": desc or "",
        "content": content or "",
        "steps": [],
        "cfg": copy.deepcopy(cfg) if isinstance(cfg, dict)
               else copy.deepcopy(DEFAULT_CFG),
        "created_at": ts,
        "updated_at": ts,
    }
    repo.setdefault("scripts", []).append(item)
    return item


def update(repo: dict, sid: str, name: str = None, desc: str = None,
           content: str = None, steps: list = None, cfg: dict = None) -> Optional[dict]:
    """改一条。**只动传进来的字段**（`None` = 不改），并刷新 `updated_at`。

    ★ `updated_at` 只在**真有字段变化**时才刷新：
      否则"打开看一眼就关掉"也会把时间推到现在，列表按更新时间一排序就乱了 ——
      用户会以为"这条内容改过"，其实没有。
    """
    item = find(repo, sid)
    if item is None:
        return None
    changed = False
    if name is not None and item.get("name") != name:
        item["name"] = (name.strip() or item.get("name"))
        changed = True
    if desc is not None and item.get("desc") != desc:
        item["desc"] = desc
        changed = True
    if content is not None and item.get("content") != content:
        item["content"] = content
        changed = True
    if steps is not None and _dump(steps) != _dump(item.get("steps")):
        item["steps"] = steps
        changed = True
    if cfg is not None and _dump(cfg) != _dump(item.get("cfg")):
        item["cfg"] = cfg
        changed = True
    if changed:
        item["updated_at"] = now_ts()
    return item


def _dump(obj) -> str:
    """比较"有没有真的变了"用的稳定序列化（`sort_keys` 保证键序不影响结果）。"""
    try:
        return json.dumps(obj, ensure_ascii=False, sort_keys=True)
    except Exception:
        return str(obj)


def duplicate(repo: dict, sid: str, new_name: str = None) -> Optional[dict]:
    """**另存为新脚本**：复制一份，改名"原名 副本"，**新建时间 = 现在**。

    ★ 副本的名字带"副本"后缀，且**允许重名**（名字不是主键）——
      这样"另存为"不会因为跟谁重名而失败，用户的动作永远成功、结果永远可预期。

    ★ 步骤必须**重编 id 并复制素材**：沿用原 id 的话，副本与原脚本共用同一份
      帧 / 模板 —— 在副本里重标一步，就把原脚本那一步的素材覆盖了
      （见 `_copy_assets` 注释）。
    """
    src = find(repo, sid)
    if src is None:
        return None
    base = new_name if new_name is not None else "%s 副本" % (src.get("name") or "脚本")
    item = create(repo, base, src.get("desc") or "", src.get("content") or "",
                  copy.deepcopy(src.get("cfg")))
    _adopt_steps(item, src.get("steps") or [])      # 重编 id + 复制素材，见该函数
    return item


def import_steps(repo: dict, name: str, desc: str = "", steps: list = None,
                 cfg: dict = None) -> dict:
    """**把一批外来步骤收编成一条新脚本** —— 主窗【存入仓库】走的就是这条。

    ## 为什么需要它（P7）
    在此之前仓库是**单程**的：只有【载入到工作台】能把仓库取出来跑，
    却没有任何入口把工作台标好的东西**存回去**。用户在台面上标完一步，
    想让 `scripts.json` 里先有这条资产，只能去仓库窗【＋ 新建脚本】**再标一遍** ——
    台上那份白干。这条函数就是那条缺失的回程路。

    ## 与【载入到工作台】严格对称
        载入：仓库脚本 → `profile["steps"]`（**替换**台面，并带上 reference）
        存入：`profile["steps"]` → 仓库**新增一条**（并把 reference 一起带上）
    两边都**只读对方、只写自己**：本模块压根不认识 profile
    （`verify_script_repo.py` 第 11 节用 tokenize 断言代码里没有 profile 调用），
    所以"存入"不可能反过来动到工作台配置。

    ## 两条纪律（都交给 `_adopt_steps`）
      ① **步骤 id 必须重编**：工作台的 id 是 `s01`，直接沿用会让这条脚本的
         帧/模板与工作台草稿**共用同一个文件** —— 回头在仓库里重标这一步，
         就把台面那一步覆盖了。
      ② **素材必须复制**（不是引用、更不是移动）：台面那份要原样留着继续用。
    """
    item = create(repo, name, desc, "", cfg=cfg)
    _adopt_steps(item, steps)
    # 外来步骤的名字可能已乱序（台面重排过、或只存了标齐的几步）——
    # 统一按新顺序归位为「第 N 步」。
    renumber_steps(item)
    return item


def delete(repo: dict, sid: str) -> bool:
    """删掉一条。返回是否真的删了（**不落盘**）。

    ⚠ **不删素材文件**（`data/frames` / `assets/templates` 里那些）：
      与工作台"删步骤不删帧"同一口径 —— 删错一条已经够难受，
      再把素材一起扬了就等于没有后悔药。孤儿文件顶多占点空间。
    """
    scripts = repo.get("scripts", [])
    for i, s in enumerate(scripts):
        if s.get("id") == sid:
            scripts.pop(i)
            return True
    return False
