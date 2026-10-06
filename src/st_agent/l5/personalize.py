"""L5 推送文案个性化（[07 §4](../../../docs/技术架构-v2/07-L5-主动触达.md)）。

**个性化 ≠ 拟人化**（[铁律 2](../../../项目管理/工程宪法.md)）：本模块只按用户的记忆切片
调**措辞档**（说多说少、附不附证据），**不造人格、不用第一人称、不加情绪词**——
这正是 [07 §4](../../docs/技术架构-v2/07-L5-主动触达.md) 那句括注的意思。

**档位由切片的置信度决定**——[04 §3.1](../../docs/技术架构-v2/04-L2-记忆图谱.md) 的
[`MemorySlice.confidence`](../../l2/memory/reader.py) 原文即为「消费方**按它调整表述强度**」，
故本模块正是它的消费者：对这位用户了解越深（置信度越高），这一条推送可以说得越细。

| 有效置信度 | 措辞档 | 差别（**只在信息密度**） |
|---|---|---|
| ≥ 0.75 | `detailed` | 结论 + 逐视角摘要 + 证据条数 + 逐条证据引用 + 溯源锚点 |
| ≥ 0.40 | `standard` | 结论 + 逐视角摘要 + 证据条数 |
| < 0.40 | `concise` | 结论 + 逐视角摘要 |
| 无切片 / 未注入 | `standard` | 中性缺省（同上） |

**三种档位都并列保留逐视角摘要**（[铁律 4](../../../项目管理/工程宪法.md)：分歧不合并）——
个性化改的是「说多少」，不是「说什么」。

三类降级（都不阻断投递）：

- 未注入 `MemoryReader` / 查询抛错 / 空切片 ⇒ 回落 :data:`DEFAULT_WORDING`（中性缺省），
  并在 :attr:`WordingProfile.note` 里写明理由；
- 生成的整段文案**过 [01 §6](../../docs/技术架构-v2/01-平台共享契约.md) 执行点 2**——
  命中即显式抛错（**模板自身**的问题不该被静默吞掉；这与「用户原话属数据展示、不整串复检」
  是两回事，见 [D-053](../../../项目管理/决策日志.md)）；
- 用到的切片 `memory_node_id` 进 :attr:`WordingProfile.slice_refs`，故「为什么这么说」可追溯。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from st_agent.contracts.neutrality import NeutralityGuard
from st_agent.l2.memory import SliceQuery
from st_agent.l5.errors import DeliveryValidationError
from st_agent.l5.signal import Signal

__all__ = [
    "DEFAULT_WORDING",
    "LEVEL_LABELS",
    "WORDING_BY_CONFIDENCE",
    "Personalizer",
    "Wording",
    "WordingProfile",
]

Wording = Literal["concise", "standard", "detailed"]

DEFAULT_WORDING: Wording = "standard"
"""无记忆可依时的中性缺省（结论 + 逐视角摘要 + 证据条数）。"""

WORDING_BY_CONFIDENCE: tuple[tuple[float, Wording], ...] = (
    (0.75, "detailed"),
    (0.40, "standard"),
)
"""置信度门限 → 措辞档（**从高到低**；都不到即 :data:`DEFAULT_WORDING` 之下的一档）。

