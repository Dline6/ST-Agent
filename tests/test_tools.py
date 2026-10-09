"""项目管理工具的最小回归测试（2026-09-26 加固）。

覆盖三处此前无测试的实现细节：
- ``render_ledger.serialize`` 回写任务文件时**不得丢字段**（未知键与空值键原样保留）——
  派生父状态会重写任务文件，丢字段属静默数据损坏；
- ``T-INT-*`` 集成关卡层被 ID_RE / layer_of / LAYER_ORDER 识别，且 `ready_sort_key`
  在同优先级内把关卡排到队首（2026-09-27 调度规则校正）；
- ``verify_docs`` 的三项新检查（接口面完整、集成关卡覆盖、遗留销账）与 `gate: skip` 例外；
- ``render_ledger`` 派生父 ``depends_on``（活跃区、按集合比较、剔父自身子树）与
  ``verify_docs`` 检查 9（2026-09-28 机制）；
- ``verify_docs`` 的断链扫描跳过 ``.git`` 与 gitignored 的 ``tmp/`` ``temp/`` 草稿目录
  （2026-09-27 加固）；
- **测试模块名全树唯一**（2026-10-06 由 T-L6-002 批次的 ⑥ 实测暴露：`tests/l5/test_runtime.py`
  与 `tests/l6/test_runtime.py` 同名 ⇒ `pytest tests` 全量收集报 `import file mismatch`，
  而范围套件各自跑得到——该缺陷随 PR #98 落到 main 后才被全量跑出来）。
"""

from __future__ import annotations

import pathlib
import re
import sys

import pytest

sys.dont_write_bytecode = True   # 别在 项目管理/tools/ 生成 __pycache__

import render_ledger as rl  # noqa: E402
import verify_docs as vd  # noqa: E402


# ───────────────────────── render_ledger.serialize 保真 ─────────────────────────

def test_serialize_keeps_unknown_and_empty_fields():
    fm = {"id": "T-X-001", "title": "示例", "status": "todo", "verify": "", "gate": "skip"}
    out = rl.serialize(fm, "正文")
    assert "gate: skip" in out          # 未知键保留
    assert "verify:" in out             # 空值键保留
    assert "status: todo" in out


def test_serialize_roundtrip(tmp_path):
    p = tmp_path / "T-X-001-示例.md"
    fm = {"id": "T-X-001", "title": "示例", "status": "todo", "verify": "", "custom": "值"}
    p.write_text(rl.serialize(fm, "## 目标\n示例"), encoding="utf-8")
    back, body = rl.read_fm(str(p))
    assert back["custom"] == "值"       # 未知键往返不丢
    assert back["verify"] == ""         # 空值键往返不丢
    assert back["status"] == "todo"
    assert "## 目标" in body


def test_serialize_quotes_arch_link():
    fm = {"id": "T-X-001", "arch_link": "[01 §2](../../docs/技术架构-v2/01-平台共享契约.md)"}
    line = [ln for ln in rl.serialize(fm, "正文").splitlines() if ln.startswith("arch_link:")][0]
    assert line.startswith('arch_link: "')   # 值含 Markdown 链接，须成对引号包裹


# ───────────────────────── 写文件强制 LF ─────────────────────────
# 历史事故（2026-09-26）：Windows 文本模式把 \n 翻成 \r\n，bootstrap 写出的 27 个任务
# 文件在仓库里是 CRLF，与其余 LF 文件不一致，此后每次编辑都产生整文件 diff。

def test_write_file_emits_lf_only(tmp_path):
    p = tmp_path / "x.md"
    rl.write_file(str(p), "行1\n行2\n")
    assert p.read_bytes() == "行1\n行2\n".encode("utf-8")


def test_parent_status_rewrite_keeps_lf(tmp_path, monkeypatch):
    """派生父状态的回写路径（曾是 CRLF 来源）必须写出 LF。"""
    monkeypatch.setattr(rl, "PM", str(tmp_path))   # scan 用 PM 算相对路径，须同一盘符
    parent = tmp_path / "T-X-001-父.md"
    child = tmp_path / "T-X-001.1-子.md"
    rl.write_file(str(parent), rl.serialize(
        {"id": "T-X-001", "title": "父", "status": "todo", "verify": ""}, "## 目标\n父"))
    rl.write_file(str(child), rl.serialize(
        {"id": "T-X-001.1", "parent": "T-X-001", "title": "子", "status": "done", "verify": ""},
        "## 目标\n子"))

    rl.compute_status(rl.scan(str(tmp_path)))          # 子全 done → 父应派生为 done

    text = parent.read_text(encoding="utf-8")
    assert "status: done" in text
    assert b"\r" not in parent.read_bytes()


