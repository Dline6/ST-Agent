"""`analyze` 去向的 L4 装配件（[06 §2](../../../docs/技术架构-v2/06-L4-多视角推理.md)；[`T-INT-003`](../../../项目管理/tasks/T-INT-003-M2集成关卡多视角决策闭环.md)）。

L3 派发总线的 `analyze` 去向（「触发 [06-L4](06-L4-多视角推理.md) Deliberation」）经**组合根注入的
端口**落到本件——[铁律 7](../../../项目管理/工程宪法.md) 禁 L3 import L4，故总线只见一个鸭子面
（须含 `envelope: ResultEnvelope`），本件是它背后三个 L4 件的**唯一装配点**：

    编排（视角并行产出观点） → 交叉对照（一致 / 分歧 / 盲点 / Mini Debate） → 视图投影（矩阵 + 网络）

**三段各自的职责不在此重述**（见各模块）；本件只做**衔接**：解析 `topic` / `mode`、
按序调用三段、把失败信封**原样透出**（不重包、不改 status、不吞 reason，同总线对其余
去向的口径），并把三条产物并列承载给渲染方（`result` / `map` / `view` / `traces`）。

**无「汇总结论」**（[06 架构级红线](../../docs/技术架构-v2/06-L4-多视角推理.md)、[铁律 4](../../../项目管理/工程宪法.md)）：
本件产出的信封载荷只有**描述性事实**（主题 / 模式 / 参与视角 id / 对照项计数），
无 `verdict` / `recommendation` / `advice` 类字段；分歧、盲点、冲突一律**并列**承载。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from st_agent.contracts.result_envelope import ResultEnvelope

__all__ = ["DEFAULT_MODE", "MODES", "AnalyzeOutcome", "AnalyzeService"]

MODES: tuple[str, ...] = ("quick", "deep")
"""06 §2.1 的两种模式（快速 = 核心少数视角；深度 = 全部启用视角）。"""

DEFAULT_MODE = "deep"
"""模式未给出时的取值（[05 §3.2](../../docs/技术架构-v2/05-L3-对话主入口.md)：澄清问项跳过即取 `default`）。"""


class AnalyzeOutcome(BaseModel):
    """一次 `analyze` 派发的载荷（信封 + 三条 L4 产物，**并列不合并**）。

    总线按鸭子面消费本件（只认 `envelope`）；渲染方（组合根的对话门面）按属性取
    `view`（分歧图描述件的取数面）与 `traces`（追问接口的取数面）。
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    envelope: ResultEnvelope
    """本次派发的信封（编排失败时即其失败信封，**原样**）。"""
    topic: str = ""
    mode: str = ""
    result: Any = None
    """[`DeliberationResult`](deliberation.py)（观点 + 每视角链）；编排未跑成时为 `None`。"""
    map: Any = None
    """[`DisagreementMap`](divergence.py)（一致 / 分歧 / 盲点 / Mini Debate + 对照链）。"""
    view: Any = None
    """[`DivergenceView`](divergence_view.py)（矩阵 + 网络 + 一致性标志）。"""
    traces: tuple[Any, ...] = ()
    """各视角推理链（[06 §5](../../docs/技术架构-v2/06-L4-多视角推理.md) 追问接口：按 `trace_id` 展开）。"""

    def trace_for(self, trace_id: str) -> Any:
        """按 `trace_id` 取该视角的链（矩阵行的 `trace_id` 即此键）；取不到返回 `None`。"""
        for trace in self.traces:
            if getattr(getattr(trace, "trace_id", None), "value", None) == trace_id:
                return trace
        return None


class AnalyzeService:
    """`analyze` 去向的装配门面（总线的注入端口）。

    :param deliberation: [`Deliberation`](deliberation.py)（鸭子类型 `deliberate(topic, mode=…)`）
    :param examiner: [`CrossExaminer`](crosscheck.py)（鸭子类型 `cross_examine(result)`）
    :param viewer: [`DivergenceViewer`](divergence_view.py)（鸭子类型 `build(result, map)`）；
        缺省 `None` → 视图不产（描述件走 `unavailable`，不返回残缺卡）
    """

    def __init__(self, *, deliberation: Any, examiner: Any, viewer: Any = None) -> None:
        self._deliberation = deliberation
        self._examiner = examiner
        self._viewer = viewer

    def analyze(
        self,
        confirmation: Any,
        *,
        values: Any = None,
        now: Any = None,
    ) -> AnalyzeOutcome:
        """跑一次 Deliberation 全链并承载三条产物（失败信封印在 `envelope` 里，不抛）。

        `confirmation` 为鸭子类型（消费 `.values`）；`topic` / `mode` 取自确认卡取值
        并允许 `values` 覆盖——两者都由 [05 §3.2](../../docs/技术架构-v2/05-L3-对话主入口.md) 的澄清
        协议收敛（`mode` 的问项来源是**意图级参数声明**，同 §3.2 的问项来源规则）。
        """
        merged = {**dict(getattr(confirmation, "values", {}) or {}), **dict(values or {})}
        topic = str(merged.get("topic") or "").strip()
        mode = str(merged.get("mode") or DEFAULT_MODE)
        if mode not in MODES:
            return AnalyzeOutcome(
                envelope=ResultEnvelope.validation_failed(
                    f"未知分析模式 {mode!r}（须为 {list(MODES)} 之一，06 §2.1）"
                ),
                topic=topic, mode=mode,
            )

        result = self._deliberation.deliberate(topic, mode=mode)
        envelope = getattr(result, "envelope", None)
        if not isinstance(envelope, ResultEnvelope):
            return AnalyzeOutcome(
                envelope=ResultEnvelope.dependency_failed(
                    f"编排面返回非法结构（缺 ResultEnvelope）：{type(result).__name__}"
                ),
                topic=topic, mode=mode,
            )
        if envelope.status != "ok":  # 空阵容 / 缺主题 / 未注入执行面 —— 原样透出，不重包
            return AnalyzeOutcome(
                envelope=envelope, topic=topic, mode=mode, result=result,
            )

        try:
            disagreement_map = self._examiner.cross_examine(result)
            view = (
                self._viewer.build(result, disagreement_map)
                if self._viewer is not None else None
            )
        except Exception as exc:  # noqa: BLE001 —— 注入端口的缺陷不外溢为派发崩溃
            # 对照 / 视图件消费**注入的**端口（维度目录、证据重研判），其实现不属本层；
            # 缺陷若抛穿会越过派发面直达回环端点（[05 §4](../../docs/技术架构-v2/05-L3-对话主入口.md)：
            # 派发恒出信封，不得以异常收场）。故显式转 `dependency_failed`，不静默、不编造。
            return AnalyzeOutcome(
                envelope=ResultEnvelope.dependency_failed(
                    f"多视角对照 / 视图投影失败（注入端口缺陷）：{exc}",
                    log_ref="l4/analyze",
                ),
                topic=topic, mode=mode, result=result,
            )
        return AnalyzeOutcome(
            envelope=ResultEnvelope.ok(
                {
                    "topic": topic,
                    "mode": mode,
                    "lens_ids": list(getattr(result, "lens_ids", ()) or ()),
                    "disagreements": len(disagreement_map.disagreements),
                    "blind_spots": len(disagreement_map.blind_spots),
                },
                as_of=envelope.as_of,
                evidence_refs=envelope.evidence_refs,
            ),
            topic=topic, mode=mode, result=result, map=disagreement_map, view=view,
            traces=tuple(getattr(result, "traces", ()) or ()),
        )
