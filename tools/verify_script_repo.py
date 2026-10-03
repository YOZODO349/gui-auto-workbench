# -*- coding: utf-8 -*-
"""脚本仓库（**存储区 + 编辑区** 双区）：持久化 + 搜索 + 排序 + 增删改 + 另存为 + 界面契约。

**为什么单开一门**：仓库是**用户资产**（他攒下来的脚本），
它的失败模式全是"静默丢数据"——不像崩溃那样会报错，而是
"重启后少了两条""改完存到别处去了""删完还剩一个同名空壳""搜出来的顺序莫名变了"。
这类问题不写断言就永远发现不了。

本门覆盖两侧：
  · **数据层** `src/scripts_repo.py` —— 落盘往返、重启读取、搜索、排序、
    增删改、另存为（步骤重编 id + 素材复制）、id 不复用、坏文件不崩
  · **界面契约** `gui.ScriptRepoWindow` —— **双区**：存储区点选即加载、
    编辑区改完**回写原 id**、编辑区**没有任何可写代码的文本框**、
    新建走填表、删除有确认、主窗入口两个模式下都可用

⚠⚠ **本门绝不碰真实数据**：
  ① `config/scripts.json` 会被指向临时文件（`_verify_repo.json`），跑完删除；
  ② `config/profile.json` 全程只读 —— 唯一会写它的用例（【载入到工作台】）
     跑完按**原始字节**还原，`verify_all.py` 再兜一层哈希比对。
  这是被 `README`/skill 第 7 章第 11 条血训过的：脚本写真实配置 = 清空用户数据。

跑法：  .venv\\Scripts\\python.exe tools/verify_script_repo.py
        .venv\\Scripts\\python.exe tools/verify_script_repo.py --check-dead
"""
from __future__ import annotations

import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from src import scripts_repo as R                                     # noqa: E402

DEAD = "--check-dead" in sys.argv
TMP = os.path.join(ROOT, "config", "_verify_repo.json")
# ⚠ 帧 / 模板也要隔离（P7 新增）：收编外来的步骤会**复制素材**，
#   走的是 `storage.frame_path()` —— 不隔离就在用户的 `data/frames/`、
#   `assets/templates/` 里留下几个 `sc0X-0Y.png`，违反本门"绝不碰真实数据"的硬约束，
#   而且那些残留日后可能和真实脚本的同名步骤混淆。指到 config 下的临时目录，跑完自删。
ASSET_TMP = os.path.join(ROOT, "config", "_verify_assets")
_ASSET_OLD = None


class Checker:
    def __init__(self):
        self.n = 0
        self.fail = 0

    def ok(self, name, cond, detail=""):
        self.n += 1
        cond = bool(cond)
        if not cond:
            self.fail += 1
        print("%s %d. %s%s" % ("[PASS]" if cond else "[FAIL]", self.n, name,
                               ("　—　" + detail) if detail else ""))
        return cond


def _isolate_assets():
    """把帧 / 模板路径指到隔离目录（返回隔离根）。"""
    global _ASSET_OLD
    from src import storage
    if _ASSET_OLD is None:
        _ASSET_OLD = (storage.frame_path, storage.template_path)
    storage.frame_path = lambda sid: os.path.join(ASSET_TMP, "frames",
                                                  "%s.png" % sid)
    storage.template_path = lambda sid: os.path.join(ASSET_TMP, "templates",
                                                     "%s.png" % sid)
    return ASSET_TMP


def _restore_assets():
    """还原真实的帧 / 模板路径，并把隔离目录整个删掉。"""
    global _ASSET_OLD
    from src import storage
    if _ASSET_OLD is not None:
        storage.frame_path, storage.template_path = _ASSET_OLD
        _ASSET_OLD = None
    if os.path.isdir(ASSET_TMP):
        import shutil
        shutil.rmtree(ASSET_TMP, ignore_errors=True)