# ───────────────────── 派生父 depends_on（2026-09-28 机制） ─────────────────────
# 「父 depends_on ＝ children 并集去掉指向父自身子树的边」原只写在文档，工具既不派生也不校验：
# 拆子任务后父该项只能手填，T-L1-005 手填 [T-L1-001]（其子根本不涉 L1-001）即漂移实例。
# 现由 derive() 按活跃区重算，规则函数 derive_parent_deps 与 verify_docs 检查 9 共用。

def test_parent_depends_on_derived_from_children_union(tmp_path, monkeypatch):
    """父 depends_on ＝直接子并集，剔掉指向父自身子树的边（兄弟边 / 父自身）。"""
    monkeypatch.setattr(rl, "PM", str(tmp_path))
    p  = tmp_path / "T-L1-001-父.md"
    c1 = tmp_path / "T-L1-001.1-甲.md"
    c2 = tmp_path / "T-L1-001.2-乙.md"
    rl.write_file(str(p), rl.serialize(
        {"id": "T-L1-001", "title": "父", "status": "todo", "depends_on": "[]", "verify": ""},
        "## 目标\n父"))
    rl.write_file(str(c1), rl.serialize(
        {"id": "T-L1-001.1", "parent": "T-L1-001", "title": "甲", "status": "done",
         "depends_on": "[T-L0-001, T-L1-001.2]", "verify": ""}, "## 目标\n甲"))    # 兄弟边
    rl.write_file(str(c2), rl.serialize(
        {"id": "T-L1-001.2", "parent": "T-L1-001", "title": "乙", "status": "done",
         "depends_on": "[T-L0-002]", "verify": ""}, "## 目标\n乙"))

    rl.compute_status(rl.scan(str(tmp_path)))

    fm, _ = rl.read_fm(str(p))
    assert rl.fm_list(fm, "depends_on") == ["T-L0-001", "T-L0-002"]   # 兄弟边剔除，外部并集保留
    assert "status: done" in p.read_text(encoding="utf-8")            # 状态照样派生
    assert b"\r" not in p.read_bytes()                                # 写盘仍 LF


def test_parent_depends_on_order_only_diff_is_not_written(tmp_path, monkeypatch):
    """集合相同、仅顺序不同 → 不写盘（顺序属笔误级噪音，不该产生 churn）。"""
    monkeypatch.setattr(rl, "PM", str(tmp_path))
    p = tmp_path / "T-L1-002-父.md"
    c = tmp_path / "T-L1-002.1-子.md"
    rl.write_file(str(p), rl.serialize(
        {"id": "T-L1-002", "title": "父", "status": "todo",
         "depends_on": "[T-L0-002, T-L0-001]", "verify": ""}, "## 目标\n父"))
    rl.write_file(str(c), rl.serialize(
        {"id": "T-L1-002.1", "parent": "T-L1-002", "title": "子", "status": "todo",
         "depends_on": "[T-L0-001, T-L0-002]", "verify": ""}, "## 目标\n子"))

    before = p.read_bytes()
    rl.compute_status(rl.scan(str(tmp_path)))

    assert p.read_bytes() == before


def test_parent_depends_on_never_becomes_self_referential(tmp_path, monkeypatch):
    """子依赖父自身 → 该边剔除；若把父 id 留在子树外会派生出自我依赖、被依赖图判环。"""
    monkeypatch.setattr(rl, "PM", str(tmp_path))
    p = tmp_path / "T-L1-003-父.md"
    c = tmp_path / "T-L1-003.1-子.md"
    rl.write_file(str(p), rl.serialize(
        {"id": "T-L1-003", "title": "父", "status": "todo", "depends_on": "[T-L0-001]", "verify": ""},
        "## 目标\n父"))
    rl.write_file(str(c), rl.serialize(
        {"id": "T-L1-003.1", "parent": "T-L1-003", "title": "子", "status": "todo",
         "depends_on": "[T-L1-003]", "verify": ""}, "## 目标\n子"))

    rl.compute_status(rl.scan(str(tmp_path)))

    fm, _ = rl.read_fm(str(p))
    assert rl.fm_list(fm, "depends_on") == []


