"""T-ECO-002.1 测试：导入校验流水线（[09 §3](../../docs/技术架构-v2/09-生态与分享.md)）。

GWT 对照（任务文件 6 条）：
- GWT-1 `inspect` 给出「这个 Skill 想做什么」（权限 + 依赖 + 来源追溯）且**未安装**
- GWT-2 缺失依赖逐条点名 + 给获取途径，**不自动安装**依赖
- GWT-3 权限**全部批准**才装（fail-closed）；装上的 `source=imported` + `provenance`
- GWT-4 损坏 / 非本族 / 异主版本 / 校验和不符 → 明确报错且**不安装**
- GWT-5 `.stmem` 经 L2 `FragmentImporter` 入库（幂等），来源取自容器 `manifest`
- GWT-6 四类各自的安装面保留身份（`flow_id` / `lens_id` / `memory_node_id` / `skill_id`）
"""

from __future__ import annotations

import json

import pytest

from eco_helpers import (
    ABSENT_SKILL_ID,
    AUTHOR,
    DEMO_FLOW_ID,
    DEMO_LENS_ID,
    DEMO_SKILL_BASE,
    DEMO_SKILL_ID,
    EXISTING_SKILL_ID,
    SHARER,
    flow_container,
    lens_container,
    mem_container,
    memory_node,
    skill_container,
    skill_descriptor,
)
from st_agent.eco import (
    ShareContainer,
    ShareFormatError,
    ShareImportError,
    ShareImporter,
    ShareVersionError,
)
from st_agent.eco.checksum import checksum_basis, sha256_hex
from st_agent.l1.skills.errors import SkillNotFoundError

PERMISSION = "net_access:<*.example.com>"


def _skill_blob(*, permissions=(PERMISSION,), dependencies=(), sharer=SHARER):
    return skill_container(
        descriptor=skill_descriptor(permissions=permissions, dependencies=dependencies),
        sharer=sharer,
    ).to_bytes()


class TestGwt1Inspect:
    """GWT-1：审核面把「想做什么」三段给全，且什么都没装。"""

    def test_inspect_reports_permissions_dependencies_and_provenance(self, importer, skills):
        plan = importer.inspect(_skill_blob(dependencies=(ABSENT_SKILL_ID,)))

        assert plan.kind == "skill"
        assert plan.permissions == (PERMISSION,)
        assert any("待批准" in line for line in plan.permission_display)

        (gap,) = plan.missing
        assert gap.skill_id == ABSENT_SKILL_ID

        assert plan.provenance.sharer == SHARER
        assert plan.provenance.imported_at is not None
        assert plan.provenance.checksum == plan.container.checksum()

        lines = plan.what_it_wants()
        assert any("权限申请" in line for line in lines)
        assert any("缺失依赖" in line for line in lines)
        assert any("来源追溯" in line for line in lines)

        with pytest.raises(SkillNotFoundError):
            skills.get(DEMO_SKILL_ID)          # 审核阶段**未安装**

    def test_inspect_declares_pending_without_approving(self, importer, permissions):
        """第 3 段只**登记**声明（初态 pending）——不预设已批准（01 §10）。"""
        importer.inspect(_skill_blob())
        assert permissions.pending_permissions(DEMO_SKILL_BASE) == (PERMISSION,)
        assert permissions.approved_permissions(DEMO_SKILL_BASE) == ()


class TestGwt2Dependencies:
    """GWT-2：依赖解析只报告、不代装。"""

    def test_missing_dependency_gives_how_to_get_and_is_not_installed(self, importer, skills):
        (gap,) = importer.inspect(_skill_blob(dependencies=(ABSENT_SKILL_ID,))).missing
        assert gap.how_to_get.strip()
        with pytest.raises(SkillNotFoundError):
            skills.get(ABSENT_SKILL_ID)        # 缺失依赖**未**被自动安装

    def test_present_dependency_is_not_reported(self, importer):
        plan = importer.inspect(_skill_blob(dependencies=(EXISTING_SKILL_ID,)))
        assert plan.missing == ()

    def test_missing_dependency_face_is_explicit(self, store):
        blind = ShareImporter(store=store)
        with pytest.raises(ShareImportError, match="未注入 SkillRegistry"):
            blind.inspect(_skill_blob(dependencies=(EXISTING_SKILL_ID,)))