口径来源：04 §3.1 的「消费方按置信度调整表述强度」。产品口径，可经构造参数整体覆写。
"""

LEVEL_LABELS: Mapping[str, str] = {
    "emergency": "紧急", "important": "重要", "routine": "日常",
}
"""级别标签（中性名词，不含情绪词）。"""

_QUERY_TASK_TYPE = "delivery"
"""切片查询的任务类型——L2 `TASK_TYPE_AFFINITY` 的 `delivery` 行
（`attention` / `thesis`：用户的关注面与既有观点，正是推送措辞的依据）。"""


class WordingProfile(BaseModel):
    """一次个性化选定的措辞档（**可解释**：带上用到的切片引用与选档依据）。"""

    model_config = ConfigDict(frozen=True)

    wording: Wording
    source: Literal["memory", "default"]
    """本档来自记忆切片还是中性缺省（数据面可分；渲染归表现层）。"""
    slice_refs: tuple[str, ...] = ()
    """参与选档的记忆节点 id（按切片顺序）。"""
    note: str = ""
    """中性说明（选档依据 / 缺记忆时的降级理由）。"""


class Personalizer:
    """推送文案的产出面（07 §4）。

    :param reader: [L2 `MemoryReader`](../../l2/memory/reader.py) 的鸭子面
        （只用到 ``query(SliceQuery(...))``）；缺省 ``None`` ⇒ 恒走中性缺省
    :param guard: 中性化守卫（缺省用模块级统一件；注入以便测试固定规则库）
    :param thresholds: 置信度门限覆写（缺省 :data:`WORDING_BY_CONFIDENCE`）
    """

    def __init__(
        self,
        *,
        reader: Any = None,
        guard: NeutralityGuard | None = None,
        thresholds: tuple[tuple[float, Wording], ...] | None = None,
    ) -> None:
        self._reader = reader
        self._guard = guard if guard is not None else NeutralityGuard()
        self._thresholds = tuple(
            WORDING_BY_CONFIDENCE if thresholds is None else thresholds
        )

    # ───────────────────────── 档位选择 ─────────────────────────

    def profile(self, signal: Signal) -> WordingProfile:
        """按记忆切片选措辞档（**不阻断**：取不到即中性缺省）。"""
        if self._reader is None:
            return WordingProfile(
                wording=DEFAULT_WORDING, source="default",
                note="未注入记忆读取面：按中性缺省措辞",
            )
        try:
            result = self._reader.query(_slice_query(signal))
        except Exception as exc:  # 读取面实现缺陷 → 降级但仍显式说明
            return WordingProfile(
                wording=DEFAULT_WORDING, source="default",
                note=f"记忆读取面不可用（{exc}）：按中性缺省措辞",
            )
        slices = tuple(getattr(result, "slices", ()) or ())
        if not slices:
            return WordingProfile(
                wording=DEFAULT_WORDING, source="default",
                note="无相关记忆切片：按中性缺省措辞",
            )
        top = slices[0]
        confidence = _confidence_of(top)
        wording = _wording_for(confidence, self._thresholds)
        return WordingProfile(
            wording=wording, source="memory",
            slice_refs=tuple(
                str(getattr(getattr(s, "node", None), "memory_node_id", ""))
                for s in slices
            ),
            note=f"按切片有效置信度 {confidence:.2f} 选档（04 §3.1：按置信度调整表述强度）",
        )

    # ───────────────────────── 文案产出 ─────────────────────────

    def build(self, signal: Signal) -> tuple[str, str, WordingProfile]:
        """产出 ``(title, body, profile)``——逐段过 01 §6（命中即显式抛错）。"""
        profile = self.profile(signal)
        title = f"{LEVEL_LABELS[signal.level]}：{_topic_of(signal)}"
        lines = [signal.content_ref.conclusion]
        lines.extend(
            f"[{stance.stance}] {stance.summary}" for stance in signal.content_ref.lens_stances
        )
        if profile.wording != "concise":
            lines.append(f"证据 {len(signal.evidence_refs)} 条")
        if profile.wording == "detailed":
            lines.extend(f"- {ref}" for ref in signal.evidence_refs)
            lines.append(f"溯源 {signal.source_trace_id}")
        body = "\n".join(lines)
        self._require_neutral(title, "推送标题")
        self._require_neutral(body, "推送正文")
        return title, body, profile

    # ───────────────────────── 内部 ─────────────────────────

    def _require_neutral(self, text: str, where: str) -> None:
        verdict = self._guard.check_output(text)
        if not verdict.passed:
            hits = " / ".join(f"{f.kind}:{f.matched!r}" for f in verdict.findings)
            raise DeliveryValidationError(
                f"{where} 未过 01 §6 中性化校验（{hits}）——文案模板须中性，"
                f"个性化只调措辞档、不造人格（07 §4 / 铁律 2）：{text!r}"
            )


def _wording_for(
    confidence: float, thresholds: tuple[tuple[float, Wording], ...],
) -> Wording:
    for floor, wording in thresholds:
        if confidence >= floor:
            return wording
    return "concise"


def _confidence_of(slice_: Any) -> float:
    """切片的有效置信度（缺字段即视为 0——保守落到最低档，不抬高表述强度）。"""
    value = getattr(slice_, "confidence", None)
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _topic_of(signal: Signal) -> str:
    """标题里的主题词——取 `dedup_key` 的首段（`<标的>:<事件类型>` 的标的）。"""
    head = signal.dedup_key.split(":", 1)[0].strip()
    return head or signal.signal_id


def _slice_query(signal: Signal) -> SliceQuery:
    """构造切片查询（口径见 :data:`_QUERY_TASK_TYPE`）。"""
    return SliceQuery(task_type=_QUERY_TASK_TYPE, topic=_topic_of(signal))