def test_archived_parent_depends_on_is_frozen(tmp_path, monkeypatch):
    """归档＝冻结历史，不派生 depends_on（同检查 5/6 的历史豁免口径）。"""
    monkeypatch.setattr(rl, "PM", str(tmp_path))
    done = tmp_path / "tasks" / "done" / "M0"
    done.mkdir(parents=True)
    p = done / "T-L1-004-父.md"
    c = tmp_path / "T-L1-004.1-子.md"
    rl.write_file(str(p), rl.serialize(
        {"id": "T-L1-004", "title": "父", "status": "done", "depends_on": "[T-L0-001]", "verify": ""},
        "## 目标\n父"))
    rl.write_file(str(c), rl.serialize(
        {"id": "T-L1-004.1", "parent": "T-L1-004", "title": "子", "status": "done",
         "depends_on": "[T-L0-002]", "verify": ""}, "## 目标\n子"))

    rl.compute_status(rl.scan(str(tmp_path)))

    fm, _ = rl.read_fm(str(p))
    assert rl.fm_list(fm, "depends_on") == ["T-L0-001"]   # 未被并集 [T-L0-002] 覆盖


def test_ledger_shows_derived_parent_deps(tmp_path, monkeypatch):
    """账本渲染读的是 derive() 改过的同一份内存 dict——不同步内存会渲染出旧值。"""
    tasks = tmp_path / "tasks"
    tasks.mkdir()
    for name, val in (("PM", str(tmp_path)), ("TASKS", str(tasks)),
                      ("DONE", str(tasks / "done")), ("LEDGER", str(tmp_path / "任务账本.md"))):
        monkeypatch.setattr(rl, name, val)
    p = tasks / "T-L1-005-父.md"
    c = tasks / "T-L1-005.1-子.md"
    rl.write_file(str(p), rl.serialize(
        {"id": "T-L1-005", "title": "父", "priority": "P0", "milestone": "M0",
         "status": "todo", "depends_on": "[T-L9-999]", "verify": ""}, "## 目标\n父"))
    rl.write_file(str(c), rl.serialize(
        {"id": "T-L1-005.1", "parent": "T-L1-005", "title": "子", "priority": "P0", "milestone": "M0",
         "status": "done", "depends_on": "[T-L0-002]", "verify": ""}, "## 目标\n子"))

    text, *_ = rl.build_ledger()

    assert "T-L0-002" in text and "T-L9-999" not in text


# ───────────────────────── INT 层识别 ─────────────────────────

def test_int_layer_recognized():
    assert rl.ID_RE.match("T-INT-001")
    assert rl.layer_of("T-INT-003") == "INT"
    assert "INT" in rl.LAYER_ORDER
    assert rl.LAYER_ORDER["INT"] == len(rl.LAYERS) - 1   # 层表置末（账本分区顺序），但就绪集排序不看它


def test_ready_sort_gate_first_within_priority():
    """就绪集同优先级下 T-INT-* 取队首（工作流「调度规则」§2）。"""
    gate = {"id": "T-INT-001", "priority": "P0"}
    l2   = {"id": "T-L2-001",  "priority": "P0"}
    l4   = {"id": "T-L4-001",  "priority": "P0"}
    eco  = {"id": "T-ECO-001", "priority": "P1"}
    l5   = {"id": "T-L5-001",  "priority": "P1"}
    # 关卡先于同优先级的普通任务；关卡之后层号序仍生效
    assert rl.ready_sort_key(gate) < rl.ready_sort_key(l2) < rl.ready_sort_key(l4)
    # 优先级仍是首键：P0 关卡先于一切 P1
    assert rl.ready_sort_key(gate) < rl.ready_sort_key(eco)
    # 同优先级内按层号；ECO 已置于 L4 之后，故先于 L5
    assert rl.ready_sort_key(eco) < rl.ready_sort_key(l5)


def test_eco_layer_sits_after_l4():
    """ECO 层置于 L4 之后、L5 之前（决策日志 D-044）；INT 仍居末。"""
    assert rl.LAYER_ORDER["L4"] < rl.LAYER_ORDER["ECO"] < rl.LAYER_ORDER["L5"] < rl.LAYER_ORDER["L6"]
    assert rl.LAYER_ORDER["INT"] == len(rl.LAYERS) - 1


