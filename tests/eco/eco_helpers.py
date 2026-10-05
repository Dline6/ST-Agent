"""ECO 层测试夹具件（四支用例共用；同 ``tests/l2/memory_helpers.py`` 的范式）。

时间锚点固定（``NOW``），故容器创建时间、卡片文件名与校验和全部可复算。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from st_agent.contracts.capability_types import Provenance, SkillDescriptor
from st_agent.contracts.registry_types import SemVer
from st_agent.eco import ShareContainer
from st_agent.l1.workflow.models import WorkflowDAG, WorkflowNode
from st_agent.l2.memory import FragmentPayload, checked_node, new_node_id
from st_agent.l4.lens import JudgingCriteria, Lens

CST = timezone(timedelta(hours=8))
NOW = datetime(2026, 10, 5, 18, 0, tzinfo=CST)
PASS = "eco-rig-passphrase"
STOCK_ID = "sh.600000"

SHARER = "alice"
AUTHOR = "tester"
DEMO_SKILL_ID = "sk_demo_import_v1.0"
DEMO_SKILL_BASE = "sk_demo_import"
ABSENT_SKILL_ID = "sk_absent_skill_v1.0"
EXISTING_SKILL_ID = "sk_st_list_sync_v1.0"
"""官方 Pack 里必然存在的 skill_id（依赖解析的「本地已有」一侧）。"""
DEMO_FLOW_ID = "wf_demo_import_v1.0"
DEMO_LENS_ID = "lens_0123456789abcdef0123"



def memory_node(*, privacy: str = "public", **over: Any):
    """构造一个合法记忆节点（缺省 thesis 类、``public`` 分级）。

    ``privacy`` 是 `.stmem` 强制三步的判据面：只有 ``public`` 能离开本机
    （[09 §2](../../docs/技术架构-v2/09-生态与分享.md)）。
    """
    fields: dict[str, Any] = {
        "type": "thesis",
        "memory_node_id": new_node_id(),
        "confidence": 0.8,
        "source": "user_stated",
        "privacy_level": privacy,
        "created_at": NOW,
        "updated_at": NOW,
        "subject": STOCK_ID,
        "subject_kind": "stock",
        "view": "看好反转",
        "stated_at": NOW,
    }
    fields.update(over)
    return checked_node(**fields)


# ───────────────────────── 导入侧：四类容器的构造 ─────────────────────────

def skill_descriptor(
    *,
    skill_id: str = DEMO_SKILL_ID,
    name: str = "导入演示",
    description: str = "导入校验流水线演示用的自建 Skill",
    permissions: tuple[str, ...] = (),
    dependencies: tuple[str, ...] = (),
    **over: Any,
) -> SkillDescriptor:
    """构造一个待导入的自建 Skill 描述体（`source` 由导入侧改写为 `imported`）。"""
    fields: dict[str, Any] = {
        "skill_id": skill_id,
        "name": name,
        "description": description,
        "input_schema": {"type": "object", "properties": {}},
        "output_schema": {"type": "object", "properties": {}},
        "parameters": (),
        "dependencies": tuple(dependencies),
        "source": "user-built",
        "provenance": Provenance(),
        "permissions": tuple(permissions),
        "offline_level": "full",
        "version_policy": "follow-latest",
    }
    fields.update(over)
    return SkillDescriptor(**fields)


def skill_container(
    *,
    descriptor: SkillDescriptor | None = None,
    sharer: str | None = SHARER,
    author: str = AUTHOR,
    dependencies: tuple[str, ...] | None = None,
    created_at: datetime = NOW,
) -> ShareContainer:
    """待导入的 `.stskill` 容器（依赖声明缺省取自描述体，与导出侧口径同）。"""
    payload = descriptor if descriptor is not None else skill_descriptor()
    declared = tuple(payload.dependencies) if dependencies is None else dependencies
    return ShareContainer.pack(
        payload, author=author, sharer=sharer, dependencies=declared, created_at=created_at
    )


def flow_container(
    *,
    flow_id: str = DEMO_FLOW_ID,
    skill_ids: tuple[str, ...] = (EXISTING_SKILL_ID,),
    sharer: str | None = SHARER,
    author: str = AUTHOR,
    created_at: datetime = NOW,
) -> ShareContainer:
    """待导入的 `.stflow` 容器（依赖声明＝节点引用的 skill_id 去重升序）。"""
    dag = WorkflowDAG(
        flow_id=flow_id,
        name="演示工作流",
        description="导入校验流水线演示用的工作流",
        version=SemVer.parse(flow_id.rsplit("_v", 1)[1]),
        nodes=tuple(
            WorkflowNode(node_id=f"n{i}", skill_id=sid, params={})
            for i, sid in enumerate(skill_ids)
        ),
    )
    return ShareContainer.pack(
        dag, author=author, sharer=sharer,
        dependencies=tuple(sorted(set(skill_ids))), created_at=created_at,
    )


def lens_container(
    *,
    lens_id: str = DEMO_LENS_ID,
    skill_bundle: tuple[str, ...] = (EXISTING_SKILL_ID,),
    kind: str = "custom",
    sharer: str | None = SHARER,
    author: str = AUTHOR,
    created_at: datetime = NOW,
) -> ShareContainer:
    """待导入的 `.stlens` 容器（依赖声明＝`skill_bundle`）。"""
    lens = Lens(
        lens_id=lens_id,
        name="演示视角",
        description="导入校验流水线演示用的自定义视角",
        skill_bundle=tuple(skill_bundle),
        judging_criteria=JudgingCriteria(natural="按数据充分度给出中性评判"),
        kind=kind,  # type: ignore[arg-type]
        enabled=True,
    )
    return ShareContainer.pack(
        lens, author=author, sharer=sharer,
        dependencies=tuple(skill_bundle), created_at=created_at,
    )


def mem_container(
    *,
    nodes: tuple[Any, ...] | None = None,
    sharer: str | None = SHARER,
    author: str = AUTHOR,
    created_at: datetime = NOW,
) -> ShareContainer:
    """待导入的 `.stmem` 容器（缺省一个 `public` 节点——只有它能离开本机，09 §2）。"""
    payload = FragmentPayload(nodes=tuple(nodes) if nodes is not None else (memory_node(),))
    return ShareContainer.pack(
        payload, author=author, sharer=sharer, dependencies=(), created_at=created_at
    )