class TestGwt3PermissionGate:
    """GWT-3：权限门与安装落点。"""

    def test_install_requires_all_permissions_approved(self, importer):
        plan = importer.inspect(_skill_blob())
        with pytest.raises(ShareImportError, match="未全部批准"):
            importer.install(plan, confirmed_by="user")

    def test_install_after_approval_registers_imported_skill(self, importer, skills, permissions):
        plan = importer.inspect(_skill_blob())
        permissions.approve(DEMO_SKILL_BASE, PERMISSION)

        outcome = importer.install(plan, confirmed_by="user")

        assert outcome.installed_id == DEMO_SKILL_ID      # 保留分享方标识
        descriptor = skills.get(DEMO_SKILL_ID)
        assert descriptor.source == "imported"
        assert descriptor.provenance.sharer == SHARER
        assert descriptor.provenance.checksum == plan.provenance.checksum
        assert descriptor.provenance.imported_at

    def test_rejected_permission_blocks_install(self, importer, permissions):
        plan = importer.inspect(_skill_blob())
        permissions.reject(DEMO_SKILL_BASE, PERMISSION)
        with pytest.raises(ShareImportError, match="未全部批准"):
            importer.install(plan, confirmed_by="user")

    def test_install_rejects_non_user_confirmation(self, importer):
        plan = importer.inspect(_skill_blob(permissions=()))
        with pytest.raises(ShareImportError, match="confirmed_by"):
            importer.install(plan, confirmed_by="agent")

    def test_skill_without_declaration_installs_after_confirmation(self, importer, skills):
        plan = importer.inspect(_skill_blob(permissions=()))
        importer.install(plan, confirmed_by="user")
        assert skills.get(DEMO_SKILL_ID).permissions == ()

    def test_approval_survives_a_new_version_of_the_same_base(self, importer, permissions):
        """A3：批准按 `base` 归集——同 base 的次版本导入不必重批（01 §10 随能力走）。"""
        first = importer.inspect(_skill_blob())
        permissions.approve(DEMO_SKILL_BASE, PERMISSION)
        importer.install(first, confirmed_by="user")

        next_version = importer.inspect(
            skill_container(
                descriptor=skill_descriptor(
                    skill_id="sk_demo_import_v1.1", permissions=(PERMISSION,)
                )
            ).to_bytes()
        )
        assert importer.install(next_version, confirmed_by="user").installed_id == (
            "sk_demo_import_v1.1"
        )

    def test_existing_skill_is_not_silently_overwritten(self, importer, permissions):
        first = importer.inspect(_skill_blob())
        permissions.approve(DEMO_SKILL_BASE, PERMISSION)
        importer.install(first, confirmed_by="user")
        with pytest.raises(ShareImportError, match="失败"):
            importer.install(importer.inspect(_skill_blob()), confirmed_by="user")


class TestGwt4Damaged:
    """GWT-4：坏文件与「这次导入不成立」两类失败分工明确。"""

    @pytest.mark.parametrize(
        "blob",
        [b"not json at all", b'{"format_id": "st-agent-backup", "entries": []}'],
        ids=["非 JSON", "非本族（备份归档形状）"],
    )
    def test_broken_or_foreign_file_reports_and_does_not_install(self, importer, skills, blob):
        with pytest.raises(ShareFormatError):
            importer.inspect(blob)
        with pytest.raises(SkillNotFoundError):
            skills.get(DEMO_SKILL_ID)

    def test_tampered_payload_fails_checksum(self, importer):
        blob = _skill_blob()
        tampered = blob.replace("导入演示".encode("utf-8"), "导入演示改".encode("utf-8"))
        assert tampered != blob
        with pytest.raises(ShareFormatError, match="校验和"):
            importer.inspect(tampered)

    def test_incompatible_major_version_is_refused(self, importer):
        document = skill_container().to_document()
        document["header"]["version"] = "2.0"
        document["manifest"]["checksum"] = sha256_hex(
            checksum_basis(document["header"], document["payload"], document["manifest"])
        )
        blob = json.dumps(document, ensure_ascii=False).encode("utf-8")
        with pytest.raises(ShareVersionError):
            importer.inspect(blob)