def test_ui_layer_sits_between_l3_and_l4():
    """UI 层（跨层表现层）置于 L3 之后、L4 之前（决策日志 D-060）；INT 仍居末。"""
    assert rl.ID_RE.match("T-UI-001")
    assert rl.layer_of("T-UI-001.2") == "UI"
    assert "UI" in rl.LAYER_ORDER
    assert rl.LAYER_ORDER["L3"] < rl.LAYER_ORDER["UI"] < rl.LAYER_ORDER["L4"]
    assert rl.LAYER_ORDER["INT"] == len(rl.LAYERS) - 1


def test_agt_layer_sits_after_ui():
    """AGT 层（跨层受控自主运行时）置于 UI 之后、L4 之前（决策日志 D-090）；INT 仍居末。"""
    assert rl.ID_RE.match("T-AGT-001")
    assert rl.layer_of("T-AGT-001.2") == "AGT"
    assert "AGT" in rl.LAYER_ORDER
    assert rl.LAYER_ORDER["UI"] < rl.LAYER_ORDER["AGT"] < rl.LAYER_ORDER["L4"]
    assert rl.LAYER_ORDER["INT"] == len(rl.LAYERS) - 1


def test_milestones_registered():
    """M0–M5 既有里程碑 + M6 / M7 已登记（决策日志 D-090 / D-103）——账本里程碑视图据它成行。"""
    assert [m[0] for m in rl.MILESTONES] == [
        "M0", "M1", "M2", "M3", "M4", "M5", "M6", "M7",
    ]
    assert {m[0]: m[1] for m in rl.MILESTONES}["M5"] == "受控自主"
    assert [m[1] for m in rl.MILESTONES[-2:]] == ["界面完备", "打包发布"]


# ───────────────────────── archive：移动后改正相对链接 ─────────────────────────
# 2026-09-27 加固：archive 只做 shutil.move，而任务文件里全是相对链接（`../../docs/`、
# 邻居任务、`../工作流.md`）——搬深两层即 815 条断链。故 archive 同批改正链接。

def test_link_path_skips_non_relative():
    assert rl._link_path("../../docs/a.md") == "../../docs/a.md"
    assert rl._link_path("../tasks/T-X-001.md#锚") == "../tasks/T-X-001.md"
    for raw in ("#锚", "https://e.com/a", "mailto:a@b", "/abs/a.md", ""):
        assert rl._link_path(raw) is None


def test_retarget_picks_first_existing_and_is_idempotent(tmp_path):
    here = tmp_path / "项目管理/tasks/done/M0"      # 比 tasks/ 深两层
    here.mkdir(parents=True)
    (tmp_path / "docs").mkdir(); (tmp_path / "docs/d.md").write_text("x", encoding="utf-8")
    # 候选二 = 目标没搬走，仍是 tasks/<p>（相对此处即多深两层 → ../../../../docs/d.md）
    cands = lambda p: [str(here / p), str(tmp_path / "项目管理/tasks" / p)]     # noqa: E731
    once = rl._retarget("见 [01](../../docs/d.md)", str(here), cands)
    assert "(../../../../docs/d.md)" in once
    assert rl._retarget(once, str(here), cands) == once          # 幂等：已成立则不动


def test_retarget_leaves_broken_link_alone(tmp_path):
    """候选全不存在 → 原样保留（留给 verify_docs 报断链，不静默猜）。"""
    here = tmp_path / "任务"
    here.mkdir()
    text = "见 [无](../../nowhere.md)"
    assert rl._retarget(text, str(here), lambda p: [str(here / p)]) == text


def test_retarget_handles_link_text_containing_brackets(tmp_path):
    """链接文字里的行内代码常带方括号（`list[str]` / `Mapping[str, Any]`）——正则不得提前收口。

    回归（2026-10-04 归档 M2 实测）：链接文字用 ``[^\\]]*`` 会在 ``Any]`` 处截断、与 ``](``
    失配，整条链接被**静默漏改**（``verify_docs`` 仍报得出，因为它先 ``strip_code`` 去掉
    行内代码、文字退化为空；本处要逐字节保留原文，故不能照搬那一步）。
    """
    here = tmp_path / "项目管理/tasks/done/M2"           # 比 tasks/ 深两层
    here.mkdir(parents=True)
    (tmp_path / "src").mkdir()
    (tmp_path / "src/r.py").write_text("x", encoding="utf-8")

    def cands(p):
        return [str(here / p), str(tmp_path / "项目管理/tasks" / p)]

    text = (
        "见 [`run(..., Mapping[str, Any])`](../../src/r.py) "
        "与 [`codes: tuple[str, ...]`](../../src/r.py)。"
    )
    fixed = rl._retarget(text, str(here), cands)
    assert fixed.count("(../../../../src/r.py)") == 2, "两条链接都要改到"
    assert "](../../src/r.py)" not in fixed
    assert rl._retarget(fixed, str(here), cands) == fixed          # 幂等


