"""L4 组合根端口的**真实现**（[`T-INT-003`](../../../项目管理/tasks/T-INT-003-M2集成关卡多视角决策闭环.md)）。

[`deliberation.OpinionSynthesizer`](deliberation.py) / [`divergence.DimensionCatalog`](divergence.py) /
[`divergence.EvidenceReviewer`](divergence.py) 三个鸭子端口的真实装配件——各端口的形态与
缺省件由 `T-L4-002` / `T-L4-003` 定下，**真实现归本关卡的组合根**（[`app.build_m2_runtime`](../app.py)）。

三条边界：

- **编排件本身不触网**（[06 §3](../../../docs/技术架构-v2/06-L4-多视角推理.md) 的「不引入 LLM 裁判」红线）
  —— 出网只发生在**注入的端口**里，即本模块。把真实现与引擎分开，是为了让引擎的
  对照逻辑保持确定性可复算（同 [`deliberation`](deliberation.py) 把方向研判推给注入端口）。
- **失败显式化**（[00 §6](../../../docs/技术架构-v2/00-架构总览.md)）：端不可用 / 载荷不可解析 /
  取数面失败一律抛 [`PortUnavailableError`](errors.py)，**不静默降级、不编造**；各端口
  的消费者按其口径处置（编排 → 该视角 `insufficient-data`；对照 → 标注未判定）。
- **中性视角（[铁律 2](../../../项目管理/工程宪法.md)）**：端口产出的**理由 / 说明文案**是生成文案，
  仍由下游各自的 §6 门把关（编排的 `check_output`、对照的 `_safe_note`）——本模块**不**再自造一层。

明文 prompt 不落盘（[`LlmClient`](../l0/llm/client.py) 的口径）。
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.l1.skills.dimensions import OFFICIAL_DIMENSIONS, DimensionSeed
from st_agent.l4.deliberation import SynthesizedView
from st_agent.l4.divergence import Dimension, EvidenceReview
from st_agent.l4.errors import PortUnavailableError

__all__ = [
    "DIMENSION_SQL",
    "LlmEvidenceReviewer",
    "LlmOpinionSynthesizer",
    "MarketDimensionCatalog",
]

#: 本地数据面的枚举口径（表与视图同列——官方 Skill 大量经 `v_` 视图取数，
#: 视图存在即其基表在缓存内可用；这里只判**存在性**，维度全集来自
#: [`OFFICIAL_DIMENSIONS`](../l1/skills/dimensions.py) 声明）。
DIMENSION_SQL = "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"

_STANCE_VALUES = ("positive", "negative", "neutral")
"""合成器允许的方向（**不含** `insufficient-data`——那是编排的判定，不是合成器的产出）。"""

_SYNTH_PROMPT = """你是多视角中性推理中的一个**中性视角**，负责依据已给出的数据形成一条结构化观点。

视角：{name}
视角职责：{description}
评判准则：{criteria}

本视角本轮取到的有效数据（JSON）：
{data}

数据充分度：{sufficiency:.2f}
参考的个性化记忆切片数：{memory_count}

请只输出一个 JSON 对象，字段：
{{"stance": "positive" | "negative" | "neutral", "key_reasons": ["<陈述式理由>", ...]}}

硬性要求：
- 只用**陈述句**描述数据与判断依据；**不得**出现第一人称、人名、性格标签、情感表达或对话体
- `key_reasons` 至少 1 条，每条一句话
- 数据不足以支撑方向时用 `neutral`，**不要**臆断涨跌
- 不要给出任何统一建议或最终结论（系统只并列呈现各视角观点）
"""

_REVIEW_PROMPT = """你在一次多视角推理的**冲突质询**中，对一条被冲突双方引用的证据做重评估。

证据引用：{ref}
引用它的视角数：{lens_count}
评估时点：{as_of}

请只输出一个 JSON 对象，字段：
{{"validity": "valid" | "invalid" | "unknown",
  "freshness": "fresh" | "stale" | "unknown",
  "weight": <0 到 1 的数或 null>,
  "note": "<一句中性说明>"}}

