"""多视角推理门面（L4 编排结果 → 描述载荷）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from st_agent.l2.memory import (
    ONBOARDING_KIND,
    SHARE_CONFIRMATION,
    ConfidenceModel,
    ConflictQueue,
    FragmentImporter,
    MemoryDeleter,
    MemoryGraph,
    MemoryReader,
    MemoryShare,
    MemoryWriter,
    OnboardingProtocol,
    SliceQuery,
    WritePolicy,
    checked_node,
    memory_policy_family,
    new_node_id,
    official_onboarding_entry,
)


@dataclass(frozen=True)
class DeliberationFacade:
    """L4 多视角推理面：持阵容 / 编排 / 分歧视图 / `analyze` 去向的装配件。

    [`T-UI-016.1`] 起增 `analyze`（[`AnalyzeService`]）与 :meth:`run_analysis`——表现层要
    在 `/deliberation` 页上**自带触发**一次编排（[D-112] ②：无会话态、无落盘），故需要一个
    「跑一次全链、把三段产物并列交出」的取数口；把 `AnalyzeService` 的入参形状收在本方法内，
    表现层不必知道它收的是 `confirmation` 鸭子面。
    """

    roster: Any
    deliberation: Any = None
    viewer: Any = None
    analyze: Any = None
    """`analyze` 去向的装配件（鸭子面 `analyze(confirmation, *, values=…)`）——[`T-UI-016.1`]。"""
    reader: Any = None
    """记忆读面（鸭子面 `query(SliceQuery)`，即 L2 `MemoryReader`）——决策留痕读面用（[`T-UI-016.3`]）。"""

    def run_analysis(self, *, topic: str, mode: str) -> Any:
        """跑一次编排全链（编排 → 对照 → 视图投影），返回 `AnalyzeOutcome` 形状的鸭子面。

        `topic` / `mode` 经 `values` 传入（`AnalyzeService` 由 [05 §3.2] 的澄清协议收敛二者，
        表现层的页内触发是同一口径的另一入口，[D-112] ②）。**不臆造缺省**：面缺席即由调用方
        回 `unavailable` + 点名（见 [`UiApp.api_deliberation_analyze`]）。
        """
        return self.analyze.analyze(
            _AnalyzeValues(values={}), values={"topic": topic, "mode": mode}
        )

    def decisions(self) -> list[dict[str, Any]]:
        """决策留痕（[06 §2.4](../../docs/技术架构-v2/06-L4-多视角推理.md) 写下的 L2 `history` 节点）。

        经注入的记忆读面按 `task_type="decision"` 取（[`TASK_TYPE_AFFINITY`] 该档含 `history`），
        再**收窄到 `type == "history"`** ——本面要的是**决策记录**，不是该档带回的
        `thesis` / `attention` / `identity`（那三类的读面归记忆区）。按 `updated_at` 降序。
        读面缺席 ⇒ 空列表（调用方回 `unavailable` + 点名，不臆造留痕）。
        """
        if self.reader is None:
            return []
        from st_agent.l2.memory import SliceQuery  # 局部导入：仅决策读面需要
        result = self.reader.query(
            SliceQuery(task_type="decision", view="timeline", token_budget=None)
        )
        rows = [
            {
                "node_id": str(s.node.memory_node_id),
                "event": str(getattr(s.node, "event", "") or ""),
                "updated_at": s.node.updated_at,
                "source": str(getattr(s.node, "source", "") or ""),
                "confidence": float(s.confidence),
            }
            for s in result.slices
            if str(getattr(s.node, "type", "")) == "history"
        ]
        rows.sort(key=lambda row: row["updated_at"], reverse=True)
        return rows

    def record_decision(self, **fields: Any) -> Any:
        """决策沉淀（[06 §2.4](../../docs/技术架构-v2/06-L4-多视角推理.md)）——转发给编排面。

        **不在此校验决策语义**（L4 `record_decision` 已声明该口径）；面缺席由调用方 fail-closed。
        """
        return self.deliberation.record_decision(**fields)


@dataclass(frozen=True)
class _AnalyzeValues:
    """`AnalyzeService.analyze` 的 `confirmation` 鸭子面（它只消费 `.values`）。"""

    values: dict[str, Any]