def _mini_repo(tmp_path):
    """迷你仓库：一个已完成 M0 任务（带两类相对链接）+ 一个 M1 任务 + 一个外部引用页。"""
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/d.md").write_text("# 契约\n", encoding="utf-8")
    pm = tmp_path / "项目管理"
    tasks = pm / "tasks"
    tasks.mkdir(parents=True)
    # 账本渲染会引用 LAYERS 里的 Story 链接——把目标建出来，否则断链检查假阳
    for _c, _h, story, _m in rl.LAYERS:
        for tgt in re.findall(r"\]\(([^)]+)\)", story):
            doc = pm / tgt
            doc.parent.mkdir(parents=True, exist_ok=True)
            doc.write_text("# 占位\n", encoding="utf-8")
    (tasks / "T-L1-001-甲.md").write_text(rl.serialize(
        {"id": "T-L1-001", "title": "甲", "priority": "P0", "milestone": "M0",
         "parent": "", "depends_on": "[]", "status": "done", "verify": ""},
        "## 涉及契约\n[01](../../docs/d.md)\n\n## 参考\n[乙](T-L1-002-乙.md)\n"),
        encoding="utf-8")
    (tasks / "T-L1-002-乙.md").write_text(rl.serialize(
        {"id": "T-L1-002", "title": "乙", "priority": "P1", "milestone": "M1",
         "parent": "", "depends_on": "[]", "status": "todo", "verify": ""},
        "## 目标\n乙\n"), encoding="utf-8")
    notes = pm / "notes.md"
    notes.write_text("[甲](tasks/T-L1-001-甲.md) [乙](tasks/T-L1-002-乙.md)\n", encoding="utf-8")
    return pm, tasks, notes


def _patch_paths(monkeypatch, tmp_path, tasks, pm):
    """把工具的全部路径全局量指到迷你仓库（``PM`` 也须指，``scan`` 用它算 rel）。"""
    for name, val in (("PM", str(pm)), ("TASKS", str(tasks)), ("DONE", str(tasks / "done")),
                      ("LEDGER", str(pm / "任务账本.md")), ("ROOT", str(tmp_path))):
        monkeypatch.setattr(rl, name, val)
    monkeypatch.setattr(rl, "MILESTONES", [("M0", "甲", "目标")])
    monkeypatch.setattr(vd, "ROOT", str(tmp_path))


def test_archive_rewrites_links_and_leaves_no_broken(tmp_path, monkeypatch):
    pm, tasks, notes = _mini_repo(tmp_path)
    _patch_paths(monkeypatch, tmp_path, tasks, pm)

    rl.archive(False)

    moved = tasks / "done/M0/T-L1-001-甲.md"
    assert moved.exists() and not (tasks / "T-L1-001-甲.md").exists()
    text = moved.read_text(encoding="utf-8")
    assert "(../../../../docs/d.md)" in text     # 没搬走的：只多深两层
    assert "(../../T-L1-002-乙.md)" in text      # 跨里程碑的邻居：仍指 tasks/
    # 外部引用页：指向本批的改、指向别处的分毫不动
    n = notes.read_text(encoding="utf-8")
    assert "[甲](tasks/done/M0/T-L1-001-甲.md)" in n
    assert "[乙](tasks/T-L1-002-乙.md)" in n
    # 账本随移动重生（派生视图），且全仓 0 断链
    assert (pm / "任务账本.md").exists()
    assert vd.check_links()[2] == []


def test_archive_dry_moves_nothing(tmp_path, monkeypatch):
    pm, tasks, notes = _mini_repo(tmp_path)
    _patch_paths(monkeypatch, tmp_path, tasks, pm)

    rl.archive(True)

    assert (tasks / "T-L1-001-甲.md").exists()
    assert not (tasks / "done").exists()


# ───────────────────────── 检查 6：接口面 ─────────────────────────

@pytest.fixture()
def fake_tasks(monkeypatch):
    """用合成任务替掉真实 tasks/ 扫描，让检查逻辑可单独测。"""
    def _install(tasks, bodies):
        monkeypatch.setattr(rl, "scan", lambda root: tasks)
        monkeypatch.setattr(rl, "read_fm", lambda path: (bodies.get(path, {}), bodies.get(path + "::body", "")))
    return _install