class TestGwt5Memory:
    """GWT-5：`.stmem` 走 L2 既有收件侧。"""

    def test_mem_container_installs_through_l2_importer(self, importer, graph):
        node = memory_node()
        outcome = importer.install(
            importer.inspect(mem_container(nodes=(node,)).to_bytes()), confirmed_by="user"
        )
        assert outcome.kind == "mem"
        assert graph.has_node(node.memory_node_id)
        imported = graph.get_node(node.memory_node_id)
        assert imported.source == "inferred"                    # 他人陈述（04 §8）
        assert imported.provenance.imported.sharer == SHARER    # 来源取自 manifest

    def test_reimport_is_idempotent(self, importer):
        blob = mem_container(nodes=(memory_node(),)).to_bytes()
        importer.install(importer.inspect(blob), confirmed_by="user")
        second = importer.install(importer.inspect(blob), confirmed_by="user")
        assert "跳过 1 条" in second.note

    def test_non_public_node_is_refused(self, importer):
        blob = mem_container(nodes=(memory_node(privacy="private"),)).to_bytes()
        with pytest.raises(ShareImportError, match="失败"):
            importer.install(importer.inspect(blob), confirmed_by="user")


class TestGwt6OtherKinds:
    """GWT-6：四类安装面各保身份；flow / lens 另落导入留痕。"""

    def test_flow_install_preserves_identity_and_writes_ledger(self, importer, workflows):
        plan = importer.inspect(flow_container().to_bytes())
        assert plan.kind == "flow"

        outcome = importer.install(plan, confirmed_by="user")

        assert outcome.installed_id == DEMO_FLOW_ID
        assert workflows.get(DEMO_FLOW_ID).flow_id == DEMO_FLOW_ID
        (record,) = importer.ledger_records()
        assert (record.kind, record.installed_id) == ("flow", DEMO_FLOW_ID)
        assert record.origin.sharer == SHARER

    def test_lens_install_preserves_identity_and_forces_custom(self, importer, lenses):
        plan = importer.inspect(lens_container(kind="builtin").to_bytes())
        outcome = importer.install(plan, confirmed_by="user")

        assert outcome.installed_id == DEMO_LENS_ID
        installed = lenses.get(DEMO_LENS_ID)
        assert installed.lens_id == DEMO_LENS_ID            # 保留分享方标识
        assert installed.kind == "custom"                   # 导入物可删（06 §1）
        assert importer.ledger_records()[0].kind == "lens"

    def test_flow_with_missing_dependency_is_refused_by_layer(self, importer):
        plan = importer.inspect(flow_container(skill_ids=(ABSENT_SKILL_ID,)).to_bytes())
        assert [g.skill_id for g in plan.missing] == [ABSENT_SKILL_ID]
        with pytest.raises(ShareImportError, match="缺失依赖"):
            importer.install(plan, confirmed_by="user")

    def test_missing_install_face_is_explicit(self, store):
        blind = ShareImporter(store=store)
        plan = blind.inspect(flow_container(skill_ids=()).to_bytes())
        with pytest.raises(ShareImportError, match="未注入 WorkflowStore"):
            blind.install(plan, confirmed_by="user")


class TestProvenance:
    """来源追溯的取材（[09 §5](../../docs/技术架构-v2/09-生态与分享.md)）。"""

    def test_missing_sharer_requires_received_from(self, importer):
        blob = _skill_blob(sharer=None)
        with pytest.raises(ShareImportError, match="received_from"):
            importer.inspect(blob)
        assert importer.inspect(blob, received_from="bob").provenance.sharer == "bob"

    def test_origin_chain_is_carried(self, importer):
        blob = ShareContainer.pack(
            skill_descriptor(), author=AUTHOR, sharer=SHARER, origin_chain=("carol",)
        ).to_bytes()
        assert importer.inspect(blob).provenance.origin_chain == ("carol",)

    def test_ledger_records_sorted_and_readable(self, importer):
        for flow_id in ("wf_demo_import_v1.0", "wf_other_import_v1.0"):
            importer.install(
                importer.inspect(flow_container(flow_id=flow_id).to_bytes()),
                confirmed_by="user",
            )
        records = importer.ledger_records()
        assert [r.kind for r in records] == ["flow", "flow"]
        assert list(records) == sorted(records, key=lambda r: r.import_id)
