"""L6 反思与演进门面（周报 / 提案 / 训练 / 画布会话）。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from st_agent.contracts.neutrality import (
    RULEPACK_KIND,
    NeutralityGuard,
    default_rulepack,
    set_official_rulepack,
)
from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.l1.studio import (
    DraftAcceptError,
    DraftIntake,
    DraftSessionError,
    DraftShapeError,
    TemplateLibrary,
    draft_from_dag,
    studio_empty_state,
)
from st_agent.l5.channels import (
    ChannelPayload,
    DesktopChannel,
    EmailChannel,
    ImWebhookChannel,
    TtsChannel,
)
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


_CHANGE_EVENTS = ("ChangeApplied", "ChangeRolledBack")
"""演进变更的告知事件（[01 §11](../../docs/技术架构-v2/01-平台共享契约.md)；发布方＝L6 变更流，
见 [08 §5](../../docs/技术架构-v2/08-L6-反思演进.md)）。"""


class _ChangeNotifier:
    """订阅 `ChangeApplied` / `ChangeRolledBack`，把一次变更投成一条**中性告知**。

    沿 [`ChannelPolicies`](../l5/channel_policy.py) 的**有序链**投出 `ChannelPayload`
    （同周报 `deliver` 的口径，不另造 L5 面）；文案由本面产出并过 [01 §6] 执行点 2。
    未接线 / 渠道链全不可用 / 文案未过门 ⇒ **如实记因**，不假装告知（[01 §11] 同款口径）。
    """

    def __init__(
        self,
        *,
        dispatcher: Any,
        policies: Any,
        level: str = "important",
        failures: list | None = None,
        neutrality: NeutralityGuard | None = None,
    ) -> None:
        self._dispatcher = dispatcher
        self._policies = policies
        self._level = level
        self._failures = failures if failures is not None else []
        self._guard = NeutralityGuard() if neutrality is None else neutrality
        self.notices: list[Any] = []
        self.subscription: tuple[Any, ...] = ()

    def attach(self, events: Any, *, subscriber_id: str = "m4:change-notifier") -> tuple[Any, ...]:
        """把本面订阅到总线的两个变更事件上（[01 §11]）。"""
        self.subscription = tuple(
            events.subscribe(name, self._handle, subscriber_id=subscriber_id)
            for name in _CHANGE_EVENTS
        )
        return self.subscription

    def _handle(self, event: Any) -> None:
        payload = dict(getattr(event, "payload", None) or {})
        change_id = str(getattr(event, "change_id", None) or payload.get("change_id") or "")
        config_id = str(payload.get("config_id") or "")
        tier = str(payload.get("tier") or "")
        rolled_back = str(getattr(event, "event", "")) == "ChangeRolledBack"
        title = "演进变更已回滚" if rolled_back else "演进建议已生效"
        body = (
            f"配置条目 {config_id} 的取值由 {payload.get('old_value')!r} 改为 "
            f"{payload.get('new_value')!r}（授权档：{tier}）。该改动已记入变更历史，可一键回滚。"
        )
        label = change_id or config_id or "（未知变更）"
        if not self._neutral(title, body):
            self._failures.append(f"变更 {label} 的告知文案未过 01 §6 中性化校验，未投出")
            return
        chain = tuple(self._policies.get(self._level).channels)
        if not chain:
            self._failures.append(
                f"变更 {label} 的告知无可用渠道链（级别 {self._level}），未投出"
            )
            return
        notice = ChannelPayload(
            signal_id=change_id or f"change:{config_id}" or "change",
            level=self._level, title=title, body=body,
            trace_id=str(getattr(event, "trace_id", None) or change_id or f"change:{config_id}"),
        )
        try:
            dispatch = self._dispatcher.deliver(list(chain), notice)
        except Exception as exc:  # noqa: BLE001 —— 投出端缺陷：显式记因，不阻断其余订阅者
            self._failures.append(f"变更 {label} 的告知投出失败：{exc}")
            return
        if getattr(dispatch, "delivered", None) is None:
            self._failures.append(f"变更 {label} 的告知未投出（渠道链全不可用）")
            return
        self.notices.append(notice)

    def _neutral(self, *texts: str) -> bool:
        return all(self._guard.check_output(text).passed for text in texts)


_DECISION_ACTIONS = ("accept", "reject", "defer")
"""提案处置的三动作（[08 §5]：接受 / 否决 / 延后——**排队而非丢弃**）。"""


_STUDIO_ACTIONS = ("accept", "reject")
"""已交 Studio 提案的两动作（[`T-L6-004.2`]）——**微调**走画布编辑面（`/api/studio/edit`）。"""


_CANVAS_EDIT_OPS = ("add_node", "remove_node", "connect", "disconnect", "group", "ungroup")
"""Studio 画布的六个结构编辑操作（[`T-UI-005.1`]；与 `CanvasEditor` 的公开写面一一对应）。