def test_interface_placeholder_is_flagged(fake_tasks):
    fake_tasks(
        [{"id": "T-X-001", "status": "done", "parent": "", "path": "f.md", "milestone": "M0"}],
        {"f.md::body": "## 目标\n示例\n\n## 接口面\n- 暂无（④ 对齐时补）\n"},
    )
    assert vd.check_interfaces() == [("T-X-001", "接口面节仍为占位，未实填")]


def test_interface_missing_section_is_flagged(fake_tasks):
    fake_tasks(
        [{"id": "T-X-002", "status": "doing", "parent": "", "path": "g.md", "milestone": "M0"}],
        {"g.md::body": "## 目标\n示例\n"},
    )
    assert vd.check_interfaces() == [("T-X-002", "缺 `## 接口面` 节")]


def test_interface_filled_passes(fake_tasks):
    fake_tasks(
        [{"id": "T-X-003", "status": "done", "parent": "", "path": "h.md", "milestone": "M0"}],
        {"h.md::body": "## 接口面\n- 输入：T-L0-005 的 `MarketDb.query`\n- 输出：`SkillRunner.run`\n"},
    )
    assert vd.check_interfaces() == []


def test_section_with_domain_word_is_not_a_placeholder(fake_tasks):
    """「待补」是领域词（离线待补偿的到期点），不是占位标记——不得误判（T-L1-005.3）。"""
    body = ("## 接口面\n- 输入：`EgressGateway.online`\n- 输出：待补集落 `config/scheduler/`\n\n"
            "## 假设与前提\n- A1 离线判定取网关单一源头，待补项按到期点登记\n")
    fake_tasks(
        [{"id": "T-X-005", "status": "done", "parent": "", "path": "j.md", "milestone": "M0"}],
        {"j.md::body": body},
    )
    assert vd.check_interfaces() == []
    assert vd.check_assumptions() == []


def test_todo_task_exempt_from_section_checks(fake_tasks):
    fake_tasks(
        [{"id": "T-X-004", "status": "todo", "parent": "", "path": "i.md", "milestone": "M0"}],
        {"i.md::body": "## 目标\n未开工\n"},
    )
    assert vd.check_interfaces() == []


# ───────────────────────── 检查 7：集成关卡 ─────────────────────────

def _leaf(tid, milestone="M0", path=None):
    return {"id": tid, "status": "todo", "parent": "", "milestone": milestone,
            "depends_on": [], "path": path or f"{tid}.md"}


def _gate(deps, milestone="M0"):
    return {"id": "T-INT-001", "status": "todo", "parent": "", "milestone": milestone,
            "depends_on": deps, "path": "T-INT-001.md"}


def test_gate_missing_is_flagged(fake_tasks):
    fake_tasks([_leaf("T-L1-001")], {})
    assert [i[0] for i in vd.check_integration_gates()] == ["M0"]


def test_gate_deps_must_cover_milestone_leaves(fake_tasks):
    fake_tasks([_leaf("T-L1-001"), _leaf("T-L1-002"), _gate(["T-L1-001"])], {})
    issues = vd.check_integration_gates()
    assert len(issues) == 1 and issues[0][0] == "T-INT-001"
    assert "T-L1-002" in issues[0][1]


def test_gate_covering_all_leaves_passes(fake_tasks):
    fake_tasks([_leaf("T-L1-001"), _leaf("T-L1-002"), _gate(["T-L1-001", "T-L1-002"])], {})
    assert vd.check_integration_gates() == []


def test_gate_skip_task_is_exempt(fake_tasks):
    """长跑/运营任务标 `gate: skip` 后不进关卡依赖覆盖。"""
    fake_tasks(
        [_leaf("T-L1-001"), _leaf("T-L0-007", path="skip.md"), _gate(["T-L1-001"])],
        {"skip.md": {"gate": "skip"}},
    )
    assert vd.check_integration_gates() == []


# ───────────────────────── 检查 8：遗留销账 ─────────────────────────
# 归属任务做完了、册内条目却还在 —— 条目会静默滞留，正是 L0 册 C1/C2 的成因。

_LEGACY_HEAD = "| # | 遗留内容 | 来源 | 归属 | 解封条件 |\n|---|---|---|---|---|\n"