def _mk_asset(path, data=b"X"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


def _cleanup():
    for p in (TMP, TMP + ".tmp"):
        try:
            if os.path.exists(p):
                os.remove(p)
        except Exception:
            pass


def main():
    c = Checker()
    print("=" * 68)
    print("脚本仓库（%s）" % ("反向对照" if DEAD else "常规"))
    print("=" * 68)

    # === 0. 先隔离：把仓库路径指到临时文件，绝不动真实数据 ===
    real_path = R.REPO_PATH
    R.REPO_PATH = TMP
    _isolate_assets()                      # 帧/模板同样隔离，见 ASSET_TMP 注释
    _cleanup()

    # ================= 反向对照 =================
    if DEAD:
        # 本门的核心保护是"仓库与工作台分开存"。反向对照就要**注入"合在一起"**
        # 这个病因：如果哪天有人把仓库塞进 profile.json，
        # 下面这条会立刻红 —— 因为改仓库就会连带改动 profile.json。
        from src import storage
        repo = R.default_repo()
        R.create(repo, "对照用脚本")
        R.save_repo(repo)

        prof_before = _read_profile_bytes()
        # 故意"错"地也往 profile.json 写一份（模拟合并存储的错误做法）
        prof = storage.load_profile()
        prof["scripts_repo"] = repo["scripts"]
        storage.save_profile(prof)
        prof_after = _read_profile_bytes()

        c.ok("**反向对照**：把仓库塞进 profile.json → 确实会污染工作台配置",
             prof_before != prof_after,
             "证明「分开存」是必需的，不是洁癖")

        # 还原 profile.json（把注入的字段删掉、写回原样）
        try:
            storage.save_profile(_restore_profile())
        except Exception:
            pass

        # ---- 反向对照 2~6：**注入病因**，验证正向断言真的有区分力 ----
        #   ★ 判据（见 skill 第七章第 3 条）：对照要注入**病因**而不是症状，
        #     并且必须"关掉它就必然失败" —— 否则正向的绿是假的。
        #     下面每条都是"把真实实现换成错误实现 → 正向那条断言必须变红"。

        # 病因 A：搜索被写死成"不过滤"
        _real_query = R.query
        R.query = lambda repo, keyword="", sort=None: (
            list(repo.get("scripts", [])))
        bad_repo = {"scripts": [
            {"id": "k1", "name": "每日签到", "desc": "", "content": "",
             "created_at": "2026-01-01 00:00:00", "updated_at": "2026-01-01 00:00:00"},
            {"id": "k2", "name": "领体力", "desc": "", "content": "",
             "created_at": "2026-01-01 00:00:00", "updated_at": "2026-01-01 00:00:00"}]}
        c.ok("**反向对照 A**：搜索若被写死成「不过滤」→ 正向断言必然变红",
             [s["name"] for s in R.query(bad_repo, "签到")] != ["每日签到"],
             "正向断言『按名称搜索命中』确实有区分力")
        R.query = _real_query

        # 病因 B：brief 不兜底正文（没简介就返回空）
        _real_brief = R.brief
        R.brief = lambda s, width=40: (s.get("desc") or "")
        c.ok("**反向对照 B**：简介若不兜底正文 → 正向断言必然变红",
             R.brief({"desc": "", "content": "\n\n第一行正文\n第二行"}) != "第一行正文",
             "正向断言『没简介时兜底到正文首行』确实有区分力")
        R.brief = _real_brief

        # 病因 C：update 无脑刷新 updated_at（打开看一眼也算改过）
        _real_update = R.update
        def _bad_update(repo, sid, name=None, desc=None, content=None,
                        steps=None, cfg=None):
            it = R.find(repo, sid)
            if it is None:
                return None
            if name is not None:
                it["name"] = name
            time.sleep(1.05)
            it["updated_at"] = R.now_ts()
            return it
        R.update = _bad_update
        t_repo = {"scripts": [{"id": "u1", "name": "同名字", "desc": "",
                               "content": "", "created_at": "2026-01-01 00:00:00",
                               "updated_at": "2026-01-01 00:00:00"}]}
        _t0 = R.find(t_repo, "u1")["updated_at"]
        R.update(t_repo, "u1", "同名字")            # 传**相同**的名字
        c.ok("**反向对照 C**：更新若无脑刷新时间 → 正向断言必然变红",
             R.find(t_repo, "u1")["updated_at"] != _t0,
             "正向断言『传入相同值不刷新 updated_at』确实有区分力")
        R.update = _real_update

        # 病因 D：界面搜索框若开着 exportselection → 一搜就丢选中
        #   （这条不需要真建窗口，直接看源码约定即可 —— 建窗口在这里太重）
        gsrc = open(os.path.join(ROOT, "gui.py"), encoding="utf-8").read()
        c.ok("**反向对照 D**：仓库列表的 exportselection 必须是 0",
             "exportselection=0" in gsrc.replace(" ", ""),
             "主窗步骤列表已因此踩过坑（选中被下拉框抢走）")

        # 病因 E（本轮新增）：`steps_of` 若返回**副本** → 采集/增删的改动全丢
        _real_steps_of = R.steps_of
        R.steps_of = lambda s: list(s.get("steps") or [])
        _sc = {"id": "sc09", "steps": []}
        R.steps_of(_sc).append({"id": "x"})
        c.ok("**反向对照 E**：`steps_of` 若返回副本 → 正向断言必然变红",
             len(_sc["steps"]) == 0,
             "正向断言『返回的必须是同一个 list 对象』确实有区分力")
        R.steps_of = _real_steps_of

        # 病因 F（本轮新增）：另存为若**不重编步骤 id** → 副本与原脚本共用素材
        _real_dup = R.duplicate
        def _bad_dup(repo, sid, new_name=None):
            it = R.create(repo, "坏副本")
            for st in R.steps_of(R.find(repo, sid)):
                R.steps_of(it).append(dict(st))     # 照抄 id —— 共用素材
            return it
        R.duplicate = _bad_dup
        _dr = {"scripts": [{"id": "d1", "name": "原", "desc": "", "content": "",
                            "steps": [{"id": "d1-01", "name": "第 1 步"}],
                            "created_at": "", "updated_at": ""}]}
        _bd = _bad_dup(_dr, "d1")
        c.ok("**反向对照 F**：另存为若不重编步骤 id → 正向断言必然变红",
             [s["id"] for s in R.steps_of(_bd)] == ["d1-01"],
             "正向断言『副本步骤 id 必须重编』确实有区分力")
        R.duplicate = _real_dup

        # 病因 G（P7 新增）：收编外来步骤时**不重编 id** → 台面与仓库共用素材
        _real_adopt = R._adopt_steps
        _bench_fixture = [{
            "id": "s01", "name": "第 1 步", "frame": "s01.png",
            "anchor": {"point": [0.1, 0.2], "box": [0, 0, 1, 1],
                       "template": "s01.png", "method": "template"},
            "action": {"point": [0.1, 0.2], "radius": 6, "click": True}}]

        def _bad_adopt_no_reid(item, src_steps):
            import copy as _c
            for st in _c.deepcopy(list(src_steps or [])):
                R.steps_of(item).append(st)      # 照抄 id / frame / template
            return len(R.steps_of(item))

        R._adopt_steps = _bad_adopt_no_reid
        _it_g = R.import_steps(R.default_repo(), "坏收编",
                               steps=_bench_fixture)
        c.ok("**反向对照 G**：收编若不重编步骤 id → 正向断言必然变红",
             [s["id"] for s in R.steps_of(_it_g)] != ["sc01-01"],
             "照抄台面 s01 的话，仓库步骤与台面草稿**指向同一个素材文件名**")
        R._adopt_steps = _real_adopt

        # 病因 H（P7 新增）：收编时**不复制素材** → 仓库那条指向台面的素材
        def _bad_adopt_no_copy(item, src_steps):
            import copy as _c
            for st in _c.deepcopy(list(src_steps or [])):
                nid = R.next_step_id(item)
                st["id"] = nid
                st["frame"] = "%s.png" % nid
                a = st.get("anchor")
                if isinstance(a, dict) and a.get("template"):
                    a["template"] = "%s.png" % nid
                R.steps_of(item).append(st)
            return len(R.steps_of(item))

        from src import storage as _st_h
        _mk_asset(_st_h.frame_path("s01"), b"BENCH-FRAME")     # 台面那份帧
        R._adopt_steps = _bad_adopt_no_copy
        _it_h = R.import_steps(R.default_repo(), "坏收编2", steps=_bench_fixture)
        c.ok("**反向对照 H**：收编若不复制素材 → 正向断言必然变红",
             not os.path.exists(_st_h.frame_path(R.steps_of(_it_h)[0]["id"])),
             "不复制的话，台面与仓库共用同一张帧 —— 覆盖一个即坏另一个")
        R._adopt_steps = _real_adopt

        # 病因 I（P7b 新增）：对话框尺寸**写死**成旧的 540×280 → 正向断言必然变红
        #   这条得真建窗口才验得出来（"装不装得下"是几何事实，读源码看不出来）。
        import gui as _g
        _real_fit = _g.ScriptNameDialog._fit_to_content
        _g.ScriptNameDialog._fit_to_content = lambda self, app: self.geometry(
            "540x280+%d+%d" % (app.winfo_rootx() + 180, app.winfo_rooty() + 120))
        _g.storage.save_profile = lambda _p: True     # 掐掉落盘（双保险）
        _app_i = _g.App()
        _dlg_i = _g.ScriptNameDialog(_app_i, lambda n, d: None, mode="save")
        for _ in range(3):
            _dlg_i.update()
        _good_i, _why_i = _fully_inside(_dlg_i, _dlg_i.btn_ok)
        _dlg_i.close()
        _app_i.destroy()
        _g.ScriptNameDialog._fit_to_content = _real_fit
        c.ok("**反向对照 I**：尺寸写死 540×280 → 正向断言必然变红",
             not _good_i,
             "旧写法下主按钮%s —— 证明『完整可见』那条断言真的有区分力"
             % (_why_i or "居然还在窗内"))

        # 病因 J（P8 新增）：双区若退回"左栏死宽 620 + 不让收缩" →
        #   窄窗下右栏被挤成一条缝，**底部按钮排**必然被压扁。
        #   ⚠ 必须**在窄窗下**验才现形：默认 1240 宽时右栏够用，什么都不露。
        #   ⚠ 病因要注入到**布局约束**（把左栏最小宽拉到 620），
        #     而不是"改个变量"—— 后者症状不出现，对照就成了假对照。
        #   ⚠ 靶子选**底部按钮**而不是【关闭】：【关闭】已挪到标题栏
        #     （横跨整窗、不参与宽度竞争），拿它当靶子会打不中 ——
        #     本对照第一版就是这么失败的，恰恰反证了它确实自由了。
        _app_j = _g.App()
        _win_j = _g.ScriptRepoWindow(_app_j)
        import tkinter as _tk_j
        _body_j = None
        for _ch_j in _win_j.winfo_children():
            if isinstance(_ch_j, _tk_j.Frame) and len(_ch_j.grid_slaves()) >= 2:
                _body_j = _ch_j
                break
        if _body_j is None:                     # 退回：按几何找 body
            for _ch_j in _win_j.winfo_children():
                if isinstance(_ch_j, _tk_j.Frame) and _ch_j.winfo_height() > 100:
                    _body_j = _ch_j
                    break
        _win_j.geometry("1000x700")
        for _ in range(3):
            _win_j.update()
        _before_j = _win_j.btn_save.winfo_width()

        def _squeezed_j():
            """看底部按钮有几个被压扁。"""
            n = 0
            for _w in (_win_j.btn_save, _win_j.btn_save_as, _win_j.btn_to_bench,
                       _win_j.btn_del2, _win_j.btn_capture):
                if _w.winfo_width() < _w.winfo_reqwidth():
                    n += 1
            return n

        _n_before = _squeezed_j()
        # 注入病因：左栏最小宽拉到 620（等价于原来那个死宽 620 的效果）
        try:
            _body_j.columnconfigure(0, minsize=620)
            for _ in range(3):
                _win_j.update()
        except Exception:                       # noqa: BLE001
            pass
        _n_after = _squeezed_j()
        _aw_j = _win_j.btn_save.winfo_width()
        _rw_j = _win_j.btn_save.winfo_reqwidth()
        _win_j.close()
        _app_j.destroy()
        c.ok("**反向对照 J**：左栏死宽 620 → 窄窗下底部按钮必然被压扁",
             _n_after > _n_before,
             "注入前 %d 个被压（【保存修改】实宽 %d）→ 注入后 %d 个被压"
             "（【保存修改】实宽 %d / 所需 %d）"
             "—— 证明『不被压扁』那条断言真的有区分力"
             % (_n_before, _before_j, _n_after, _aw_j, _rw_j))

        _cleanup()
        _restore_assets()
        print("-" * 68)
        if c.fail:
            print("结果：%d/%d 通过，**%d 项失败**" % (c.n - c.fail, c.n, c.fail))
            return 1
        print("结果：%d/%d 通过" % (c.n, c.n))
        return 0

    # ================= 常规 =================
    # ---- 1. 空仓库 ----
    repo = R.load_repo()
    c.ok("空仓库能读（文件不存在时返回空仓库，不抛异常）",
         R.count(repo) == 0 and isinstance(repo.get("scripts"), list))

    # ---- 2. 新建 + 落盘 + **重启读取**（需求 5：重启后仍可读）----
    a = R.create(repo, "每日签到", "打开游戏→点签到→领奖", "goto home\nclick sign\nclaim")
    R.add_step(a)
    R.add_step(a)
    time.sleep(1.05)                       # 让时间戳跨秒，才好验排序
    b = R.create(repo, "领体力", "点体力按钮", "click stamina")
    c.ok("新建分配 id（sc01/sc02…）",
         a["id"] == "sc01" and b["id"] == "sc02", "%s / %s" % (a["id"], b["id"]))
    c.ok("保存能落盘", R.save_repo(repo))

    repo2 = R.load_repo()                  # ← 模拟"程序重启"：丢掉内存、重新读盘
    c.ok("★ 重启后条数不变（持久化）", R.count(repo2) == 2, "%d 条" % R.count(repo2))
    c.ok("★ 重启后**步骤列表完整**（脚本正文一字不差）",
         [s["id"] for s in R.find(repo2, "sc01")["steps"]] == ["sc01-01", "sc01-02"])
    c.ok("★ 重启后名称/简介/时间戳都在",
         R.find(repo2, "sc01")["name"] == "每日签到"
         and R.find(repo2, "sc01")["desc"] == "打开游戏→点签到→领奖"
         and R.find(repo2, "sc01")["created_at"])
    c.ok("★ 重启后**老字段正文**也还在（不丢用户数据）",
         R.find(repo2, "sc01")["content"] == "goto home\nclick sign\nclaim")

    # ---- 2b. ★ 步骤 id 带脚本前缀（各脚本素材天然隔离）----
    c.ok("★ 步骤 id **带脚本前缀**（两条脚本不会互相覆盖素材）",
         [s["id"] for s in R.steps_of(R.find(repo2, "sc01"))][0].startswith("sc01-"),
         "sc01 的步骤 = %s" % [s["id"] for s in R.steps_of(R.find(repo2, "sc01"))])

    # ---- 2c. ★ steps_of 返回的是**同一个 list 对象**（改它即改脚本）----
    sc = R.find(repo2, "sc01")
    R.steps_of(sc).append({"id": "sc01-99", "name": "临时"})
    c.ok("★ `steps_of` 返回**同一个 list**（往里加一步，脚本里立刻多一步）",
         any(s["id"] == "sc01-99" for s in sc["steps"]),
         "返回副本的话，采集链路的改动会全部写进空气里")
    sc["steps"] = [s for s in sc["steps"] if s["id"] != "sc01-99"]

    # ---- 2d. ★ cfg_of 的 reference 与脚本 cfg **同一个 dict**（复核写进去留得住）----
    cfg_view = R.cfg_of(sc)
    cfg_view["reference"]["w"] = 1920
    c.ok("★ `cfg_of` 的 reference 与脚本 cfg 是**同一个对象**（复核量到的分辨率留得住）",
         R.cfg_of(sc)["reference"]["w"] == 1920,
         "深拷贝的话，框会一次次按默认值重裁")
    c.ok("`cfg_of` 缺字段时会按默认补齐（老/坏记录照样能采）",
         R.cfg_of({"id": "z", "steps": []})["match"]["threshold"] > 0)

    # ---- 3. 搜索（需求 4：按名称搜索）----
    c.ok("按名称搜索命中", [s["name"] for s in R.query(repo2, "签到")] == ["每日签到"])
    c.ok("搜索是**子串**匹配（不是前缀）",
         [s["name"] for s in R.query(repo2, "体力")] == ["领体力"])
    c.ok("搜不到就返回空，不报错", R.query(repo2, "不存在的东西") == [])
    c.ok("搜索忽略大小写（英文名场景）",
         len(R.query(repo2, "DAILY")) == len(R.query(repo2, "daily")))
    c.ok("空关键词 = 不过滤", len(R.query(repo2, "")) == 2)
    # ★ 只搜名称、不搜正文 —— 需求写的是"按名称搜索"
    R.update(repo2, "sc02", None, "正文里提到签到二字", None)
    c.ok("★ 只按**名称**搜，不搜正文（否则会捞出无关条目）",
         [s["name"] for s in R.query(repo2, "签到")] == ["每日签到"],
         "sc02 简介含「签到」但名称不含 → 不该命中")

    # ---- 4. 排序（需求 4：按更新时间排序）----
    #   构造 3 条时间明确的数据来验（不能只靠"刚建的两条"——它们同秒，
    #   排序对不对看不出来，本次第一版就踩了这个"看不出差别"的坑）。
    R.save_repo(repo2)
    repo3 = {"version": 1, "scripts": [
        {"id": "s1", "name": "C脚本", "desc": "", "content": "",
         "created_at": "2026-01-01 00:00:00", "updated_at": "2026-03-01 00:00:00"},
        {"id": "s2", "name": "A脚本", "desc": "", "content": "",
         "created_at": "2026-02-01 00:00:00", "updated_at": "2026-01-01 00:00:00"},
        {"id": "s3", "name": "B脚本", "desc": "", "content": "",
         "created_at": "2026-03-01 00:00:00", "updated_at": "2026-02-01 00:00:00"},
    ]}
    c.ok("★ 按更新时间**降序**（默认：最近的在前）",
         [s["id"] for s in R.query(repo3, sort=R.SORT_UPDATED_DESC)] == ["s1", "s3", "s2"])
    c.ok("★ 按更新时间**升序**",
         [s["id"] for s in R.query(repo3, sort=R.SORT_UPDATED_ASC)] == ["s2", "s3", "s1"])
    c.ok("按创建时间降序",
         [s["id"] for s in R.query(repo3, sort=R.SORT_CREATED_DESC)] == ["s3", "s2", "s1"])
    # ★ 排序不许改原仓库的顺序（否则落盘后文件里的顺序会莫名变化）
    c.ok("★ 查询**不改原仓库顺序**（返回的是副本）",
         [s["id"] for s in repo3["scripts"]] == ["s1", "s2", "s3"])
    c.ok("搜索 + 排序能叠加",
         [s["id"] for s in R.query(repo3, "脚本", sort=R.SORT_NAME_ASC)]
         == ["s2", "s3", "s1"], "三条都含「脚本」，名称序 A、B、C")
    c.ok("★ 按名称排序 = 字母序（A→B→C）",
         [s["id"] for s in R.query(repo3, sort=R.SORT_NAME_ASC)] == ["s2", "s3", "s1"],
         "s2=A脚本 / s3=B脚本 / s1=C脚本 → A、B、C")
    c.ok("★ 名称排序不分大小写（apple 与 Banana 不会因大小写乱序）",
         [s["id"] for s in R.query(
             {"scripts": [
                 {"id": "n1", "name": "banana", "updated_at": "2026-01-01 00:00:00"},
                 {"id": "n2", "name": "Apple", "updated_at": "2026-01-01 00:00:00"},
                 {"id": "n3", "name": "cherry", "updated_at": "2026-01-01 00:00:00"}]},
             sort=R.SORT_NAME_ASC)] == ["n2", "n1", "n3"])

    # ---- 5. 更新（需求 2：保存更新）----
    t_before = R.find(repo2, "sc02")["updated_at"]
    time.sleep(1.05)
    R.update(repo2, "sc02", "领体力（改）")
    c.ok("『保存更新』改到名称", R.find(repo2, "sc02")["name"] == "领体力（改）")
    c.ok("★ 改动会刷新 updated_at", R.find(repo2, "sc02")["updated_at"] > t_before,
         "%s → %s" % (t_before, R.find(repo2, "sc02")["updated_at"]))
    c.ok("改动**不动** created_at",
         R.find(repo2, "sc02")["created_at"] < R.find(repo2, "sc02")["updated_at"])
    # ★ 没改内容就不该刷新时间（否则"打开看一眼"也会把时间推到现在，列表就乱了）
    t_mid = R.find(repo2, "sc02")["updated_at"]
    time.sleep(1.05)
    R.update(repo2, "sc02", R.find(repo2, "sc02")["name"])   # 传同样的值
    c.ok("★ 传入相同值**不刷新** updated_at（避免「看一眼就算改过」）",
         R.find(repo2, "sc02")["updated_at"] == t_mid)
    c.ok("更新不存在的 id 返回 None 且不炸", R.update(repo2, "nope", "x") is None)

    # ---- 5b. 更新**步骤**（脚本正文）----
    _st = R.steps_of(R.find(repo2, "sc01"))
    _st2 = [dict(s) for s in _st] + [{"id": "sc01-03", "name": "第 3 步"}]
    R.update(repo2, "sc01", None, None, None, steps=_st2)
    c.ok("★ 更新能改到**步骤列表**（编辑区的改动能存下来）",
         len(R.steps_of(R.find(repo2, "sc01"))) == 3)

    # ---- 6. 另存为（需求 2）----
    n_before = R.count(repo2)
    dup = R.duplicate(repo2, "sc01")
    c.ok("『另存为』新增一条", R.count(repo2) == n_before + 1)
    c.ok("★ 另存为**步骤同源**（步数一致）",
         len(R.steps_of(dup)) == len(R.steps_of(R.find(repo2, "sc01"))))
    c.ok("★ 另存为得到**新 id**（不是复用原 id）", dup["id"] != "sc01", dup["id"])
    c.ok("★ 另存为**步骤 id 必须重编**（否则副本与原脚本共用素材，改一个动两个）",
         all(s["id"].startswith(dup["id"] + "-") for s in R.steps_of(dup)),
         "副本步骤 = %s" % [s["id"] for s in R.steps_of(dup)])
    c.ok("另存为的名字带「副本」（能一眼认出）", "副本" in dup["name"], dup["name"])
    c.ok("另存为不改原条的 updated_at",
         R.find(repo2, "sc01")["updated_at"] == R.find(repo2, "sc01")["updated_at"])

    # ---- 6b. ★ 工作台 → 仓库（P7 补的回程路）----
    #   在这之前仓库是**单程**的：只有【载入到工作台】能把脚本取出来跑，
    #   没有入口把工作台标好的东西存回去 —— 用户在台面上标完只能干瞪眼。
    from src import storage
    _mk_asset(storage.frame_path("s01"), b"BENCH-FRAME")
    _mk_asset(storage.template_path("s01"), b"BENCH-TPL")
    _bench = [{
        "id": "s01", "name": "第 1 步", "frame": "s01.png",
        "anchor": {"point": [0.15625, 0.7025],
                   "box": [0.132812, 0.6925, 0.046484, 0.020625],
                   "template": "s01.png", "method": "template"},
        "action": {"point": [0.158594, 0.666875], "radius": 6,
                   "same_as_anchor": False, "click": True,
                   "click_type": "double"}}]
    _bench_cfg = {"reference": {"w": 2560, "h": 1600},
                  "match": {"threshold": 0.82}, "execution": {"click_radius": 6}}
    n_before_imp = R.count(repo2)
    imp = R.import_steps(repo2, "从工作台存来的", "台面上的那一步", _bench,
                         cfg=_bench_cfg)
    c.ok("★【存入仓库】**新增一条**（不是覆盖谁）",
         R.count(repo2) == n_before_imp + 1)
    c.ok("★ 收编的步骤 id **必须重编**（不带台面那个 s01）",
         [s["id"] for s in R.steps_of(imp)] == [imp["id"] + "-01"],
         "步骤 = %s" % [s["id"] for s in R.steps_of(imp)])
    _imp_st = R.steps_of(imp)[0]
    c.ok("★ `frame` 与模板文件名**跟着重编**（否则指向台面的素材）",
         _imp_st["frame"] == _imp_st["id"] + ".png"
         and _imp_st["anchor"]["template"] == _imp_st["id"] + ".png",
         "%s / %s" % (_imp_st["frame"], _imp_st["anchor"]["template"]))
    c.ok("★★ 素材**被复制**到新字号下（台面与仓库各一份）",
         os.path.exists(storage.frame_path(_imp_st["id"]))
         and os.path.exists(storage.template_path(_imp_st["id"])))
    c.ok("★ 台面那份**仍在**（是复制，不是移动）",
         os.path.exists(storage.frame_path("s01"))
         and os.path.exists(storage.template_path("s01")))
    c.ok("★★ 收编**不改台面的步骤对象**（只读台面、只写仓库，方向单向）",
         _bench[0]["id"] == "s01" and _bench[0]["frame"] == "s01.png"
         and _bench[0]["anchor"]["template"] == "s01.png")
    c.ok("★ 步骤名按新顺序归位成「第 N 步」",
         _imp_st["name"] == "第 1 步", _imp_st["name"])
    c.ok("★★ `cfg.reference` 从台面**带了过来**（换台机器载入才不会裁错框）",
         R.cfg_of(imp)["reference"] == {"w": 2560, "h": 1600},
         str(R.cfg_of(imp)["reference"]))
    c.ok("收编结果落盘后可回读（重启不丢）",
         R.save_repo(repo2)
         and len(R.find(R.load_repo(), imp["id"])["steps"]) == 1)

    # ---- 7. 删除（需求 2）----
    R.save_repo(repo2)
    R.delete(repo2, "sc02")
    c.ok("删除生效", R.find(repo2, "sc02") is None)
    c.ok("删除不存在的 id 返回 False 且不炸", R.delete(repo2, "nope") is False)

    # ---- 8. ★ id 不复用（模块头纪律 2）----
    after_del = R.next_script_id(repo2)
    c.ok("★ 删掉的 id **不复用**（新脚本不会顶旧号）",
         after_del not in ("sc01", "sc02"), "下一个 = %s" % after_del)
    c.ok("步骤 id 也是只增的（同脚本内不撞号）",
         R.next_step_id(R.find(repo2, "sc01")) == "sc01-04",
         R.next_step_id(R.find(repo2, "sc01")))

    # ---- 9. ★ 坏文件不崩、不覆盖 ----
    with open(TMP, "w", encoding="utf-8") as f:
        f.write("{这不是合法 JSON")
    bad = R.load_repo()
    c.ok("★ 坏 JSON 时返回空仓库（不抛异常，界面不会崩）", isinstance(bad, dict))
    c.ok("★ 坏文件**不被自动覆盖**（用户资产宁可留着让他自己看）",
         open(TMP, encoding="utf-8").read() == "{这不是合法 JSON")
    with open(TMP, "w", encoding="utf-8") as f:
        json.dump({"version": 1, "scripts": [
            {"id": "x1", "name": "半截数据"},          # 缺 desc/steps/cfg/时间
            "这是字符串不是字典",                        # 类型错
            {"name": "没有 id"},                        # 缺主键
        ]}, f, ensure_ascii=False)
    half = R.load_repo()
    c.ok("★ 半截/脏数据能读（缺字段补默认、无 id 的丢弃）",
         R.count(half) == 1 and R.steps_of(R.find(half, "x1")) == [],
         "留下的: %s" % [s["id"] for s in half["scripts"]])

    # ---- 10. brief 兜底（列表"简介"那一行）----
    c.ok("有简介时用简介", R.brief({"desc": "我的简介", "content": "正文"}) == "我的简介")
    c.ok("★ 没简介时**兜底到老正文首行**（列表不至于空着一列）",
         R.brief({"desc": "", "content": "\n\n第一行正文\n第二行"}) == "第一行正文")
    c.ok("★ 连正文也没有 → 兜底到**步骤概况**",
         R.brief({"desc": "", "content": "", "steps": [{}, {}]}) == "共 2 步")
    c.ok("两者都空时给明确占位",
         "还没标步骤" in R.brief({"desc": "", "content": "", "steps": []}))
    c.ok("过长会截断并带省略号", R.brief({"desc": "字" * 100}, 10).endswith("…"))

    # ---- 11. ★ 与 profile.json 的隔离（本门最重要的保护）----
    from src import storage
    c.ok("★ 两个文件**路径不同**（仓库不塞进 profile.json）",
         os.path.abspath(R.REPO_PATH) != os.path.abspath(storage.PROFILE_PATH),
         "repo=%s" % os.path.basename(R.REPO_PATH))
    src = open(os.path.join(ROOT, "src", "scripts_repo.py"), encoding="utf-8").read()
    # ⚠ 断言要**去掉注释和字符串**再查 —— 否则注释里提一句 `save_profile`
    #   就会被误判成"真的调用了"（本门第一版就是这么误报的）。
    #   判据是"代码里有没有这个调用"，不是"文件里有没有这个字"。
    code_only = _strip_comments_and_docstrings(src)
    c.ok("★ `scripts_repo.py` 的**代码里**没有任何 profile 读写调用",
         "save_profile" not in code_only and "load_profile" not in code_only
         and "PROFILE_PATH" not in code_only,
         "注释里可以提，代码里不许碰")

    # ---- 12. 界面契约 ----
    print("   （界面部分：构建主窗与仓库窗口…）")
    try:
        import gui

        # ⚠⚠ **测试里必须拦掉弹窗**，否则会卡死：
        #   `messagebox.showinfo/askyesno` 是**模态**的 —— 它会一直等用户点按钮。
        #   测试进程里没人可点，于是永久阻塞（本门第一版就是这么挂住的，
        #   表现是"跑到某一项就没输出了"，很容易误判成别的地方死循环）。
        #   而且拦下来还能顺带断言"确实提示了用户"，比只验"回调没被调"更硬。
        shown = []

        class _FakeMB:
            @staticmethod
            def showinfo(title, msg, **kw):
                shown.append(("info", title, msg))

            @staticmethod
            def showwarning(title, msg, **kw):
                shown.append(("warn", title, msg))

            @staticmethod
            def showerror(title, msg, **kw):
                shown.append(("err", title, msg))

            @staticmethod
            def askyesno(title, msg, **kw):
                shown.append(("ask", title, msg))
                return True                     # 一律"是"：测试要的是流程走得通

            @staticmethod
            def askokcancel(title, msg, **kw):
                shown.append(("ask", title, msg))
                return True

        _real_mb = gui.messagebox
        gui.messagebox = _FakeMB

        gui.scripts_repo.REPO_PATH = TMP          # 界面也走临时文件
        _cleanup()
        app = gui.App()
        c.ok("★ 主窗有【脚本仓库】入口按钮", hasattr(app, "btn_repo"),
             getattr(getattr(app, "btn_repo", None), "cget", lambda _k: "?")("text")
             if hasattr(app, "btn_repo") else "缺失")
        c.ok("★★ 主窗有【存入仓库】按钮（P7 补的回程路：台面 → 仓库）",
             hasattr(app, "btn_to_repo")
             and app.btn_to_repo.cget("text") == "存入仓库",
             "在这之前只有仓库 → 台面的单程路")

        win = gui.ScriptRepoWindow(app)
        c.ok("仓库窗口能构建", win is not None)
        c.ok("★ 有**两个**列表：存储区 `lst` + 编辑区 `steps_lst`（双区交互）",
             hasattr(win, "lst") and hasattr(win, "steps_lst"))
        c.ok("窗口里有搜索框", hasattr(win, "ent_kw"))
        c.ok("窗口里有排序下拉（按更新时间排）", hasattr(win, "cmb_sort")
             and any("更新" in l for l, _k in gui.scripts_repo.SORT_LABELS))
        c.ok("★ 存储区列表关了 exportselection（否则一搜就丢选中）",
             int(win.lst.cget("exportselection")) == 0)
        c.ok("空仓库时给出**明确引导**（不是空白列表）",
             "新建脚本" in win.lst.get(0), win.lst.get(0).strip())

        # ★ 编辑区**没有任何可写代码的文本框**（需求：全程不用手写脚本代码）
        c.ok("★ 编辑区**没有可编辑的文本区**（不许出现手写脚本代码的地方）",
             _editable_texts(win) == [],
             "找到 %d 个可编辑 Text" % len(_editable_texts(win)))
        c.ok("★ 旧版那个『手写内容』的编辑框已移除",
             not hasattr(gui, "ScriptEditDialog"),
             "脚本正文现在是步骤列表，靠点选生成")
        c.ok("编辑区有【采集本步画面】（走原本那条点选链路）",
             _find_button(win, "采集本步画面") is not None)
        c.ok("编辑区有【保存修改】/【另存为新脚本】/【删除脚本】",
             all(_find_button(win, t) is not None
                 for t in ("保存修改", "另存为新脚本", "删除脚本")))

        # 建两条 → 界面能搜能排
        gui.scripts_repo.create(win.repo, "每日签到", "打开游戏并签到")
        win._commit()
        gui.scripts_repo.create(win.repo, "领体力", "点体力按钮")
        win._commit()
        c.ok("★ 新建后列表立刻反映（不必重开窗口）", win.lst.size() == 2,
             "%d 行" % win.lst.size())
        win.var_kw.set("签到")
        win._refresh()
        c.ok("界面搜索生效（列表只剩命中的）", win.lst.size() == 1,
             win.lst.get(0).strip()[:20])
        c.ok("搜索时计数标签说明「筛出 N 个」",
             "筛出 1 个" in win.lb_count.cget("text"),
             win.lb_count.cget("text"))
        win._clear_kw()
        c.ok("清除搜索后恢复全量", win.lst.size() == 2)

        # ★ 选中即加载：在存储区点一下 → 内容进编辑区
        win.lst.selection_clear(0, "end")
        win.lst.selection_set(0)
        win._on_pick_script()
        cur = win._cur()
        c.ok("★ **选中即加载**：名称进编辑区", win.ent_name.get() == cur["name"],
             "%s / %s" % (win.ent_name.get(), cur["name"]))
        c.ok("★ **选中即加载**：简介进编辑区", win.ent_desc.get() == cur["desc"])
        c.ok("★ 编辑区的时间戳也一起带出来（知道这条什么时候建的）",
             (cur.get("created_at") or "—") in win.lb_time.cget("text"),
             win.lb_time.cget("text"))

        # ★ 改完**回写到原 id**（不是另存一条）
        sid = cur["id"]
        n_before_save = gui.scripts_repo.count(win.repo)
        win.ent_name.delete(0, "end")
        win.ent_name.insert(0, "改过的名字")
        win._on_form_edit()
        c.ok("填表改动会**标出未保存**（不至于默默丢）",
             "未保存" in win.lb_dirty.cget("text"), win.lb_dirty.cget("text"))
        win._save_edit()
        onfile = gui.scripts_repo.find(gui.scripts_repo.load_repo(), sid)
        c.ok("★ 保存修改**回写到原 id**（条数不变，不是另存一条）",
             gui.scripts_repo.count(win.repo) == n_before_save
             and onfile is not None and onfile["name"] == "改过的名字",
             "盘上：%s" % (onfile or {}).get("name"))

        # ★ 步骤操作：点一下就生效并立刻落盘
        win._add_step()
        win._add_step()
        onfile = gui.scripts_repo.find(gui.scripts_repo.load_repo(), sid)
        c.ok("★ 点【新增一步】立刻落盘（仓库是资产，不等关窗才存）",
             len(onfile["steps"]) == 2, "盘上 %d 步" % len(onfile["steps"]))
        c.ok("★ 新增的步骤 id 带脚本前缀（不会顶掉主窗的素材）",
             all(s["id"].startswith(sid + "-") for s in onfile["steps"]),
             str([s["id"] for s in onfile["steps"]]))
        win.steps_lst.selection_clear(0, "end")
        win.steps_lst.selection_set(0)
        win._move(1)
        onfile = gui.scripts_repo.find(gui.scripts_repo.load_repo(), sid)
        c.ok("★ 上移/下移改动也落盘",
             [s["id"] for s in onfile["steps"]]
             == [s["id"] for s in gui.scripts_repo.steps_of(win._cur())])
        win.steps_lst.selection_clear(0, "end")
        win.steps_lst.selection_set(0)
        win._del_step()
        onfile = gui.scripts_repo.find(gui.scripts_repo.load_repo(), sid)
        c.ok("★ 删除本步也落盘（且编号自动重排）",
             len(onfile["steps"]) == 1
             and onfile["steps"][0]["name"] == "第 1 步",
             "%d 步 / %s" % (len(onfile["steps"]), onfile["steps"][0]["name"]))

        # ★ 采集入口确实接到了主窗那条链路（不另写一份，免得走偏）
        c.ok("★ 主窗提供 `begin_script_capture`（仓库复用同一条采集链路）",
             hasattr(app, "begin_script_capture"))
        c.ok("★ 采集上下文：默认写主窗自己的 steps（主窗行为零变化）",
             app._cap_steps() is app.profile["steps"]
             and app._cap_cfg() is app.profile)
        _fake = {"id": "scZZ", "name": "假脚本", "steps": [], "cfg": {}}
        app._cap_ctx = {"steps": gui.scripts_repo.steps_of(_fake),
                        "cfg": gui.scripts_repo.cfg_of(_fake), "name": "假脚本"}
        c.ok("★ 切到脚本上下文后，采集写的是**脚本的 steps**",
             app._cap_steps() is _fake["steps"]
             and "假脚本" in app._cap_step_label(0),
             app._cap_step_label(0))
        app._cap_ctx = None
        app._running = True
        c.ok("★ 正在执行流程时拒绝开动采集（不与执行抢画面）",
             app.begin_script_capture(_fake, 0, lambda i: None) is False)
        app._running = False

        # 新建脚本：走**填表**对话框（不是手写内容）
        dlg = gui.ScriptNameDialog(win, lambda n, d: None)
        c.ok("新建脚本走**填表对话框**（只问名称和简介）",
             hasattr(dlg, "ent_name") and hasattr(dlg, "ent_desc"))
        c.ok("★ 新建对话框里**没有**写脚本正文的框",
             _editable_texts(dlg) == [])
        calls = []
        dlg.on_ok = lambda n, d: calls.append((n, d))
        dlg.ent_name.insert(0, "")
        n_shown = len(shown)
        dlg._ok()
        c.ok("★ 名称为空时**拦住**（否则仓库里一堆认不出的条目）",
             not calls, "回调没被调到")
        c.ok("★ 拦住时**给了提示**（不是默默无反应）",
             len(shown) > n_shown and shown[-1][0] == "info",
             str(shown[-1:] or "无提示"))
        dlg.ent_name.insert(0, "领取奖励")
        dlg._ok()
        c.ok("填好名字就能建（回调拿到名称与简介）",
             calls and calls[-1][0] == "领取奖励", str(calls[-1:]))
        dlg.close()

        # ★★ 对话框必须**按内容装得下**（P7b —— 用户实拍"存入仓库的配置框太小，
        #    简介框和【存进仓库】按钮都被顶到窗底之外，根本看不见"）。
        #    两种模式各测一遍：它们的提示文案长短不同（"新建"一行左右、
        #    "存入仓库"三行），**写死高度必然把某一边裁掉** ——
        #    实测改前恒为 280，而内容实需 360(new) / 384(save)，两边都矮。
        for _m in ("new", "save"):
            d2 = gui.ScriptNameDialog(win, lambda n, d: None, mode=_m)
            for _ in range(3):
                d2.update()
            bad2 = []
            for nm2, wdg2 in (("名称输入框", d2.ent_name), ("简介输入框", d2.ent_desc),
                              ("主按钮", d2.btn_ok)):
                good2, why2 = _fully_inside(d2, wdg2)
                if not good2:
                    bad2.append("%s：%s" % (nm2, why2))
            c.ok("★★ 对话框（mode=%s）里两个输入框与主按钮**完整可见**" % _m,
                 not bad2, "；".join(bad2))
            c.ok("★ 对话框（mode=%s）高度**按内容自适应**，不是写死的 280" % _m,
                 d2.winfo_height() >= d2.winfo_reqheight(),
                 "窗高 %d ≥ 内容需高 %d" % (d2.winfo_height(), d2.winfo_reqheight()))
            d2.close()

        # ★★ 底部按钮排 & 【关闭】不许被压扁（P8 —— 用户实拍
        #    "脚本仓库的关闭按钮被挤的很小"）。
        #    改前实测：右栏用"左栏死宽 620 + pack"，窗口收到 1000 宽时右栏只剩
        #    **344px**，而按钮排所需 486px → 整排被压；`side="right"` 的【关闭】
        #    更是只剩 **24px**（所需 74），1100 宽时塌成 1px 等于消失。
        #    改后：双区走 grid 按权重分配（都允许收缩）、底部条先 `side="bottom"`
        #    占位、按钮排 grid 两行、【关闭】挪到标题栏右上角。
        #    ⚠ 必须**多档窗口**都测 —— 只在默认尺寸测会全绿（默认 1240 恰好装得下）。
        for _sz in ((1240, 880), (1100, 800), (1000, 700), (1000, 620)):
            win.geometry("%dx%d" % _sz)
            for _ in range(3):
                win.update()
            bad3 = []
            for _nm3, _wdg3 in (("保存修改", win.btn_save),
                                ("另存为新脚本", win.btn_save_as),
                                ("载入到工作台", win.btn_to_bench),
                                ("删除脚本", win.btn_del2),
                                ("采集本步画面", win.btn_capture)):
                good3, why3 = _fully_inside(win, _wdg3)
                if not good3:
                    bad3.append("%s：%s" % (_nm3, why3))
                elif _wdg3.winfo_width() < _wdg3.winfo_reqwidth():
                    bad3.append("%s：被压 %dpx（%d<%d）"
                                % (_nm3, _wdg3.winfo_reqwidth() - _wdg3.winfo_width(),
                                   _wdg3.winfo_width(), _wdg3.winfo_reqwidth()))
            c.ok("★★ %dx%d：底部五个按钮**不被压扁、完整可见**" % _sz,
                 not bad3, "；".join(bad3) or "全部拿到所需宽度")

            # 【关闭】单独断言：必须**满宽**且完整落在窗内
            g4, w4 = _fully_inside(win, win.btn_close)
            rw4 = win.btn_close.winfo_reqwidth()
            aw4 = win.btn_close.winfo_width()
            c.ok("★★ %dx%d：【关闭】完整可见且**未被压扁**" % _sz,
                 g4 and aw4 >= rw4,
                 w4 or "实宽 %d / 所需 %d" % (aw4, rw4))
        win.geometry("1240x880")
        for _ in range(2):
            win.update()

        # ★ 模式门控：仓库按钮在**两个模式下都可用**
        app.mode.set("edit")
        app._apply_mode_gate()
        st_edit = str(app.btn_repo.cget("state"))
        app.mode.set("run")
        app._apply_mode_gate()
        st_run = str(app.btn_repo.cget("state"))
        c.ok("★【脚本仓库】入口在编辑/执行模式下都可用（它不参与执行流程）",
             st_edit == "normal" and st_run == "normal",
             "edit=%s run=%s" % (st_edit, st_run))

        # ★ 单例：连点两次不弹两个窗口
        app._repo_win = win
        w_before = win
        app._open_repo()
        c.ok("★ 连点【脚本仓库】只开一个窗口（两个窗口会各存一份、互相覆盖）",
             app._repo_win is w_before)

        # ★【载入到工作台】：跑完按**原始字节**还原 profile.json，绝不污染
        #   这是仓库"取出来接着用"的出口，也是唯一会碰 profile.json 的用例。
        raw = _read_profile_bytes()
        try:
            win.lst.selection_clear(0, "end")
            win.lst.selection_set(0)
            win._on_pick_script()
            cur = win._cur()
            n_steps = len(gui.scripts_repo.steps_of(cur))
            if n_steps:
                win._load_to_workbench()
                c.ok("★【载入到工作台】把脚本的步骤带进工作台",
                     len(app.profile["steps"]) == n_steps
                     and app.profile["steps"][0]["id"].startswith(cur["id"] + "-"),
                     "%d 步 / 首步 id=%s" % (n_steps,
                                             app.profile["steps"][0]["id"]))
                c.ok("★ 载入时**先问一句**（会覆盖台面上没存的东西）",
                     any(s[0] == "ask" for s in shown))
            else:
                c.ok("★【载入到工作台】把脚本的步骤带进工作台", False,
                     "这条脚本没有步骤，用例没跑到")
        finally:
            if raw:
                with open(storage.PROFILE_PATH, "wb") as f:
                    f.write(raw)
            app.profile = storage.load_profile()

        # ★ 走一遍真删除：确认"库里真少了、盘上也真没了"
        n_before_del = gui.scripts_repo.count(win.repo)
        sid_del = win._shown[0]["id"]
        win.lst.selection_clear(0, "end")
        win.lst.selection_set(0)
        win._on_pick_script()
        win._delete_script()
        c.ok("★ 删除后**内存里**少一条",
             gui.scripts_repo.count(win.repo) == n_before_del - 1)
        c.ok("★ 删除后**盘上**也同步（重新读盘确认）",
             gui.scripts_repo.find(gui.scripts_repo.load_repo(), sid_del) is None,
             "改完立刻落盘，崩了也不丢")
        # ⚠ 记录格式是 `(kind, title, msg)` —— 别按下标 `(_, t, _m)` 解成 title
        #   （本门第一版就这么写错过，于是拿 title 去比 "ask"，恒不相等）。
        c.ok("删除前有**确认提示**（不可撤销的动作必须问一句）",
             any(s[0] == "ask" for s in shown),
             "提示记录：%s" % [s[0] for s in shown])

        # ★★【存入仓库】：工作台 → 仓库那条回程路（P7）
        #   ⚠ 成功那一步会弹**模态填表框**（等用户敲名字）。测试里换成"立刻替用户
        #     填好名字"的替身；同时把它被调到这件事记下来 —— 只验"回调没被调"是
        #     弱断言，验"确实弹了、且是「存进仓库」那种文案"才硬。
        dlg_modes = []

        class _FakeNameDlg:
            def __init__(self, app_, on_ok, mode="new"):
                dlg_modes.append(mode)
                on_ok("从工作台存入的脚本", "台面上标好的那一步")

        _real_dlg2 = gui.ScriptNameDialog
        gui.ScriptNameDialog = _FakeNameDlg
        raw2 = _read_profile_bytes()
        try:
            # ① 一步都没有 → 拦住，不许产生空脚本
            app.profile["steps"] = []
            n_before_save = gui.scripts_repo.count(win.repo)
            k0 = len(shown)
            app._save_bench_to_repo()
            c.ok("★ 工作台一步都没有 → **拦住并说明**（不产生空脚本）",
                 gui.scripts_repo.count(win.repo) == n_before_save
                 and len(shown) > k0 and shown[-1][0] == "info",
                 str(shown[-1:] or "无提示"))

            # ② 有步骤但一步都没标齐 → 也拦住（没识别点的步骤收进仓库只是垃圾）
            app.profile["steps"] = [{"id": "s01", "name": "第 1 步",
                                     "frame": "s01.png"}]
            k1 = len(shown)
            app._save_bench_to_repo()
            c.ok("★ 一步都没标齐 → 拦住（与执行模式同一套口径）",
                 gui.scripts_repo.count(win.repo) == n_before_save
                 and len(shown) > k1 and shown[-1][0] == "info",
                 str(shown[-1:] or "无提示"))

            # ③ 标齐一步 → 真存进去
            app.profile["steps"] = [{
                "id": "s01", "name": "第 1 步", "frame": "s01.png",
                "anchor": {"point": [0.15625, 0.7025],
                           "box": [0.132812, 0.6925, 0.046484, 0.020625],
                           "template": "s01.png", "method": "template"},
                "action": {"point": [0.158594, 0.666875], "radius": 6,
                           "same_as_anchor": False, "click": True,
                           "click_type": "double"}}]
            app.profile["reference"] = {"w": 2560, "h": 1600}
            app._save_bench_to_repo()
            c.ok("★ 存入后仓库**多一条**（内存里当场可见）",
                 gui.scripts_repo.count(win.repo) == n_before_save + 1,
                 "%d → %d 条" % (n_before_save, gui.scripts_repo.count(win.repo)))
            _hit = gui.scripts_repo.query(win.repo, "从工作台存入的脚本")
            saved = (gui.scripts_repo.find(gui.scripts_repo.load_repo(),
                                           _hit[0]["id"]) if _hit else None)
            c.ok("★★ 存入的脚本**已在盘上**（不必等关窗，仓库是资产）",
                 saved is not None)
            c.ok("★ 弹的是「存进仓库」那种文案的填表框（与新建脚本区分开）",
                 "save" in dlg_modes, str(dlg_modes))
            c.ok("★ 存进去的步骤 id **重编过**（不是台面的 s01）",
                 bool(saved and saved.get("steps")
                      and saved["steps"][0]["id"] != "s01"),
                 str(saved and [s["id"] for s in saved.get("steps", [])]))
            c.ok("★ 存进去的 `cfg.reference` 跟台面一致（换机器载入不裁错框）",
                 bool(saved
                      and gui.scripts_repo.cfg_of(saved)["reference"]
                      == {"w": 2560, "h": 1600}))
            c.ok("★★ 台面步骤**一个字没动**（存入是只读台面的单向操作）",
                 app.profile["steps"][0]["id"] == "s01"
                 and app.profile["steps"][0]["frame"] == "s01.png"
                 and app.profile["steps"][0]["anchor"]["template"] == "s01.png")
        finally:
            gui.ScriptNameDialog = _real_dlg2
            if raw2:
                with open(storage.PROFILE_PATH, "wb") as f:
                    f.write(raw2)
            app.profile = storage.load_profile()

        c.ok("★★ 整个「存入」过程**没写 profile.json**（字节级未变）",
             _read_profile_bytes() == raw2,
             "存入只写 scripts.json；工作台配置连字节都不许动")

        win.close()
        app.destroy()
        gui.messagebox = _real_mb              # 还原，别影响后续门
    except Exception as exc:
        import traceback
        traceback.print_exc()
        c.ok("界面部分能跑（构建主窗/仓库窗/新建框）", False, str(exc))
    finally:
        # 无论成败都要还原：messagebox 被换成假的会让**后续门**静默跳过确认，
        # 那是"一个测试污染另一个测试"的典型事故。
        try:
            import gui as _g
            if "_real_mb" in dir():
                _g.messagebox = _real_mb
        except Exception:
            pass

    # 收尾：清掉临时文件，还原真实路径
    R.REPO_PATH = real_path
    _cleanup()
    _restore_assets()

    print("-" * 68)
    if c.fail:
        print("结果：%d/%d 通过，**%d 项失败**" % (c.n - c.fail, c.n, c.fail))
        return 1
    print("结果：%d/%d 全部通过 ✓" % (c.n, c.n))
    return 0


def _read_profile_bytes():
    from src import storage
    try:
        with open(storage.PROFILE_PATH, "rb") as f:
            return f.read()
    except Exception:
        return b""


def _restore_profile():
    """把反向对照注入的字段清掉（只删我们加的键，不动用户其它数据）。"""
    from src import storage
    prof = storage.load_profile()
    prof.pop("scripts_repo", None)
    return prof


def _find_button(parent, text):
    """深度优先找文案匹配的 Button（别记属性名 —— 一改布局名就找不到）。"""
    import tkinter as tk
    for w in parent.winfo_children():
        try:
            if isinstance(w, tk.Button) and w.cget("text") == text:
                return w
        except Exception:
            pass
        got = _find_button(w, text)
        if got is not None:
            return got
    return None


def _editable_texts(parent):
    """窗口里**可编辑**的 `tk.Text` —— 有它就说明用户能在这儿敲东西。

    ★ 本门最要紧的一条：需求是"用户全程只需点选、填表和确认，**不需要手写任何
      脚本代码**"。所以仓库窗口里**不许存在可编辑的多行文本框** ——
      只留只读的详情（`state="disabled"`）和单行 Entry（名称/简介）。
    """
    import tkinter as tk
    out = []
    for w in parent.winfo_children():
        try:
            if isinstance(w, tk.Text) and str(w.cget("state")) == "normal":
                out.append(w)
        except Exception:
            pass
        out.extend(_editable_texts(w))
    return out


def _fully_inside(win, wdg):
    """`wdg` 是否**完整**落在 `win` 的客户区内（含 1px 容差）。

    ★ 判据里必须有 `w<=1 or h<=1 → 未布局`：被压成 1px 的控件**照样算 mapped**，
      只查 `winfo_ismapped()` 会把"看不见"判成"可见"（顶栏那一轮就是这么漏的）。
    """
    try:
        if wdg is None or not wdg.winfo_exists():
            return False, "控件不存在"
        if not wdg.winfo_ismapped():
            return False, "未显示（被挤出可视区）"
        x0, y0 = win.winfo_rootx(), win.winfo_rooty()
        x1, y1 = x0 + win.winfo_width(), y0 + win.winfo_height()
        x, y = wdg.winfo_rootx(), wdg.winfo_rooty()
        w, h = wdg.winfo_width(), wdg.winfo_height()
        if w <= 1 or h <= 1:
            return False, "尺寸为 0（未布局）"
        if x < x0 - 1 or y < y0 - 1 or x + w > x1 + 1 or y + h > y1 + 1:
            return False, ("出界 x=%d y=%d w=%d h=%d / 窗口 %d,%d~%d,%d"
                           % (x, y, w, h, x0, y0, x1, y1))
        return True, ""
    except Exception as exc:                    # noqa: BLE001
        return False, str(exc)


def _strip_comments_and_docstrings(src: str) -> str:
    """去掉注释与字符串字面量，只留**可执行代码**。

    ★ 为什么必须这么干（本门第一版就是这么误报的）：
      想断言"`scripts_repo.py` 不许碰 profile"，第一版直接 `"save_profile" in src` ——
      结果**注释里提了一句 `storage.save_profile` 就红了**，而代码里根本没有这个调用。
      那种断言测的是"文件里有没有这个字"，不是"代码里有没有这个动作"，
      属于典型的弱断言：改个注释就能让它变红/变绿，跟真实行为脱钩。
    """
    import io
    import tokenize
    out = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type in (tokenize.COMMENT, tokenize.STRING):
                continue                       # 注释与字符串一律丢弃
            if tok.type == tokenize.NAME:
                out.append(tok.string)
    except Exception:
        return src                             # 解析失败就退回原文（宁可严一点）
    return " ".join(out)


if __name__ == "__main__":
    raise SystemExit(main())