**不含** `params`（节点参数绑定）——那是 Skill 级表单面（story-06 的参数面板），
本批不开放（[`T-UI-005.1` A3](tasks/T-UI-005.1-后端画布读面与编辑转交.md)）。"""


_NO_FACTORY_RESET_REASON = "未接入出厂重置面，无法清空演进状态（装配归组合根）"
"""出厂重置子面缺席时的点名原因（同 [08 §5] 的「未接判据即 fail-closed」口径）。"""


_NOT_FILED_NOTE = (
    "该提案未进入待批准队列（当前档位或风险类不予立案），无可否决 / 延后的项"
    "——「手动」档下提案止步于建议面（08 §5）"
)
"""对未立案提案做否决 / 延后时的如实说明（**不是**失败：确实无事可做）。"""


def _require_week(week: str) -> str:
    """校验周键形态——非法即 ``ValueError``（用户可修正的输入，表现层回 `validation_failed`）。

    复用 L6 的 :func:`week_window` 作判据（**不另写一份正则**：周键口径只有一处）。
    """
    try:
        week_window(week)
    except L6Error as exc:
        raise ValueError(str(exc)) from exc
    return week


def _change_proposal(raw: Mapping[str, Any]) -> ChangeProposal:
    """请求体 → :class:`ChangeProposal`（形态非法 ⇒ pydantic 的 `ValidationError`，属 `ValueError`）。"""
    payload = dict(raw)
    for required in ("config_id", "current", "suggested", "reason"):
        if required not in payload:
            raise ValueError(f"提案缺少 {required!r}（见 08 §5 的提案形态）")
    return ChangeProposal(**payload)


def _grading_rules(raw: Any) -> tuple[EvolutionRiskRule, ...]:
    """请求体 → 风险分级规则表（逐条构造即校验，不吃裸 dict）。"""
    if not isinstance(raw, (list, tuple)):
        raise ValueError("风险分级清单须为条目列表")
    rules: list[EvolutionRiskRule] = []
    for item in raw:
        if not isinstance(item, Mapping):
            raise ValueError("风险分级条目须为对象（prefix / risk_class）")
        rules.append(EvolutionRiskRule(**dict(item)))
    return tuple(rules)


class ReflectionFacade:
    """L6 反思演进栈的**表现层适配面**（组合根所有；`ui` 只经鸭子端口消费，[`T-UI-004.2`]）。

    两件事都在组合根做完，故表现层不必（也不得）import 任何 L6 类型：

    - **取数**——把 L6 读面的 pydantic 形态转成 JSON 就绪的普通结构；
    - **输入类失败归一**——用户可修正的输入（周键形态 / 档位取值 / 待批准项不存在 / 确认次数不足）
      在**调到 L6 之前**就地转 ``ValueError``，表现层据此回 ``validation_failed``（[01 §5]）；
      **状态损坏**这类内部失败**不吞、不上抛为输入错**，原样传出，由表现层回 ``failed`` + `log_ref`
      （[00 §6] 失败显式化）。判据一律复用 L6 自己的常量 / 校验函数，不另写一份。

    **子面未接线**（`authorization` / `change_flow` / `factory_reset` 缺）时返回
    ``{"available": False, "reason": …}``——表现层据此回 ``unavailable`` + 点名，与「面在但无数据」
    分开（[01 §5] 六态不可混用）。
    """

    def __init__(self, *, l6: L6Stack, feedback: Any, skills: Any = None) -> None:
        self._l6 = l6
        self._feedback = feedback
        """L3 的反馈采集面（`FeedbackCollector.record` 的绑定方法）——反馈的**产生方是交互层**，
        本面只转发（[01 §1] / [05 §9]）。"""
        self._skills = skills
        """L1 `SkillRegistry`（鸭子类型 `tool_catalog()`）——Studio 画布页「加节点」的可选 Skill
        清单来源（[`T-UI-005.1`]）；缺省 ``None`` ⇒ 该子面 fail-closed 并点名，**不伪造**清单。"""

    # ── 周报 ────────────────────────────────────────────────────────────────
    def report_weeks(self) -> tuple[str, ...]:
        """已生成过的周键（升序）——表现层列历史与取最近一期。"""
        return tuple(self._l6.reports.stored_weeks())

    def report(self, week: str) -> dict[str, Any] | None:
        """某周的报告；从未生成 ⇒ ``None``（表现层据此给「尚未生成」而非空报告）。"""
        stored = self._l6.reports.stored(_require_week(week))
        return None if stored is None else stored.model_dump(mode="json")

    # ── 反馈采集 ────────────────────────────────────────────────────────────
    def record_feedback(
        self,
        *,
        target: Any,
        action: str,
        reason: str | None = None,
        context: Mapping[str, Any] | None = None,
    ) -> ResultEnvelope:
        """转交 L3 采集面（载荷不合契约时它自己回 `validation_failed`，本面不预判）。"""
        if not isinstance(action, str) or not action:
            raise ValueError("反馈动作须为非空字符串（08 §1 的五类之一）")
        if target is None:
            raise ValueError("反馈对象（target）必填——无法寻址的反馈一律拒收")
        return self._feedback(target, action, reason=reason, context=context)

    # ── 提案与待批准项 ──────────────────────────────────────────────────────
    def proposals(self) -> list[dict[str, Any]]:
        """已检出的主动提案（含 Skill 草稿）。"""
        return [item.model_dump(mode="json") for item in self._l6.proposals.all()]

    # ── 主动提案落 Studio（[`T-L6-004.2`] / [08 §4]「衔接 story-06」） ──────
    def studio_handoff(self, proposal_id: str) -> dict[str, Any]:
        """把一条主动提案的 Skill 草稿交 **Studio 草稿接收面**（落画布）。

        组合根在此把 L6 的 ``SkillDraft`` 翻译为 L1 的 ``WorkflowDraft`` 并转交
        [`DraftIntake.receive`](../l1/studio/draft.py)——表现层**不 import** 任何层类型。

        子面未接线（`L6Stack.studio is None`）→ ``{"available": False, "reason": …}``
        （与「面在但无提案」分开）；输入类失败（提案不存在 / 草稿形状非法 / 未接接收面）
        **就地转 ``ValueError``**，表现层据此回 `validation_failed`。
        """
        studio = self._l6.studio
        if studio is None or not studio.available:
            return {"available": False, "reason": "未接入 Studio 草稿接收面（08 §4）"}
        if not isinstance(proposal_id, str) or not proposal_id:
            raise ValueError("提案标识（proposal_id）必填——无法寻址的提案一律拒收")
        try:
            view = studio.handoff(proposal_id)
        except StudioHandoffError as exc:
            raise ValueError(str(exc)) from exc
        except (DraftShapeError, DraftAcceptError) as exc:
            raise ValueError(str(exc)) from exc
        return {**view.model_dump(mode="json"), "available": True}

    def studio_decide(self, *, proposal_id: str, action: str) -> dict[str, Any]:
        """处置一条**已交 Studio** 的提案：接受（落 v1.0 创建 Skill）/ 否决。

        两个动作都**委托** L1 `DraftIntake`（本面不复制其判定、不另造落盘）。**画布微调**
        是另一条路（[`studio_edit`] 经 `CanvasEditor`），故此处只认这两动作。
        """
        studio = self._l6.studio
        if studio is None or not studio.available:
            return {"available": False, "reason": "未接入 Studio 草稿接收面（08 §4）"}
        if action not in _STUDIO_ACTIONS:
            raise ValueError(
                f"处置动作须为 {'/'.join(_STUDIO_ACTIONS)}（画布微调走 /api/studio/edit，08 §4）"
            )
        if not isinstance(proposal_id, str) or not proposal_id:
            raise ValueError("提案标识（proposal_id）必填——无法寻址的提案一律拒收")
        try:
            if action == "accept":
                accepted = studio.accept(proposal_id)
                return {"available": True, "action": action, "applied": True,
                        "flow_id": accepted.flow_id, "change_id": accepted.change_id}
            rejected = studio.reject(proposal_id)
            return {"available": True, "action": action, "rejected": rejected.rejected,
                    "reason": rejected.reason}
        except StudioHandoffError as exc:
            raise ValueError(str(exc)) from exc
        except (DraftSessionError, DraftAcceptError) as exc:
            raise ValueError(str(exc)) from exc

    # ── Studio 画布（[`T-UI-005.1`] / story-06 主画布） ─────────────────────

    def studio_sessions(self) -> dict[str, Any]:
        """仍在编辑中的 Studio 会话一览（画布页列「已交 Studio 未处置」的那些）。"""
        studio = self._l6.studio
        if studio is None or not studio.available:
            return {"available": False, "reason": "未接入 Studio 草稿接收面（08 §4）"}
        sessions: list[dict[str, Any]] = []
        for proposal_id in studio.opened():
            session = studio.session(proposal_id)
            if session is None:                     # 理论上不可达；真发生则不臆造一行
                continue
            sessions.append(self._canvas_session(proposal_id, session))
        return {"available": True, "sessions": sessions}

    def studio_canvas(self, proposal_id: str) -> dict[str, Any]:
        """某会话的画布视图（节点 / 连线 / 分组 / 校验违规）——画布页的取数面。

        未交 Studio（无会话）⇒ ``ValueError``（输入类失败，表现层回 `validation_failed`）；
        子面未接线 ⇒ ``{"available": False, …}``（表现层回 `unavailable` + 点名）。
        """
        studio = self._l6.studio
        if studio is None or not studio.available:
            return {"available": False, "reason": "未接入 Studio 草稿接收面（08 §4）"}
        if not isinstance(proposal_id, str) or not proposal_id:
            raise ValueError("提案标识（proposal_id）必填——无法寻址的提案一律拒收")
        session = studio.session(proposal_id)
        if session is None:
            raise ValueError(f"该提案尚未交 Studio，无可查看的画布：{proposal_id!r}（08 §4）")
        return {"available": True, "canvas": self._canvas_payload(proposal_id, session)}

    def studio_skills(self) -> dict[str, Any]:
        """画布页「加节点」的可选 Skill 清单（01 §2 描述体的投影——**唯一来源**是注册表）。

        取 `SkillRegistry.tool_catalog()` 的**能力条目**（同 base 折叠、取最高版本），
        只回名称 / 执行目标 / 描述——前端据它填下拉，不自己拼 `skill_id`。
        """
        if self._skills is None:
            return {"available": False, "reason": "未接入 Skill 注册表（装配归组合根）"}
        return {
            "available": True,
            "skills": [
                {"name": entry.name, "skill_id": entry.skill_id,
                 "description": entry.description}
                for entry in self._skills.tool_catalog()
            ],
        }

    def studio_edit(self, *, proposal_id: str, op: str, args: Mapping[str, Any] | None = None
                    ) -> dict[str, Any]:
        """把一次**画布结构编辑**转交该会话的 `CanvasEditor`（同一个句柄，不分裂）。

        `op` ∈ [`_CANVAS_EDIT_OPS`]；`CanvasEditor` 是**全函数**（用户级冲突一律
        `applied=False`，不抛异常），故本面把 `EditResult` 归一成载荷并**附编辑后的画布**——
        表现层一次请求即可重渲染。缺键 / 非法 `op` / 无可编辑会话 ⇒ ``ValueError``。
        """
        studio = self._l6.studio
        if studio is None or not studio.available:
            return {"available": False, "reason": "未接入 Studio 草稿接收面（08 §4）"}
        if not isinstance(proposal_id, str) or not proposal_id:
            raise ValueError("提案标识（proposal_id）必填——无法寻址的提案一律拒收")
        if op not in _CANVAS_EDIT_OPS:
            raise ValueError(
                f"画布编辑操作须为 {'/'.join(_CANVAS_EDIT_OPS)}，收到 {op!r}（08 §4）")
        params = args if args is not None else {}
        if not isinstance(params, Mapping):
            raise ValueError("画布编辑参数须为对象（{参数名: 值}）")
        session = studio.session(proposal_id)
        if session is None:
            raise ValueError(f"该提案尚未交 Studio，无可编辑的画布：{proposal_id!r}（08 §4）")
        try:
            editor = studio.tune(proposal_id)       # 同一个 CanvasEditor 句柄
        except (StudioHandoffError, DraftSessionError) as exc:
            raise ValueError(str(exc)) from exc
        result = _apply_canvas_edit(editor, op, params)
        edit = {
            "action": result.action,
            "applied": result.applied,
            "message": result.message,
            "blocked_by": [str(issue) for issue in result.blocked_by],
        }
        return {
            "available": True,
            "canvas": self._canvas_payload(proposal_id, session, edit=edit),
        }

    def _canvas_session(self, proposal_id: str, session: Any) -> dict[str, Any]:
        """会话小结（一览用）——身份与规模，不含节点明细。"""
        dag = session.dag
        return {
            "proposal_id": proposal_id,
            "base": session.base,
            "flow_id": dag.flow_id,
            "node_count": len(dag.nodes),
            "status": session.status,
        }

    def _canvas_payload(self, proposal_id: str, session: Any, *, edit: dict | None = None
                        ) -> dict[str, Any]:
        """画布 JSON 就绪结构（`studio_canvas` 的 `canvas` 槽）。

        **校验结论取当前态**（`editor.validate_current()`）而非落画布时的首轮——编辑后
        违规清单会随之变化，画布页要看到的是**现在**这一版（未编辑时两者逐字相同）。
        """
        dag = session.dag
        validation = session.editor.validate_current()
        catalog = self.studio_skills()
        canvas: dict[str, Any] = {
            "session": self._canvas_session(proposal_id, session),
            "nodes": [
                {
                    "node_id": node.node_id,
                    "skill_id": node.skill_id,
                    "params": {name: binding.model_dump(mode="json")
                               for name, binding in node.params.items()},
                }
                for node in dag.nodes
            ],
            "edges": [
                {"edge_id": edge.edge_id, "from_node": edge.from_node,
                 "to_node": edge.to_node}
                for edge in dag.edges
            ],
            "groups": [
                {"group_id": group.group_id, "name": group.name,
                 "node_ids": list(group.node_ids)}
                for group in dag.groups
            ],
            "schedule": dag.schedule.model_dump(mode="json"),
            "violations": [str(issue) for issue in validation.issues],
            "validation_ok": validation.ok,
            "skill_options": list(catalog.get("skills") or ()) if catalog.get("available") else [],
        }
        if edit is not None:
            canvas["edit"] = edit
        return canvas

    def pending(self) -> list[dict[str, Any]]:
        """待批准 / 已延后的提案（**留痕即事实**，处置过的也在）。"""
        return [item.model_dump(mode="json") for item in self._l6.change_flow.pending()]

    def decide(
        self,
        *,
        action: str,
        proposal: Mapping[str, Any] | None = None,
        pending_id: str = "",
    ) -> dict[str, Any]:
        """逐条处置（接受 / 否决 / 延后）——**唯一通道是变更流**（08 §5 / §7 红线）。

        接受：立案件**经 01 §7 门面落值生效**并产生 `change_id`；否决：留痕而值逐字节不变；
        延后：排队而非丢弃（可再处置）。三者的落点都在 L6，本面只把结论归一成一份结构。
        """
        if action not in _DECISION_ACTIONS:
            raise ValueError(f"处置动作须为 {'/'.join(_DECISION_ACTIONS)}，收到 {action!r}")
        if not pending_id and proposal is None:
            raise ValueError("需给出待批准项 id 或提案内容")
        if pending_id:
            return self._decide_pending(action, pending_id)
        flow = self._l6.change_flow
        candidate = _change_proposal(proposal or {})
        if action == "accept":
            run = flow.submit(candidate)
            if run.pending is not None:
                return self._decide_pending("accept", run.pending.pending_id)
            return _run_payload(run, action)
        # 否决 / 延后只对**已立案**的项有意义：先问审批门，未立案就别去 submit——
        # 自主档下 submit 会直接落值生效，那样「否决」反而改了值（08 §5 的档位语义）。
        if not flow.gate(candidate).filed:
            return _run_payload(None, action, note=_NOT_FILED_NOTE)
        run = flow.submit(candidate)
        if run.pending is None:
            return _run_payload(run, action)
        return self._decide_pending(action, run.pending.pending_id)

    def _decide_pending(self, action: str, pending_id: str) -> dict[str, Any]:
        flow = self._l6.change_flow
        pending = flow.get_pending(pending_id)
        if pending is None:
            raise ValueError(f"待批准项不存在：{pending_id!r}")
        if pending.status in ("accepted", "rejected"):
            # 重复处置是**输入类**失败（用户点重了），不是内部错——就地转 `ValueError`，
            # 与 L6 自己的「显式失败而非吞错」一致（08 §5）。
            raise ValueError(f"该提案已处置过（{pending.status}），不可重复处置")
        if action == "accept":
            return _run_payload(flow.accept(pending_id), action)
        return _run_payload(
            None, action,
            pending=flow.reject(pending_id) if action == "reject" else flow.defer(pending_id),
        )

    # ── A/B 实验 ────────────────────────────────────────────────────────────
    def experiments(self) -> list[dict[str, Any]]:
        """全部实验（五字段 + 状态 + 决策；判定**保守**，不产 p 值）。"""
        return [item.model_dump(mode="json") for item in self._l6.experiments.all()]

    # ── 训练对话 ────────────────────────────────────────────────────────────
    def trainings(self) -> list[dict[str, Any]]:
        """训练会话留痕（含「已确认 / 未确认」）。"""
        return [item.model_dump(mode="json") for item in self._l6.training.all()]

    def callbacks(self) -> list[dict[str, Any]]:
        """待回访项（含已提及的）——「下次对话主动提及」的取材面。"""
        return [
            item.model_dump(mode="json")
            for item in self._l6.training.callbacks(pending_only=False)
        ]

    # ── 变更历史 ────────────────────────────────────────────────────────────
    def changes(self) -> list[dict[str, Any]]:
        """演进变更时间线（含回滚记录与已回滚标注）。"""
        flow = self._l6.change_flow
        if flow is None:
            return []
        return [item.model_dump(mode="json") for item in flow.history()]

    def rollback(self, change_id: str) -> dict[str, Any]:
        """按该变更的**生效前取值**回放（本身也是一次变更，产生新 `change_id`）。"""
        flow = self._l6.change_flow
        if flow is None:
            raise ValueError(NO_REGISTRY_REASON)
        if not change_id:
            raise ValueError("需给出要回滚的 change_id")
        if flow.get(change_id) is None:
            raise ValueError(f"变更不存在：{change_id!r}")
        return _run_payload(flow.rollback(change_id), "rollback")

    # ── 演进授权 ────────────────────────────────────────────────────────────
    def authorization(self) -> dict[str, Any]:
        """档位 + 风险分级清单 + 三档取值（表现层据此渲染设置页）。"""
        auth = self._l6.authorization
        if auth is None:
            return {"available": False, "reason": NO_AUTHORIZATION_REASON}
        return {
            "available": True,
            "tier": auth.tier(),
            "tiers": list(TIERS),
            "grading": [rule.model_dump(mode="json") for rule in auth.grading()],
            "risk_classes": list(RISK_CLASSES),
            "default_tier": DEFAULT_TIER,
            "tier_config_id": TIER_CONFIG_ID,
            "grading_config_id": GRADING_CONFIG_ID,
        }

    def set_authorization(
        self, *, tier: Any = None, grading: Any = None
    ) -> dict[str, Any]:
        """切换档位 / 替换风险分级清单——**经统一标量落值面生效并留痕**（08 §5）。"""
        auth = self._l6.authorization
        if auth is None:
            return {"available": False, "reason": NO_AUTHORIZATION_REASON}
        if tier is not None:
            if tier not in TIERS:
                raise ValueError(f"档位须为 {'/'.join(TIERS)}，收到 {tier!r}")
            record = auth.set_tier(tier)
            return {"available": True, "tier": auth.tier(), "changed": record is not None}
        if grading is not None:
            rules = _grading_rules(grading)
            if not rules:
                raise ValueError("风险分级清单不得为空（08 §5）")
            record = auth.set_grading(rules)
            return {
                "available": True,
                "grading": [rule.model_dump(mode="json") for rule in auth.grading()],
                "changed": record is not None,
            }
        raise ValueError("需给出 tier 或 grading")

    # ── 出厂重置 ────────────────────────────────────────────────────────────
    def factory_reset(self, confirmations: Any) -> dict[str, Any]:
        """「回滚到出厂设置」——**三次确认**是硬门：不足即**就地拒**，一步都不做。"""
        reset = self._l6.factory_reset
        if reset is None:
            return {"available": False, "reason": _NO_FACTORY_RESET_REASON}
        if isinstance(confirmations, bool) or not isinstance(confirmations, int):
            raise ValueError("确认次数须为整数")
        if confirmations != RESET_CONFIRMATIONS:
            raise ValueError(
                f"出厂重置需 {RESET_CONFIRMATIONS} 次确认，收到 {confirmations}"
                "——不足即拒、不留痕、不执行任何动作（08 §6）"
            )
        return reset.request(confirmations).model_dump(mode="json")


def _apply_canvas_edit(editor: Any, op: str, args: Mapping[str, Any]) -> Any:
    """把一次画布编辑转交 `CanvasEditor`（[`T-UI-005.1`]）。

    只做**参数解包与必填校验**——判定件（成环 / 契约 / 标识形态 / 连带删除）一律在
    `CanvasEditor` 里，**不在此复制**（[03 §4] 编辑面落地口径）。缺必填参数 ⇒ ``ValueError``
    （输入类失败，表现层回 `validation_failed`）。
    """
    def need(key: str) -> str:
        value = args.get(key)
        if value is None or (isinstance(value, str) and not value.strip()):
            raise ValueError(f"画布编辑 {op} 缺少必填参数 {key!r}")
        return str(value)

    if op == "add_node":
        return editor.add_node(need("node_id"), need("skill_id"))
    if op == "remove_node":
        return editor.remove_node(need("node_id"))
    if op == "connect":
        return editor.connect(need("edge_id"), need("from_node"), need("to_node"))
    if op == "disconnect":
        return editor.disconnect(need("edge_id"))
    if op == "group":
        raw = args.get("node_ids") or ()
        if not isinstance(raw, (list, tuple)):
            raise ValueError("画布编辑 group 的 node_ids 须为数组")
        return editor.group(need("group_id"), need("name"), tuple(str(n) for n in raw))
    return editor.ungroup(need("group_id"))          # op 已在 facade 侧校验为 ungroup


def _run_payload(
    run: ChangeRun | None,
    action: str,
    *,
    pending: PendingChange | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    """把变更流的一次动作归一成一份结构（提交 / 接受 / 否决 / 延后 / 回滚共用）。"""
    item = pending if pending is not None else (run.pending if run is not None else None)
    return {
        "action": action,
        "applied": bool(run.applied) if run is not None else False,
        "decision": run.decision.model_dump(mode="json") if run is not None else None,
        "change": run.change.model_dump(mode="json") if run is not None and run.change else None,
        "pending": item.model_dump(mode="json") if item is not None else None,
        "note": note if note is not None else (run.note if run is not None else ""),
    }
