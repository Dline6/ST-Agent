"""T-ECO-001.2 测试：四类导出流程与分享卡片（09 §2 / §5）。

GWT 对照（任务文件 5 条）：
- GWT-1 四类导出（三类无确认门）共用同一套容器代码
- GWT-2 `.stmem` 强制三步：过滤 → 清单 → 用户确认
- GWT-3 命名 / 描述的中性化拦截（记忆节点原文不在此列）
- GWT-4 出处链追加（历史不覆盖）+ 分享卡片
- GWT-5 返回字节 + `save_to(path)`，全程不写 `Store`
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from eco_helpers import NOW, STOCK_ID, memory_node
from st_agent.contracts.capability_types import Provenance, SkillDescriptor
from st_agent.contracts.neutrality import NeutralityGuard
from st_agent.contracts.registry_types import SemVer
from st_agent.eco import (
    SHARE_EXTENSIONS,
    ShareCard,
    ShareContainer,
    ShareExportError,
    ShareExporter,
    appended_chain,
)
from st_agent.eco import card as card_module
from st_agent.eco import export as export_module
from st_agent.l0.storage import PARTITION_NAMES
from st_agent.l1.workflow.models import WorkflowDAG, WorkflowNode
from st_agent.l2.memory.errors import MemoryValidationError
from st_agent.l4.lens import JudgingCriteria

AUTHOR = "alice"
OFFICIAL_SKILL = "sk_st_list_sync_v1.0"


# ───────────────────────── 夹具：四类各备一个真件 ─────────────────────────

@pytest.fixture()
def custom_skill(skills):
    """一个自建 Skill（Studio 产物，`source=user-built`）。"""
    return skills.register(
        "sk_eco_demo", name="生态演示技能", description="用于导出演示的自建技能",
        input_schema={"type": "object", "properties": {}},
        output_schema={"type": "object", "properties": {}},
        source="user-built",
    )


@pytest.fixture()
def custom_flow(workflows):
    """一条自建工作流（单节点引用官方 Skill，过 `validate_dag`）。"""
    return workflows.save(
        WorkflowDAG(
            flow_id="wf_eco_demo_v1.0",
            name="生态演示工作流",
            description="用于导出演示的自建工作流",
            version=SemVer(major=1, minor=0),
            nodes=(WorkflowNode(node_id="n1", skill_id=OFFICIAL_SKILL),),
        )
    )


@pytest.fixture()
def custom_lens(lenses):
    """一个自定义视角（`LensRoster.add_custom` 产物）。"""
    return lenses.add_custom(
        name="生态演示视角", description="用于导出演示的自定义视角",
        skill_bundle=(OFFICIAL_SKILL,),
        judging_criteria=JudgingCriteria(natural="低估值优先"),
    )


@pytest.fixture()
def seeded_graph(graph, writer):
    """图谱里放一条公开节点 + 一条私有节点（`.stmem` 三步的判据面）。"""
    public = writer.add_node(memory_node(privacy="public"))
    private = writer.add_node(memory_node(privacy="private"))
    return {"public": public, "private": private}


# ───────────────────────── GWT-1 ─────────────────────────

class TestGwt1ExportsFourKinds:
    def test_export_skill(self, exporter, custom_skill):
        container = exporter.export_skill(custom_skill.skill_id, author=AUTHOR, sharer="bob")
        assert container.share_type == "skill"
        assert container.payload == custom_skill
        assert container.manifest.dependencies == custom_skill.dependencies
        assert container.manifest.checksum
        assert ShareContainer.from_bytes(container.to_bytes()).payload == custom_skill

    def test_export_flow_declares_node_skills_as_dependencies(self, exporter, custom_flow):
        container = exporter.export_flow(custom_flow.flow_id, author=AUTHOR)
        assert container.share_type == "flow"
        assert container.payload == custom_flow
        assert container.manifest.dependencies == (OFFICIAL_SKILL,)

    def test_export_lens_declares_bundle_as_dependencies(self, exporter, custom_lens):
        container = exporter.export_lens(custom_lens.lens_id, author=AUTHOR)
        assert container.share_type == "lens"
        assert container.payload == custom_lens
        assert container.manifest.dependencies == (OFFICIAL_SKILL,)

    def test_four_kinds_share_one_code_path(self, exporter, custom_skill, custom_flow,
                                            custom_lens, seeded_graph):
        containers = [
            exporter.export_skill(custom_skill.skill_id, author=AUTHOR),
            exporter.export_flow(custom_flow.flow_id, author=AUTHOR),
            exporter.export_lens(custom_lens.lens_id, author=AUTHOR),
            exporter.export_memory(author=AUTHOR, confirmed_by="user"),
        ]
        assert {c.share_type for c in containers} == {"skill", "flow", "lens", "mem"}
        assert [c.extension for c in containers] == [SHARE_EXTENSIONS[k] for k in
                                                     ("skill", "flow", "lens", "mem")]
        # 四类都经同一条容器路径：逐类读回一致（不是四份实现各走各的）
        for container in containers:
            assert ShareContainer.from_bytes(container.to_bytes()).payload == container.payload


# ───────────────────────── GWT-2：`.stmem` 强制三步 ─────────────────────────

class TestGwt2MemoryThreeSteps:
    def test_plan_filters_private_and_reports_counts(self, exporter, seeded_graph):
        plan = exporter.plan_memory()
        assert plan.excluded_nodes == 1
        assert [n.memory_node_id for n in plan.payload.nodes] == [
            seeded_graph["public"].memory_node_id
        ]
        assert any("已剔除私有 / 敏感节点 1 条" == line for line in plan.included_summary)

    def test_summary_carries_no_node_text(self, exporter, seeded_graph):
        plan = exporter.plan_memory()
        assert all(seeded_graph["public"].view not in line for line in plan.included_summary)

    def test_export_requires_user_confirmation(self, exporter, seeded_graph):
        with pytest.raises(MemoryValidationError, match="confirmed_by"):
            exporter.export_memory(author=AUTHOR, confirmed_by="system")

    def test_exported_payload_matches_the_shown_plan(self, exporter, seeded_graph):
        """清单与载荷同源：第三步产出的容器载荷＝第二步展示的那一份（叶 A1）。"""
        plan = exporter.plan_memory()
        container = exporter.export_memory(author=AUTHOR, confirmed_by="user")
        assert container.share_type == "mem"
        assert container.payload == plan.payload

    def test_empty_graph_exports_empty_fragment(self, exporter):
        container = exporter.export_memory(author=AUTHOR, confirmed_by="user")
        assert container.payload.nodes == () and container.payload.edges == ()


# ───────────────────────── GWT-3：中性化拦截 ─────────────────────────

class _StubRegistry:
    """只回一个对象的取材面（模拟「库里的存量对象命名不合规」——注册期校验只对
    新注册生效，规则库更新后旧对象仍在；导出前的拦截是最后一道线）。"""

    def __init__(self, obj):
        self._obj = obj

    def get(self, _id):
        return self._obj


class TestGwt3NeutralityInterception:
    def test_persona_skill_name_blocks_export(self, custom_skill):
        persona_name = "老张的选股管家"   # 人名 + 拟人后缀（01 §6 两个命中）
        persona = SkillDescriptor(**{**custom_skill.model_dump(), "name": persona_name})
        exporter = ShareExporter(skills=_StubRegistry(persona), now=lambda: NOW)
        with pytest.raises(ShareExportError, match="中性化校验"):
            exporter.export_skill(persona.skill_id, author=AUTHOR)

    def test_first_person_description_blocks_export(self, custom_lens):
        offender = custom_lens.model_copy(update={"description": "我会帮你挑选标的"})
        exporter = ShareExporter(lenses=_StubRegistry(offender), now=lambda: NOW)
        with pytest.raises(ShareExportError, match="中性化校验"):
            exporter.export_lens(offender.lens_id, author=AUTHOR)

    def test_compliant_objects_pass(self, exporter, custom_skill, custom_flow, custom_lens):
        assert exporter.export_skill(custom_skill.skill_id, author=AUTHOR).share_type == "skill"
        assert exporter.export_flow(custom_flow.flow_id, author=AUTHOR).share_type == "flow"
        assert exporter.export_lens(custom_lens.lens_id, author=AUTHOR).share_type == "lens"

    def test_memory_node_text_is_not_neutrality_checked(self, exporter, writer):
        """记忆本体作数据展示、不过输出校验（D-053）——原文原样导出（叶 A2）。"""
        node = writer.add_node(memory_node(privacy="public", view="我认为会反转"))
        container = exporter.export_memory(author=AUTHOR, confirmed_by="user")
        restored = container.payload.nodes
        assert [n.view for n in restored] == ["我认为会反转"]
        assert restored[0].memory_node_id == node.memory_node_id


# ───────────────────────── GWT-4：出处链与卡片 ─────────────────────────

class TestGwt4ProvenanceChainAndCard:
    def test_skill_imported_from_others_appends_a_hop(self, skills, exporter):
        imported = skills.register(
            "sk_eco_imported", name="导入来的技能", description="由他人分享后导入的技能",
            input_schema={}, output_schema={}, source="imported",
            provenance=Provenance(
                sharer="bob", imported_at=NOW.isoformat(), checksum="a" * 64,
                origin_chain=("carol",),
            ),
        )
        container = exporter.export_skill(imported.skill_id, author=AUTHOR, sharer="alice")
        assert container.manifest.provenance.sharer == "alice"
        assert container.manifest.provenance.origin_chain == ("bob", "carol")

    def test_explicit_previous_covers_types_without_provenance(self, exporter, custom_flow):
        previous = Provenance(sharer="bob", origin_chain=("carol",))
        container = exporter.export_flow(
            custom_flow.flow_id, author=AUTHOR, sharer="alice", previous=previous
        )
        assert container.manifest.provenance.origin_chain == ("bob", "carol")

    def test_chain_history_is_not_overwritten(self, exporter, custom_lens):
        previous = Provenance(sharer="bob", origin_chain=("carol", "dave"))
        container = exporter.export_lens(
            custom_lens.lens_id, author=AUTHOR, previous=previous
        )
        assert container.manifest.provenance.origin_chain == ("bob", "carol", "dave")

    def test_fresh_artifact_has_empty_chain(self, exporter, custom_skill):
        container = exporter.export_skill(custom_skill.skill_id, author=AUTHOR)
        assert container.manifest.provenance.origin_chain == ()

    @pytest.mark.parametrize(
        "previous,expected",
        [
            (None, ()),
            (Provenance(), ()),
            (Provenance(sharer="bob"), ("bob",)),
            (Provenance(origin_chain=("bob",)), ("bob",)),
            (Provenance(sharer="bob", origin_chain=("carol",)), ("bob", "carol")),
        ],
    )
    def test_appended_chain_rule(self, previous, expected):
        assert appended_chain(previous) == expected

    def test_card_carries_description_checksum_and_hint(self, exporter, custom_skill):
        container = exporter.export_skill(custom_skill.skill_id, author=AUTHOR)
        card = exporter.card_for(container)
        assert card.title == custom_skill.name
        assert card.description == custom_skill.description
        assert card.checksum == container.checksum()
        assert card.file_name == f"{custom_skill.skill_id}.stskill"
        assert "导入前请核对" in card.download_hint

    def test_memory_card_uses_fixed_neutral_wording(self, exporter, seeded_graph):
        card = exporter.card_for(exporter.export_memory(author=AUTHOR, confirmed_by="user"))
        assert card.file_name.startswith("mem_") and card.file_name.endswith(".stmem")
        assert card.title == "记忆公开片段"

    def test_card_text_is_neutral(self, exporter, custom_skill, custom_lens, seeded_graph):
        guard = NeutralityGuard()
        for container in (
            exporter.export_skill(custom_skill.skill_id, author=AUTHOR),
            exporter.export_lens(custom_lens.lens_id, author=AUTHOR),
            exporter.export_memory(author=AUTHOR, confirmed_by="user"),
        ):
            verdict = guard.check_output(exporter.card_for(container).to_text())
            assert verdict.passed, verdict.findings

    def test_card_and_export_cannot_reach_the_network(self):
        """卡片是纯文本产出、产品不经手发布（09 §6）——两层模块的 import 面里没有任何出网件。

        AST 断言而非「跑一次看有没有发包」：出网能力若在 import 面里不存在，
        就不必靠运行时观测去证伪（同仓 [`T-L1-004`](../../项目管理/tasks/done/M0/T-L1-004-官方SkillPack公共认知主动服务Bundl.md)
        GWT-5 的先例）。
        """
        forbidden_roots = {"socket", "urllib", "http", "ftplib", "smtplib", "requests"}
        for module in (card_module, export_module):
            tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
            dotted: set[str] = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    dotted |= {alias.name for alias in node.names}
                elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                    dotted.add(node.module)
            roots = {name.split(".")[0] for name in dotted}
            assert not roots & forbidden_roots, f"{module.__name__} 引入出网件：{roots & forbidden_roots}"
            assert not any(
                name == "st_agent.l0.net" or name.startswith("st_agent.l0.net.")
                for name in dotted
            ), f"{module.__name__} 引入出网网关"

    def test_card_is_plain_text(self, exporter, custom_skill):
        container = exporter.export_skill(custom_skill.skill_id, author=AUTHOR)
        card = exporter.card_for(container)
        assert isinstance(card.to_text(), str)
        assert card.download_hint == ShareCard.for_container(container).download_hint


# ───────────────────────── GWT-5：字节与落盘 ─────────────────────────

class TestGwt5BytesAndSave:
    def test_save_writes_the_same_bytes(self, exporter, custom_skill, tmp_path: Path):
        container = exporter.export_skill(custom_skill.skill_id, author=AUTHOR)
        target = container.save_to(tmp_path / f"demo{container.extension}")
        assert target.read_bytes() == container.to_bytes()
        assert ShareContainer.from_bytes(target.read_bytes()).payload == custom_skill

    def test_save_refuses_to_create_directories(self, exporter, custom_skill, tmp_path: Path):
        """导出位置由调用方负责——父目录不存在即显式报错，不替用户在别处造目录。"""
        container = exporter.export_skill(custom_skill.skill_id, author=AUTHOR)
        with pytest.raises(ShareExportError, match="目标目录不存在"):
            container.save_to(tmp_path / "not-there" / f"demo{container.extension}")

    def test_exports_touch_no_store_partition(
        self, exporter, store, custom_skill, custom_flow, custom_lens, seeded_graph,
        tmp_path: Path,
    ):
        before = {p: sorted(store.list_files(p)) for p in PARTITION_NAMES}
        for container in (
            exporter.export_skill(custom_skill.skill_id, author=AUTHOR),
            exporter.export_flow(custom_flow.flow_id, author=AUTHOR),
            exporter.export_lens(custom_lens.lens_id, author=AUTHOR),
            exporter.export_memory(author=AUTHOR, confirmed_by="user"),
        ):
            container.save_to(tmp_path / f"x{container.extension}")
        assert {p: sorted(store.list_files(p)) for p in PARTITION_NAMES} == before


# ───────────────────────── 取材面缺失 ─────────────────────────

class TestMissingSourceIsExplicit:
    def test_no_sources_at_all(self):
        exporter = ShareExporter(now=lambda: NOW)
        with pytest.raises(ShareExportError, match="未注入 SkillRegistry"):
            exporter.export_skill("sk_demo_v1.0", author=AUTHOR)
        with pytest.raises(ShareExportError, match="未注入 MemoryShare"):
            exporter.export_memory(author=AUTHOR, confirmed_by="user")

    def test_partial_assembly_only_disables_that_kind(self, skills, custom_skill):
        exporter = ShareExporter(skills=skills, now=lambda: NOW)
        assert exporter.export_skill(custom_skill.skill_id, author=AUTHOR).share_type == "skill"
        with pytest.raises(ShareExportError, match="未注入 WorkflowStore"):
            exporter.export_flow("wf_demo_v1.0", author=AUTHOR)
