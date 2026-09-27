"""项目管理工具的最小回归测试（2026-09-26 加固）。

覆盖三处此前无测试的实现细节：
- ``render_ledger.serialize`` 回写任务文件时**不得丢字段**（未知键与空值键原样保留）——
  派生父状态会重写任务文件，丢字段属静默数据损坏；
- ``T-INT-*`` 集成关卡层被 ID_RE / layer_of / LAYER_ORDER 识别，且 `ready_sort_key`
  在同优先级内把关卡排到队首（2026-09-27 调度规则校正）；
- ``verify_docs`` 的三项新检查（接口面完整、集成关卡覆盖、遗留销账）与 `gate: skip` 例外；
- ``verify_docs`` 的断链扫描跳过 ``.git`` 与 gitignored 的 ``tmp/`` ``temp/`` 草稿目录
  （2026-09-27 加固）。
"""

from __future__ import annotations

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

