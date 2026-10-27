"""L6 的 LLM 适配器：训练理解与模式观察（提示词 + 解析 + 中性守门）。"""

from __future__ import annotations

import json
from typing import Any
from st_agent.contracts.neutrality import (
    RULEPACK_KIND,
    NeutralityGuard,
    default_rulepack,
    set_official_rulepack,
)
from st_agent.l3.chat import SessionStore
from st_agent.l6 import (
    DEFAULT_TIER,
    GRADING_CONFIG_ID,
    NO_AUTHORIZATION_REASON,
    NO_REGISTRY_REASON,
    RESET_CONFIRMATIONS,
    RISK_CLASSES,
    TIER_CONFIG_ID,
    TIERS,
    ChangeProposal,
    ChangeRun,
    EvolutionRiskRule,
    L6Error,
    L6Stack,
    PatternObservation,
    PendingChange,
    SkillDraft,
    StudioHandoffError,
    SuggestionDraft,
    TrainingDraft,
    build_l6,
    week_key,
    week_window,
)


_INTENT_JSON_HINT = "仅输出一行 JSON，不得输出 JSON 以外的任何字符。"


def _strip_code_fence(raw: str) -> str:
    """剥掉 LLM 回复里常见的 ``` 围栏（模型偶发；不剥会误判为解析失败）。"""
    body = (raw or "").strip()
    if body.startswith("```"):
        body = body.strip("`")
        body = body.split("\n", 1)[1] if "\n" in body else ""
    return body.strip()


_TRAINING_JSON_CONTRACT = (
    '{"restatement": "对用户修正逻辑的结构化复述", "pattern": "提炼出的模式陈述", '
    '"suggestion": {"config_id": "点分条目 id", "current": 现值或 null, '
    '"suggested": 建议值, "reason": "中性陈述式理由"} 或 null}'
)


def training_understanding_prompt(correction: str, *, target: str = "") -> str:
    """构造训练对话概念性理解的结构化提示词（实现细节，非契约）。"""
    focus = f"（被修正的对象：{target}）" if target else ""
    return (
        "任务：把用户对系统结论的修正整理为结构化理解，供用户确认后写入记忆。\n"
        f"仅输出一行 JSON，结构为：{_TRAINING_JSON_CONTRACT}\n"
        "规则：restatement 与 pattern 用中性陈述，不得出现人称代词、情感措辞或对话体；"
        "无法给出配置建议时 suggestion 置 null；"
        f"{_INTENT_JSON_HINT}\n"
        f"用户修正{focus}：{correction}"
    )