硬性要求：
- 依据不足时如实填 `unknown`（不猜）
- `note` 用陈述句，**不得**出现第一人称、人名、性格标签、情感表达或对话体
"""


def _invoke(llm: Any, endpoint_id: str, prompt: str, *, initiator: str, purpose: str) -> str:
    """调一次端点并收全文；端点报错 / 空应答 → :class:`PortUnavailableError`（不静默）。"""
    chunks: list[str] = []
    for event in llm.invoke(
        endpoint_id, prompt, initiator=initiator, purpose=purpose,
    ):
        if event.kind == "chunk":
            chunks.append(event.text)
        elif event.kind == "error":
            envelope = event.error_envelope
            reason = envelope.reason if envelope is not None else "端点返回错误事件"
            status = envelope.status if envelope is not None else "failed"
            raise PortUnavailableError(f"LLM 端点不可用（{status}）：{reason}")
    text = "".join(chunks).strip()
    if not text:
        raise PortUnavailableError("LLM 端点未返回任何内容")
    return text


def _parse_json_object(raw: str) -> dict[str, Any]:
    """解析端点回的 JSON 对象（容忍 ``` 围栏）；不可解析 → :class:`PortUnavailableError`。"""
    body = raw.strip()
    if body.startswith("```"):
        body = body.strip("`")
        body = body.split("\n", 1)[1] if "\n" in body else ""
    try:
        payload = json.loads(body)
    except (ValueError, TypeError) as exc:
        raise PortUnavailableError(f"端点返回无法解析为 JSON：{exc}") from exc
    if not isinstance(payload, dict):
        raise PortUnavailableError("端点返回的 JSON 顶层不是对象")
    return payload


class LlmOpinionSynthesizer:
    """方向研判的**真实现**（[`OpinionSynthesizer`](deliberation.py)；[06 §2.2](../../../docs/技术架构-v2/06-L4-多视角推理.md)）。

    经注入的 [`LlmClient`](../l0/llm/client.py) 对「该视角的数据输出 + 记忆切片 + 数据充分度」
    做一次方向研判，产出 `stance` + 陈述式理由。**失败即抛**
    [`PortUnavailableError`](errors.py)——由编排的失败隔离转成该视角 `insufficient-data`，
    不产出编造的观点（同缺省件「数据不足」路径的中性口径）。

    :param llm: `LlmClient`（或同形鸭子件）
    :param endpoint_id: 端点标识（[01 §1](../contracts/identifiers.py) 形态校验由 `LlmClient` 做）
    """

    def __init__(
        self,
        llm: Any,
        endpoint_id: str,
        *,
        initiator: str = "l4-synthesis",
        purpose: str = "多视角观点合成",
    ) -> None:
        self._llm = llm
        self._endpoint_id = endpoint_id
        self._initiator = initiator
        self._purpose = purpose

    def synthesize(
        self,
        lens: Any,
        ok_outputs: list[Any],
        memory_slice_ids: tuple[str, ...],
        sufficiency: float,
    ) -> SynthesizedView:
        prompt = _SYNTH_PROMPT.format(
            name=getattr(lens, "name", ""),
            description=getattr(lens, "description", ""),
            criteria=_criteria_text(lens),
            data=json.dumps(ok_outputs, ensure_ascii=False, default=str),
            sufficiency=float(sufficiency),
            memory_count=len(memory_slice_ids),
        )
        payload = _parse_json_object(
            _invoke(self._llm, self._endpoint_id, prompt,
                    initiator=self._initiator, purpose=self._purpose)
        )
        stance = payload.get("stance")
        if stance not in _STANCE_VALUES:
            raise PortUnavailableError(
                f"合成端点返回的 stance 非法（{stance!r}，须为 {list(_STANCE_VALUES)} 之一）"
            )
        reasons = payload.get("key_reasons")
        if not isinstance(reasons, (list, tuple)) or not reasons:
            raise PortUnavailableError("合成端点未给出 key_reasons（至少 1 条）")
        return SynthesizedView(
            stance=stance, key_reasons=tuple(str(r) for r in reasons),
        )


def _criteria_text(lens: Any) -> str:
    """视角评判准则的文本取值面（`judging_criteria.natural`；缺失即空串，不臆造）。"""
    criteria = getattr(lens, "judging_criteria", None)
    return str(getattr(criteria, "natural", "") or "")