def _registry(tmp_path, body, name="L9-遗留问题.md"):
    d = tmp_path / "遗留问题"
    d.mkdir(exist_ok=True)
    (d / name).write_text(body, encoding="utf-8")
    return str(d)


def _task(tid, status):
    return {"id": tid, "status": status, "parent": "", "path": f"{tid}.md", "milestone": "M0"}


def test_legacy_entry_whose_owner_is_done_is_flagged(fake_tasks, tmp_path, monkeypatch):
    fake_tasks([_task("T-L0-001", "done")], {})
    monkeypatch.setattr(vd, "LEGACY_DIR", _registry(tmp_path, _LEGACY_HEAD +
        "| A1 | 归属方已做完却未销账 | [T-L0-001] | `T-L0-001` | T-L0-001 `done` |\n"))
    assert vd.check_legacy_settlement() == [
        ("L9-遗留问题.md · A1", "归属/解封条件 T-L0-001 已全 done，条目未销账")]


def test_legacy_entry_whose_owner_is_open_passes(fake_tasks, tmp_path, monkeypatch):
    """来源列里的已 done 任务不算（那是留痕不是归属）。"""
    fake_tasks([_task("T-L0-001", "done"), _task("T-L2-001", "todo")], {})
    monkeypatch.setattr(vd, "LEGACY_DIR", _registry(tmp_path, _LEGACY_HEAD +
        "| A1 | 归属方还没轮到 | [T-L0-001] | `T-L2-001` | T-L2-001 `done` |\n"))
    assert vd.check_legacy_settlement() == []


def test_legacy_closed_section_and_unborn_task_are_ignored(fake_tasks, tmp_path, monkeypatch):
    """「已闭（备查）」之后不再判；册内点到尚不存在的任务 id（如待立项）也不判。"""
    fake_tasks([_task("T-L0-001", "done")], {})
    monkeypatch.setattr(vd, "LEGACY_DIR", _registry(tmp_path, _LEGACY_HEAD +
        "| A1 | 任务尚不存在 | — | 建议新立 `T-L0-099` | 立项后 |\n"
        "\n## 已闭（备查）\n\n"
        "- **A2** 归属 `T-L0-001` 但已闭，不该判\n"))
    assert vd.check_legacy_settlement() == []


# ───────────────────────── 检查 9：父依赖派生 ─────────────────────────
# 活跃区父的 depends_on 须等于直接子并集（去父自身子树）——手填漂移即在此暴露。

def _t(tid, deps, parent=""):
    return {"id": tid, "status": "done", "parent": parent, "milestone": "M0",
            "depends_on": deps, "path": f"{tid}.md"}


def test_parent_deps_mismatch_is_flagged(fake_tasks):
    fake_tasks([_t("T-L1-001", []), _t("T-L1-001.1", ["T-L0-001"], "T-L1-001"),
                _t("T-L1-001.2", ["T-L0-002"], "T-L1-001")], {})
    assert vd.check_parent_deps() == [
        ("T-L1-001", "父 depends_on 与子叶并集不一致：应为 T-L0-001, T-L0-002")]


def test_parent_deps_consistent_passes(fake_tasks):
    """集合相同、顺序不同不算漂移。"""
    fake_tasks([_t("T-L1-002", ["T-L0-002", "T-L0-001"]),
                _t("T-L1-002.1", ["T-L0-001"], "T-L1-002"),
                _t("T-L1-002.2", ["T-L0-002"], "T-L1-002")], {})
    assert vd.check_parent_deps() == []


def test_parent_deps_ignores_intra_subtree_edges(fake_tasks):
    """子依赖兄弟 / 依赖父自身，皆不进父的外部依赖。"""
    fake_tasks([_t("T-L1-003", []), _t("T-L1-003.1", [], "T-L1-003"),
                _t("T-L1-003.2", ["T-L1-003.1", "T-L1-003"], "T-L1-003")], {})
    assert vd.check_parent_deps() == []


def test_leaf_without_children_is_not_checked(fake_tasks):
    fake_tasks([_t("T-L1-004", ["T-L0-001"])], {})
    assert vd.check_parent_deps() == []


# ───────────────────────── 检查 1：断链扫描的目录跳过 ─────────────────────────
# 历史现象（2026-09-27）：临时目录里的草稿 .md（如 temp/ 下的外部仓库快照）指向本项目
# 不存在的文件，让 `--strict` 在本地恒红——而该目录在 .gitignore 里，永不可提交。

