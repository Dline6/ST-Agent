"""T-ECO-001.1 测试：四类分享物统一容器与格式（09 §1）。

GWT 对照（任务文件 5 条）：
- GWT-1 四类打包 → 三段齐备 + 种类↔扩展名 + 明文可检视
- GWT-2 读回本体逐字段一致（含 `.stmem` 的子类专属字段）
- GWT-3 格式版本：同主版本可读 / 主版本不同即拒
- GWT-4 校验和可复核 + 任一处改动即失败
- GWT-5 损坏 / 缺段 / 非本族（备份归档）→ 明确报错
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from eco_helpers import NOW, PASS, STOCK_ID, memory_node
from st_agent.contracts.capability_types import Provenance, SkillDescriptor
from st_agent.contracts.registry_types import SemVer
from st_agent.eco import (
    SHARE_EXTENSIONS,
    SHARE_FORMAT_ID,
    ShareContainer,
    ShareFormatError,
    ShareHeader,
    ShareManifest,
    ShareVersionError,
    container_checksum,
    extension_for,
    kind_of,
)
from st_agent.eco.checksum import CHECKSUM_PATTERN, checksum_basis, sha256_hex
from st_agent.l0.backup import BACKUP_FORMAT_TAG, create_backup
from st_agent.l1.workflow.models import (
    ParamBinding,
    WorkflowDAG,
    WorkflowEdge,
    WorkflowNode,
)
from st_agent.l2.memory.sharing import FragmentPayload
from st_agent.l4.lens import JudgingCriteria, Lens

AUTHOR = "alice"


# ───────────────────────── 四类本体 ─────────────────────────

def skill_descriptor(**over: Any) -> SkillDescriptor:
    fields: dict[str, Any] = {
        "skill_id": "sk_demo_skill_v1.0",
        "name": "演示技能",
        "description": "演示用的自建技能",
        "input_schema": {"type": "object", "properties": {}},
        "output_schema": {"type": "object", "properties": {}},
        "source": "user-built",
        "provenance": Provenance(),
        "offline_level": "full",
        "version_policy": "follow-latest",
    }
    fields.update(over)
    return SkillDescriptor(**fields)


def workflow_dag(**over: Any) -> WorkflowDAG:
    fields: dict[str, Any] = {
        "flow_id": "wf_demo_flow_v1.0",
        "name": "演示工作流",
        "description": "演示用的自建工作流",
        "version": SemVer(major=1, minor=0),
        "nodes": (
            WorkflowNode(
                node_id="n1",
                skill_id="sk_demo_skill_v1.0",
                params={"threshold": ParamBinding(kind="literal", value=0.5)},
            ),
            WorkflowNode(node_id="n2", skill_id="sk_other_skill_v1.0"),
        ),
        "edges": (WorkflowEdge(edge_id="e1", from_node="n1", to_node="n2"),),
    }
    fields.update(over)
    return WorkflowDAG(**fields)


def custom_lens(**over: Any) -> Lens:
    fields: dict[str, Any] = {
        "lens_id": "lens_0123456789abcdef0123",
        "name": "自定义估值视角",
        "description": "关注估值与安全边际",
        "judging_criteria": JudgingCriteria(natural="低估值优先"),
        "kind": "custom",
    }
    fields.update(over)
    return Lens(**fields)


def fragment(*nodes: Any) -> FragmentPayload:
    return FragmentPayload(nodes=tuple(nodes))


PAYLOADS = {
    "skill": skill_descriptor,
    "flow": workflow_dag,
    "lens": custom_lens,
    "mem": fragment,
}


def repack(container: ShareContainer, mutate) -> bytes:
    """改动容器文档后**重算校验和**再序列化（模拟「另一个版本 / 另一种内容的合法容器」）。

    直接改字节而不重算会被校验和拦住（那正是 GWT-4 要验的），故此件专用于
    需要「容器本身自洽」的场景（版本判据、载荷结构判据）。
    """
    doc = container.to_document()
    mutate(doc)
    manifest = {**doc["manifest"], "checksum": ""}
    doc["manifest"]["checksum"] = sha256_hex(
        checksum_basis(doc["header"], doc["payload"], manifest)
    )
    return json.dumps(doc, ensure_ascii=False, indent=2).encode("utf-8")


# ───────────────────────── GWT-1 ─────────────────────────

class TestGwt1PacksFourKinds:
    @pytest.mark.parametrize("kind", sorted(PAYLOADS))
    def test_three_segments_plaintext_and_extension(self, kind: str):
        payload = PAYLOADS[kind]()
        container = ShareContainer.pack(payload, author=AUTHOR, sharer="bob")

        assert container.share_type == kind
        assert container.extension == SHARE_EXTENSIONS[kind]
        assert extension_for(kind) == SHARE_EXTENSIONS[kind]

        blob = container.to_bytes()
        text = blob.decode("utf-8")            # 明文可检视（无加密层）
        doc = json.loads(text)
        assert set(doc) == {"header", "payload", "manifest"}
        assert doc["header"] == {
            "format_id": SHARE_FORMAT_ID, "share_type": kind, "version": "1.0",
        }
        assert doc["manifest"]["author"] == AUTHOR
        assert doc["manifest"]["checksum"] == container.checksum()
        assert CHECKSUM_PATTERN.match(container.checksum())

    def test_kind_is_inferred_from_payload(self):
        for kind, build in PAYLOADS.items():
            assert kind_of(build()) == kind

    def test_explicit_share_type_must_agree(self):
        with pytest.raises(ShareFormatError, match="不符"):
            ShareContainer.pack(skill_descriptor(), author=AUTHOR, share_type="flow")

    def test_unknown_payload_rejected(self):
        with pytest.raises(ShareFormatError, match="未知分享物本体"):
            ShareContainer.pack({"not": "a model"}, author=AUTHOR)

    def test_container_rejects_payload_of_other_kind(self):
        """构造期即钉住：`payload` 与 `header.share_type` 必须一致（防分头构造漂移）。"""
        header = ShareHeader(share_type="lens")
        manifest = ShareManifest(author=AUTHOR, created_at=NOW)
        with pytest.raises(ShareFormatError, match="payload 须为 Lens"):
            ShareContainer(header=header, payload=skill_descriptor(), manifest=manifest)


# ───────────────────────── GWT-2 ─────────────────────────

class TestGwt2RoundTrip:
    @pytest.mark.parametrize("kind", sorted(PAYLOADS))
    def test_payload_survives_round_trip(self, kind: str):
        payload = PAYLOADS[kind]()
        container = ShareContainer.pack(payload, author=AUTHOR)
        back = ShareContainer.from_bytes(container.to_bytes())
        assert back.payload == payload
        assert back.manifest == container.manifest
        assert back.header == container.header

    def test_memory_fragment_keeps_subclass_fields(self):
        """`.stmem` 的节点是判别联合——子类专属字段不得在容器往返里丢掉（叶 A1）。"""
        node = memory_node(type="attention", holdings=(STOCK_ID,),
                           sector_preferences=("银行",))
        back = ShareContainer.from_bytes(
            ShareContainer.pack(fragment(node), author=AUTHOR).to_bytes()
        )
        restored = back.payload.nodes[0]
        assert restored.type == "attention"
        assert restored.holdings == (STOCK_ID,)
        assert restored.sector_preferences == ("银行",)

    def test_workflow_bindings_survive_round_trip(self):
        """`kind=literal` 的绑定以「value 是否显式给出」判定——往返不得把它降级成 ref。"""
        back = ShareContainer.from_bytes(
            ShareContainer.pack(workflow_dag(), author=AUTHOR).to_bytes()
        )
        binding = back.payload.nodes[0].params["threshold"]
        assert binding.kind == "literal" and binding.value == 0.5


# ───────────────────────── GWT-3 ─────────────────────────

class TestGwt3FormatVersion:
    def test_same_major_readable(self):
        container = ShareContainer.pack(skill_descriptor(), author=AUTHOR)
        blob = repack(container, lambda d: d["header"].update(version="1.7"))
        assert ShareContainer.from_bytes(blob).header.version == "1.7"

    @pytest.mark.parametrize("version", ["0.9", "2.0", "3.1"])
    def test_other_major_rejected(self, version: str):
        container = ShareContainer.pack(skill_descriptor(), author=AUTHOR)
        blob = repack(container, lambda d: d["header"].update(version=version))
        with pytest.raises(ShareVersionError, match="主版本"):
            ShareContainer.from_bytes(blob)

    def test_bad_version_shape_rejected(self):
        container = ShareContainer.pack(skill_descriptor(), author=AUTHOR)
        blob = repack(container, lambda d: d["header"].update(version="v1"))
        with pytest.raises(ShareFormatError, match="格式版本"):
            ShareContainer.from_bytes(blob)

    def test_unknown_extra_keys_are_tolerated(self):
        """次版本可加字段 ⇒ 未知键须照读（不因 `extra` 而拒），01 §9 的前向兼容。"""
        container = ShareContainer.pack(skill_descriptor(), author=AUTHOR)
        blob = repack(container, lambda d: d["manifest"].update(signed_by="community"))
        back = ShareContainer.from_bytes(blob)
        assert back.payload == container.payload
        assert container_checksum(blob) == back.manifest.checksum


# ───────────────────────── GWT-4 ─────────────────────────

class TestGwt4Checksum:
    def test_recomputed_matches_manifest(self):
        container = ShareContainer.pack(skill_descriptor(), author=AUTHOR)
        assert container_checksum(container.to_bytes()) == container.checksum()

    def test_payload_change_detected(self):
        container = ShareContainer.pack(skill_descriptor(), author=AUTHOR)
        doc = container.to_document()
        doc["payload"]["description"] = "被改过的描述"
        with pytest.raises(ShareFormatError, match="校验和不符"):
            ShareContainer.from_bytes(json.dumps(doc, ensure_ascii=False).encode("utf-8"))

    def test_manifest_change_detected(self):
        container = ShareContainer.pack(skill_descriptor(), author=AUTHOR)
        doc = container.to_document()
        doc["manifest"]["author"] = "mallory"
        with pytest.raises(ShareFormatError, match="校验和不符"):
            ShareContainer.from_bytes(json.dumps(doc, ensure_ascii=False).encode("utf-8"))

    def test_checksum_covers_canonical_content_not_formatting(self):
        """基是规范化 JSON ⇒ 缩进 / 键序之差不算改动（内容变才变）。"""
        container = ShareContainer.pack(skill_descriptor(), author=AUTHOR)
        doc = container.to_document()
        compact = json.dumps(doc, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        assert container_checksum(compact) == container.checksum()

    def test_missing_checksum_rejected(self):
        """校验和缺席（未打包 / 被抹掉）不得当成「免检」——空串与实算不符即拒。"""
        container = ShareContainer.pack(skill_descriptor(), author=AUTHOR)
        doc = container.to_document()
        doc["manifest"]["checksum"] = ""
        blob = json.dumps(doc, ensure_ascii=False).encode("utf-8")
        with pytest.raises(ShareFormatError, match="校验和不符"):
            ShareContainer.from_bytes(blob)


# ───────────────────────── GWT-5 ─────────────────────────

class TestGwt5Corruption:
    def test_not_json(self):
        with pytest.raises(ShareFormatError, match="合法 JSON"):
            ShareContainer.from_bytes(b"\x00\x01\x02 not json")

    def test_truncated(self):
        container = ShareContainer.pack(workflow_dag(), author=AUTHOR)
        with pytest.raises(ShareFormatError):
            ShareContainer.from_bytes(container.to_bytes()[: len(container.to_bytes()) // 2])

    def test_missing_segment(self):
        doc = ShareContainer.pack(skill_descriptor(), author=AUTHOR).to_document()
        del doc["manifest"]
        with pytest.raises(ShareFormatError, match="缺段"):
            ShareContainer.from_bytes(json.dumps(doc).encode("utf-8"))

    def test_backup_archive_is_rejected(self, store, tmp_path: Path):
        """同族但互不通用：备份归档也是 JSON，故只能靠格式标识拦（叶 A5）。"""
        archive = create_backup(store, PASS, tmp_path / "backup" / "b.bak")
        raw = archive.read_bytes()
        assert BACKUP_FORMAT_TAG.encode("utf-8") in raw       # 确实是一份真归档
        with pytest.raises(ShareFormatError, match="互不通用"):
            ShareContainer.from_bytes(raw)

    def test_foreign_format_id_rejected(self):
        container = ShareContainer.pack(skill_descriptor(), author=AUTHOR)
        blob = repack(container, lambda d: d["header"].update(format_id="st-agent-backup/v1"))
        with pytest.raises(ShareFormatError, match="不是分享物容器"):
            ShareContainer.from_bytes(blob)

    def test_unknown_share_type_rejected(self):
        container = ShareContainer.pack(skill_descriptor(), author=AUTHOR)
        blob = repack(container, lambda d: d["header"].update(share_type="video"))
        with pytest.raises(ShareFormatError, match="share_type"):
            ShareContainer.from_bytes(blob)

    def test_payload_of_wrong_shape_rejected(self):
        container = ShareContainer.pack(skill_descriptor(), author=AUTHOR)
        blob = repack(container, lambda d: d["header"].update(share_type="lens"))
        with pytest.raises(ShareFormatError, match="payload 段结构非法"):
            ShareContainer.from_bytes(blob)

    def test_payload_not_an_object_rejected(self):
        container = ShareContainer.pack(skill_descriptor(), author=AUTHOR)
        blob = repack(container, lambda d: d.update(payload=["nope"]))
        with pytest.raises(ShareFormatError, match="payload 段须为 JSON 对象"):
            ShareContainer.from_bytes(blob)


# ───────────────────────── 契约面细节 ─────────────────────────

class TestManifestContract:
    def test_dependencies_must_be_skill_ids(self):
        with pytest.raises(ShareFormatError, match="非 skill_id"):
            ShareContainer.pack(
                skill_descriptor(), author=AUTHOR, dependencies=("wf_demo_v1.0",)
            )

    def test_dependencies_are_declared(self):
        container = ShareContainer.pack(
            skill_descriptor(), author=AUTHOR, dependencies=("sk_demo_skill_v1.0",)
        )
        assert container.manifest.dependencies == ("sk_demo_skill_v1.0",)
        assert ShareContainer.from_bytes(container.to_bytes()).manifest.dependencies == (
            "sk_demo_skill_v1.0",
        )

    def test_created_at_requires_timezone(self):
        with pytest.raises(ShareFormatError, match="时区"):
            ShareManifest(author=AUTHOR, created_at=NOW.replace(tzinfo=None))

    def test_provenance_carries_sharer_and_chain(self):
        container = ShareContainer.pack(
            skill_descriptor(), author=AUTHOR, sharer="bob", origin_chain=("carol",)
        )
        assert container.manifest.provenance.sharer == "bob"
        assert container.manifest.provenance.origin_chain == ("carol",)
        # 校验和只此一处（导入侧的 `imported_at` / `checksum` 由导入方记账）
        assert container.manifest.provenance.checksum is None
        assert container.manifest.provenance.imported_at is None