class MarketDimensionCatalog:
    """盲点判定的**维度目录真实现**（[`DimensionCatalog`](divergence.py)；[06 §3](../../../docs/技术架构-v2/06-L4-多视角推理.md)）。

    「维度全集」来自官方 Pack 旁的 [`OFFICIAL_DIMENSIONS`](../l1/skills/dimensions.py) 声明
    （维度 → 产出它的官方 Skill），本件**读本地市场库判存在性**——只有承载表确实在
    缓存里的维度才返回，避免把本地根本没有的数据面报成盲点。产出 Skill 经注入的
    `SkillRegistry.get_latest(base)` 解析为 `skill_id`；**解析不出的 base 不计入**
    （不臆造 Skill 标识），该维度随之 `skill_ids` 为空 → 对照件按其既有口径记「覆盖不可判」。

    `topic` 参数按端口形态接收但**不做主题裁剪**：自然语言主题 → 数据域的相关性
    判定不是算法性的，本件如实返回本地数据面的**全部**维度域；「与主题相关」这一步
    由**参与视角的覆盖判据**在下游承担（06 §3 的盲点定义即「有维度但无视角引用」）。

    :param market_query: 取数源（`MarketQuerySource` 鸭子类型，如 `MarketDb`）；
        缺省 `None` → 调用即抛（组合根须注入，不静默当无盲点）
    :param skills: `SkillRegistry`（或同形鸭子件，须有 `get_latest(base)`）；缺省 `None`
        → 产出 Skill 一律不可解析（如实留空，而非臆造）
    """

    def __init__(
        self,
        market_query: Any = None,
        *,
        skills: Any = None,
        dimensions: tuple[DimensionSeed, ...] = OFFICIAL_DIMENSIONS,
    ) -> None:
        self._market = market_query
        self._skills = skills
        self._dimensions = dimensions

    def dimensions_for(self, topic: str) -> tuple[Dimension, ...]:
        """本地缓存中**确实存在**的数据维度（含其产出 Skill 声明）。"""
        present = self._present_names()
        out: list[Dimension] = []
        for seed in self._dimensions:
            if present.isdisjoint(seed.tables):
                continue
            out.append(Dimension(
                key=seed.key, label=seed.label, skill_ids=self._skill_ids(seed),
            ))
        return tuple(out)

    def _present_names(self) -> set[str]:
        """本地市场库里已建的表与视图名（取数面失败 → 抛，由对照件标注未判定）。"""
        if self._market is None:
            raise PortUnavailableError("未注入取数源，维度目录不可用（组合根须注入 MarketDb）")
        envelope = self._market.query(DIMENSION_SQL)
        if not isinstance(envelope, ResultEnvelope):
            raise PortUnavailableError(
                f"取数源返回非法类型 {type(envelope).__name__}（须为 ResultEnvelope）"
            )
        if envelope.status != "ok":
            raise PortUnavailableError(
                f"维度目录取数失败（{envelope.status}）：{envelope.reason}"
            )
        data = envelope.data
        rows = data.get("rows") if isinstance(data, dict) else None
        if not isinstance(rows, (list, tuple)):
            raise PortUnavailableError(
                "维度目录取数载荷缺 'rows' 键（口径同 MarketDb.query 的 {'columns','rows'}）"
            )
        return {str(row["name"]) for row in rows if isinstance(row, dict) and "name" in row}

    def _skill_ids(self, seed: DimensionSeed) -> tuple[str, ...]:
        """声明的产出 Skill base → `skill_id`（解析不出的跳过，不臆造标识）。"""
        if self._skills is None:
            return ()
        resolved: list[str] = []
        for base in seed.skill_bases:
            try:
                descriptor = self._skills.get_latest(base)
            except Exception:  # noqa: BLE001 —— 注册表寻址失败即不计入（不臆造）
                continue
            skill_id = getattr(descriptor, "skill_id", None)
            if isinstance(skill_id, str) and skill_id:
                resolved.append(skill_id)
        return tuple(resolved)


class LlmEvidenceReviewer:
    """证据重研判的**真实现**（[`EvidenceReviewer`](divergence.py)；[06 §4](../../../docs/技术架构-v2/06-L4-多视角推理.md)）。

    对一条冲突证据做「成立 / 时效 / 权重」的研判。**失败不抛**——返回三态 `unknown`
    并把不可用如实写进 `note`：对照件对重评估是**逐条同步调用**且不设异常兜底，
    一条证据研判失败不应毁掉整张分歧图；同时「未判定」必须是**可见**的降级，
    不能静默当成「已判定」。
    """

    def __init__(
        self,
        llm: Any,
        endpoint_id: str,
        *,
        initiator: str = "l4-evidence-review",
        purpose: str = "冲突证据重研判",
    ) -> None:
        self._llm = llm
        self._endpoint_id = endpoint_id
        self._initiator = initiator
        self._purpose = purpose

    def review(
        self, ref: str, *, lens_ids: tuple[str, ...], as_of: datetime | None,
    ) -> EvidenceReview:
        prompt = _REVIEW_PROMPT.format(
            ref=ref, lens_count=len(lens_ids),
            as_of=as_of.isoformat() if as_of is not None else "未提供",
        )
        try:
            payload = _parse_json_object(_invoke(
                self._llm, self._endpoint_id, prompt,
                initiator=self._initiator, purpose=self._purpose,
            ))
        except PortUnavailableError as exc:
            return EvidenceReview(ref=ref, note=f"证据重研判端点不可用，本条未判定：{exc}")
        return EvidenceReview(
            ref=ref,
            validity=_one_of(payload.get("validity"), ("valid", "invalid")),
            freshness=_one_of(payload.get("freshness"), ("fresh", "stale")),
            weight=_weight(payload.get("weight")),
            note=str(payload.get("note") or ""),
        )


def _one_of(value: Any, allowed: tuple[str, ...]) -> Any:
    """端点取值落在允许集才采用，否则留 `unknown`（不臆断）。"""
    return value if value in allowed else "unknown"


def _weight(value: Any) -> float | None:
    """权重取值面（数值且落在 [0, 1] 才采用，否则不判）。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if 0.0 <= float(value) <= 1.0 else None