def test_draft_dirs_are_not_scanned(tmp_path, monkeypatch):
    (tmp_path / "target.md").write_text("目标", encoding="utf-8")
    (tmp_path / "ok.md").write_text("[目标](target.md)[再指一次](target.md)", encoding="utf-8")
    for d in ("tmp", "temp"):
        (tmp_path / d).mkdir()
        (tmp_path / d / "draft.md").write_text("[坏](不存在的文件.md)", encoding="utf-8")
    monkeypatch.setattr(vd, "ROOT", str(tmp_path))

    md, cnt, broken = vd.check_links()
    assert md == 2                     # ok.md + target.md；草稿目录不参与计数
    assert cnt == 2
    assert broken == []                # 草稿里的断链不上报

def test_git_dir_is_not_scanned(tmp_path, monkeypatch):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "x.md").write_text("[坏](无.md)", encoding="utf-8")
    monkeypatch.setattr(vd, "ROOT", str(tmp_path))

    assert vd.check_links() == (0, 0, [])


# ───────────────────────── convert_store CLI（T-L0-018.1） ─────────────────────────

def test_convert_store_cli_roundtrip(tmp_path, monkeypatch):
    """明 → 密 → 明：口令只经环境变量，读回内容不变。"""
    import convert_store as cs
    from st_agent.l0.storage import Store, read_mode

    plain = tmp_path / "plain"
    Store.create(plain).put("memory", "a.json", b"x")
    enc, back = tmp_path / "enc", tmp_path / "back"
    monkeypatch.setenv(cs.TARGET_PASSPHRASE_ENV, "target-pass")
    assert cs.main(["--source", str(plain), "--target", str(enc), "--to", "encrypted"]) == 0
    assert read_mode(enc) == "encrypted"

    monkeypatch.delenv(cs.TARGET_PASSPHRASE_ENV)
    monkeypatch.setenv(cs.SOURCE_PASSPHRASE_ENV, "target-pass")
    assert cs.main(["--source", str(enc), "--target", str(back), "--to", "plain"]) == 0
    assert Store.open(back).get("memory", "a.json") == b"x"


def test_convert_store_cli_requires_target_passphrase(tmp_path, monkeypatch):
    import convert_store as cs
    from st_agent.l0.storage import Store

    src = tmp_path / "p"
    Store.create(src)
    monkeypatch.delenv(cs.TARGET_PASSPHRASE_ENV, raising=False)
    assert cs.main(["--source", str(src), "--target", str(tmp_path / "o"),
                    "--to", "encrypted"]) == 2
    assert not (tmp_path / "o").exists()          # 未开工即拒，不留半成品


def test_convert_store_cli_refuses_passphrase_in_argv(tmp_path):
    """口令**不得**经 argv（进程列表可见）——传了即用法错误。"""
    import convert_store as cs

    with pytest.raises(SystemExit):
        cs.main(["--source", str(tmp_path), "--target", str(tmp_path / "o"),
                 "--to", "plain", "--passphrase", "oops"])



# ───────────────────────── 测试树：模块名全树唯一 ─────────────────────────

def test_test_module_basenames_are_unique_across_the_tree():
    """**测试模块名在全树唯一**——``tests/`` 下各目录都没有 ``__init__.py``、pytest 走
    prepend 导入模式，故两个同名 ``test_*.py``（如 ``tests/l5/test_runtime.py`` 与
    ``tests/l6/test_runtime.py``）会让**全量**收集直接报 ``import file mismatch``：
    按层的范围套件各自跑得到，只有 ``pytest tests``（CI 的 ``push → main`` 兜底、
    与集成关卡 ⑥ 的强制全量）才炸——静默得很（auto-merge 不等这个 job）。
    本用例把该不变量钉在**恒随跑**的跨层套件里，新增测试文件时即时报错。
    """
    root = pathlib.Path(__file__).resolve().parent
    seen: dict[str, str] = {}
    clashes: list[str] = []
    for path in sorted(root.rglob("test_*.py")):
        if "__pycache__" in path.parts:
            continue
        rel = path.relative_to(root).as_posix()
        other = seen.setdefault(path.name, rel)
        if other != rel:
            clashes.append(f"{path.name}: {other} / {rel}")
    assert clashes == [], (
        "测试模块名冲突——pytest 全量收集会报 import file mismatch（改名为全树唯一即可）："
        f"{clashes}"
    )
