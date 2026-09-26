"""``flow_id`` 拼接规则（T-L1-003.1；03 §3.1 + 01 §1）。

形态：``wf_<注册名>_v<主>.<次>``，与 ``skill_id``（``skills/ids.py``）同构。

**与 skill_id 的差别**：``skill_id`` 在 §1 登记的是占位类型（``SkillId`` 的
``^sk_[0-9a-f]{20}$``，与真实的 ``sk_<注册名>_v<主>.<次>`` 对不上）；``flow_id``
登记时按 [D-018] 定案**直接对齐真实格式**——契约层 ``FLOW_ID_PATTERN`` 即唯一
口径，本模块不另造副本，故两份永不漂移。
"""

from __future__ import annotations

from pydantic import ValidationError

from st_agent.contracts.identifiers import FLOW_ID_PATTERN, FlowId
from st_agent.contracts.registry_types import SemVer
from st_agent.l1.workflow.errors import WorkflowValidationError

__all__ = [
    "FLOW_ID_PATTERN",
    "base_of",
    "check_flow_id",
    "flow_id_for",
    "parse_flow_id",
]

_PREFIX = "wf"


def check_flow_id(value: str) -> str:
    """校验 flow_id 形态（走契约层 ``FlowId``——01 §1 登记的唯一口径）。"""
    try:
        FlowId.of(value)
    except (ValidationError, TypeError) as exc:
        raise WorkflowValidationError(
            f"非法 flow_id {value!r}（须为 wf_<注册名>_v<主>.<次>，如 "
            "wf_daily_brief_v1.0）"
        ) from exc
    return value


def parse_flow_id(flow_id: str) -> tuple[str, SemVer]:
    """拆 ``flow_id`` → ``(base, SemVer)``（形态非法即拒）。"""
    check_flow_id(flow_id)
    m = FLOW_ID_PATTERN.match(flow_id)
    assert m is not None
    return m.group(1), SemVer(major=int(m.group(2)), minor=int(m.group(3)))


def base_of(flow_id: str) -> str:
    """取 flow_id 的 base（版本号剥离；激活指针与版本列表按 base 归集）。"""
    base, _ = parse_flow_id(flow_id)
    return base


def flow_id_for(base: str, version: SemVer | str) -> str:
    """由 base + 版本拼出 flow_id（base 须为 ``wf_`` 前缀形态）。"""
    if isinstance(version, str):
        version = SemVer.parse(version)
    if not isinstance(base, str) or not base.startswith(f"{_PREFIX}_"):
        raise WorkflowValidationError(f"非法工作流 base {base!r}（须为 wf_ 前缀）")
    return check_flow_id(f"{base}_v{version.major}.{version.minor}")