def _parse_training_draft(raw: str) -> TrainingDraft | None:
    """把端点回的文本解析为 :class:`TrainingDraft`；任何不合形态 ⇒ ``None``（端口明示「理解不出」）。"""
    try:
        payload = json.loads(_strip_code_fence(raw))
    except (ValueError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    restatement = str(payload.get("restatement") or "").strip()
    pattern = str(payload.get("pattern") or "").strip()
    if not restatement or not pattern:
        return None
    suggestion: SuggestionDraft | None = None
    raw_suggestion = payload.get("suggestion")
    if isinstance(raw_suggestion, dict) and raw_suggestion.get("config_id"):
        try:
            suggestion = SuggestionDraft(
                config_id=str(raw_suggestion["config_id"]),
                current=raw_suggestion.get("current"),
                suggested=raw_suggestion.get("suggested"),
                reason=str(raw_suggestion.get("reason") or ""),
            )
        except (ValueError, TypeError):
            return None
    try:
        return TrainingDraft(restatement=restatement, pattern=pattern, suggestion=suggestion)
    except (ValueError, TypeError):
        return None


class LlmTrainingUnderstander:
    """训练对话「概念性理解」的真实 LLM 实现（鸭子端口 ``understand``；[08 §3]）。

    住**组合根**而非 L6——L6 层的机器守卫断言本层不 import `l0.llm` / `l0.net`
    （[`T-L6-002.1`] A1 / [08 §7] 红线），故适配器只能由同时可 import `l0.llm` 与 L6
    鸭子面的组合根承载（先例＝ :class:`_SignalEmittingAnalyze` 住 `app.py`）。

    端点不可用 / 回复不合形态 ⇒ 返回 ``None``，由 [`TrainingProtocol`] 走 `unavailable`
    （**不硬猜**）；明文 prompt 不落盘（`LlmClient` 口径）。
    """

    def __init__(
        self,
        llm: Any,
        endpoint_id: str,
        *,
        initiator: str = "l6-training",
        purpose: str = "训练对话概念性理解",
    ) -> None:
        self._llm = llm
        self._endpoint_id = endpoint_id
        self._initiator = initiator
        self._purpose = purpose

    def understand(self, correction: str, *, target: str = "") -> TrainingDraft | None:
        events = self._llm.invoke(
            self._endpoint_id,
            training_understanding_prompt(correction, target=target),
            initiator=self._initiator, purpose=self._purpose,
        )
        chunks: list[str] = []
        for event in events:
            if event.kind == "chunk":
                chunks.append(event.text)
            elif event.kind == "error":
                return None
        return _parse_training_draft("".join(chunks))


_PATTERN_JSON_CONTRACT = (
    '[{"key": "同类问题的稳定英文键", "count": 出现次数, "sample": "一条样本原话", '
    '"reason": "中性陈述式理由", "draft": {"name": "Skill 名", "description": "说明", '
    '"nodes": [{"node_id": "n1", "skill_id": "官方 skill id", "params": {}}], '
    '"edges": [], "flow_name": "ascii_name"}}]'
)


def pattern_observation_prompt(samples: tuple[str, ...]) -> str:
    """构造模式观察的结构化提示词（实现细节，非契约）。"""
    joined = "\n".join(f"- {text}" for text in samples)
    return (
        "任务：把下列用户提问归并为若干「同类问题」，只有反复出现的类别才值得建专门 Skill。\n"
        f"仅输出一行 JSON 数组，元素结构为：{_PATTERN_JSON_CONTRACT}\n"
        "规则：key 用不含空格的小写英文；count 为该类在下列提问中的出现次数；"
        "name 与 description 用中性命名，不得出现人称代词、情感措辞或对话体；"
        "draft.nodes 至少一个节点；"
        f"{_INTENT_JSON_HINT}\n"
        f"用户提问：\n{joined}"
    )


def _parse_observations(raw: str, *, guard: NeutralityGuard) -> tuple[PatternObservation, ...]:
    """把端点回的文本解析为观察序列；**文过一次 §6 门**的项才保留（不合形态的项丢弃、不静默放行）。"""
    try:
        payload = json.loads(_strip_code_fence(raw))
    except (ValueError, TypeError):
        return ()
    if not isinstance(payload, list):
        return ()
    out: list[PatternObservation] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        try:
            draft = _draft_of(item.get("draft"))
        except (ValueError, TypeError):
            continue
        if draft is None:
            continue
        reason = str(item.get("reason") or "")
        if not guard.check_name(draft.name, description=draft.description).passed:
            continue
        if reason and not guard.check_output(reason).passed:
            continue
        try:
            observation = PatternObservation(
                key=str(item.get("key") or "").strip(),
                count=int(item.get("count") or 0),
                sample=str(item.get("sample") or ""),
                refs=(),
                draft=draft,
                reason=reason,
            )
        except (ValueError, TypeError):
            continue
        if not observation.key:
            continue
        out.append(observation)
    return tuple(out)


def _draft_of(raw: Any) -> SkillDraft | None:
    """把 LLM 给出的草稿对象转成 :class:`SkillDraft`（无节点 / 无名称 ⇒ ``None``）。"""
    if not isinstance(raw, dict):
        return None
    name = str(raw.get("name") or "").strip()
    description = str(raw.get("description") or "").strip()
    nodes = raw.get("nodes")
    if not name or not description or not isinstance(nodes, list) or not nodes:
        return None
    return SkillDraft(
        name=name,
        description=description,
        nodes=tuple(dict(node) for node in nodes if isinstance(node, dict)),
        edges=tuple(dict(e) for e in (raw.get("edges") or ()) if isinstance(e, dict)),
        groups=tuple(dict(g) for g in (raw.get("groups") or ()) if isinstance(g, dict)),
        flow_name=str(raw.get("flow_name") or ""),
    )


class LlmPatternObserver:
    """模式观察面的真实 LLM 实现（鸭子端口 ``observations``；[08 §4]）。

    数据源＝L3 的对话史：本面**盘外**自持一个 `SessionStore` 读面（同一 `chat_history`
    分区；[08 §4] 明写「L6 不自行扫描对话记录」⇒ 观察面应把取样与归并放在 L6 之外），
    取最近的用户消息交 LLM 归并同类。L6 只消费其产出，不感知本适配器。

    端点不可用 / 回复不合形态 ⇒ 返回**空序列**（观察面本就没有「有模式」的证据）。
    """

    def __init__(
        self,
        llm: Any,
        endpoint_id: str,
        *,
        sessions: SessionStore,
        limit: int = 40,
        initiator: str = "l6-pattern-observer",
        purpose: str = "对话史模式识别",
        neutrality: NeutralityGuard | None = None,
    ) -> None:
        self._llm = llm
        self._endpoint_id = endpoint_id
        self._sessions = sessions
        self._limit = limit
        self._initiator = initiator
        self._purpose = purpose
        self._guard = NeutralityGuard() if neutrality is None else neutrality

    def observations(self) -> tuple[PatternObservation, ...]:
        samples = self._samples()
        if not samples:
            return ()
        events = self._llm.invoke(
            self._endpoint_id, pattern_observation_prompt(samples),
            initiator=self._initiator, purpose=self._purpose,
        )
        chunks: list[str] = []
        for event in events:
            if event.kind == "chunk":
                chunks.append(event.text)
            elif event.kind == "error":
                return ()
        return _parse_observations("".join(chunks), guard=self._guard)

    def _samples(self) -> tuple[str, ...]:
        """最近的用户消息（跨会话按落盘顺序；超过上限取最后 `limit` 条）。"""
        texts: list[str] = []
        for session_id in self._sessions.sessions():
            session = self._sessions.load(session_id)
            for message in self._sessions.chain(session):
                if message.role == "user":
                    texts.append(message.text)
        return tuple(texts[-self._limit:])
