"""dev 面的示例内容：六态信封 + UI 描述。

用途只有一个：在浏览器里把**六个态各自的渲染**与**组件面的三条路径**（正常 /
未实现类型降级 / 中性校验阻断）逐条走通——M1 骨架叶不接真实数据（任务 `T-UI-001.2`
假设 `A5`、`T-UI-001.3` 同口径）。信封一律用 [01 §5] 的**合法工厂**构造、描述一律经
[01 §12] 的 `UiDescription` 构造，故它们同时也是契约形状的活样本；本模块不进发布构建。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from st_agent.contracts.result_envelope import EvidenceRef, ResultEnvelope
from st_agent.contracts.ui_description import UiDescription, new_description_id

__all__ = ["DESCRIPTION_KINDS", "SAMPLE_STATUSES", "sample_description", "sample_envelope"]

_NOW = datetime(2026, 9, 29, 12, 0, tzinfo=timezone(timedelta(hours=8)))

SAMPLE_STATUSES: tuple[str, ...] = (
    "ok",
    "empty",
    "unavailable",
    "dependency_failed",
    "failed",
    "validation_failed",
)
"""六个状态（与 [01 §5] 的枚举同序）。"""


def _samples() -> dict[str, ResultEnvelope]:
    evidence = (EvidenceRef(kind="dataset_snapshot_id", ref="ds_sample0000000000000"),)
    return {
        "ok": ResultEnvelope.ok(
            {"summary": "示例载荷（骨架叶不接真实数据）"}, as_of=_NOW, evidence_refs=evidence
        ),
        "empty": ResultEnvelope.empty(
            "今日无满足条件的记录", as_of=_NOW, evidence_refs=evidence
        ),
        "unavailable": ResultEnvelope.unavailable(
            "数据源暂不可用", last_updated_at=_NOW - timedelta(hours=6), as_of=_NOW
        ),
        "dependency_failed": ResultEnvelope.dependency_failed(
            "上游依赖失败", log_ref="trace/sample-dependency"
        ),
        "failed": ResultEnvelope.failed("执行失败", log_ref="trace/sample-failure"),
        "validation_failed": ResultEnvelope.validation_failed("参数校验失败：取值超出声明范围"),
    }


def sample_envelope(status: str) -> ResultEnvelope | None:
    """按状态名取示例信封；未知状态名返回 ``None``（调用方回 400 / 404）。"""
    return _samples().get(status)


# ── UI 描述样本（01 §12） ─────────────────────────────────────────────────────

DESCRIPTION_KINDS: tuple[str, ...] = (
    "report_card",
    "table",
    "reserved",
    "generated-violation",
    "data-violation",
)
"""组件面的五条走查路径（前两条正常渲染、第三条降级、后两条验证中性分栏）。"""


def _descriptions() -> dict[str, UiDescription]:
    return {
        "report_card": UiDescription(
            description_id=new_description_id(),
            component_type="report_card",
            title="示例报告卡",
            slots={
                "sections": [
                    {"title": "概要", "lines": ["价格走势平稳", "成交温和"]},
                    {"title": "待观察", "lines": ["披露窗口临近"]},
                ]
            },
            text_kinds={"sections": "generated"},
            as_of=_NOW,
        ),
        "table": UiDescription(
            description_id=new_description_id(),
            component_type="table",
            title="示例表格",
            slots={
                "columns": [{"key": "code", "label": "代码"}, {"key": "note", "label": "备注"}],
                "rows": [
                    {"code": "sh.600000", "note": "示例数据一"},
                    {"code": "sz.000001", "note": "示例数据二"},
                ],
            },
            # 列头是**生成文案**（过 §6）；行内容是**数据展示**（原样呈现，[D-053]）
            text_kinds={"columns": "generated", "rows": "data"},
            as_of=_NOW,
        ),
        # 已登记、本期未实现 → 渲染面**显式降级**（01 §12），不猜测
        "reserved": UiDescription(
            description_id=new_description_id(),
            component_type="heatmap",
            title="示例热力图（本期未实现）",
            slots={"cells": [{"code": "sh.600000", "value": 0.12}]},
            text_kinds={"cells": "data"},
            as_of=_NOW,
        ),
        # 生成文案命中第一人称 → **阻断渲染**（回 validation_failed）
        "generated-violation": UiDescription(
            description_id=new_description_id(),
            component_type="report_card",
            title="我认为这只标的值得关注",
            slots={"sections": [{"title": "结论", "lines": ["建议继续观察"]}]},
            text_kinds={"sections": "generated"},
        ),
        # 同样的措辞出现在 data 槽（用户原话）→ **放行**（不整串复检，D-053）
        "data-violation": UiDescription(
            description_id=new_description_id(),
            component_type="report_card",
            title="对话历史回显",
            slots={"sections": [{"title": "原话", "lines": ["我认为这只标的值得关注"]}]},
            text_kinds={"sections": "data"},
        ),
    }


def sample_description(kind: str) -> UiDescription | None:
    """按走查路径名取示例描述；未知名称返回 ``None``。"""
    return _descriptions().get(kind)
