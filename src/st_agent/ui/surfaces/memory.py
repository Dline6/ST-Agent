"""记忆区：持仓 / 关注、图谱三视图、详情与导入导出（[T-UI-009.2] / [T-UI-014]）。"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any
from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.contracts.ui_description import UiDescription, checked_description, new_description_id
from st_agent.ui.envelope import envelope_payload
from st_agent.ui.tables import Column, table_slots, table_text_kinds

from st_agent.ui.surfaces.base import (
    SurfaceBase,
    _TIMELINE_STATE_LABELS,
    _fmt_value,
    _iso_day,
    _now,
)


_POSITION_COLUMNS = (
    Column("code", "代码"),
    Column("code_name", "名称"),
    Column("close", "收盘", kind="number"),
    Column("pct_chg", "涨跌幅", kind="direction"),
    Column("turn", "换手率", kind="number"),
)
"""记忆区的**持仓 / 关注行情读面**（[`T-UI-009.2`]）。

`pct_chg` 是 `direction` 列——**三路冗余编码（颜色 + ▲▼ + 带符号数值）由渲染件一次产出**
（[13-visual-design §2.2]），本层只给**数值**；某标的在本地缓存里没有日线时给 `None`，
由渲染面出「无数据」，**不**冒充 0、**不**读作「平」。"""


_POSITION_SCOPES: dict[str, tuple[str, str]] = {
    "holdings": ("持仓", "记忆中没有持仓信息"),
    "watchlist": ("关注池", "记忆中没有关注池信息"),
}
"""两段的**面键** →（段名 · 空态原因）。原因措辞与 [05 §2] 上下文卡片的同段一致：
空是邀请（说清空的原因），不是留白。"""


_MEMORY_VIEW_LABELS: dict[str, str] = {
    "graph": "图谱",
    "list": "列表",
    "timeline": "时间线",
}
"""三视图的中文名（空态原因与标题用）。"""


_MEMORY_LIST_COLUMNS = (
    Column("label", "记忆"),
    Column("type", "类型"),
    Column("confidence", "置信度(存储)", kind="number"),
    Column("effective_confidence", "置信度(有效)", kind="number"),
    Column("updated_at", "更新时刻"),
    Column("source", "来源"),
)
"""记忆区**列表视图**的列（[11-sitemap §2 记忆]）。

置信度**有效值**与**存储值并置**（[04 §5]：衰减与重估算出、不回写盘）——两列同现使
「衰减了多少」可追溯，不合成一个「总分」。类型 / 来源两列的值由描述层按生成文案表解析
（前端不自带词表，[D-064]）；标签列是记忆本体（`data` 槽，原样呈现，[D-053]）。"""


def _confidence_bucket(value: Any) -> str:
    """置信度浮点 → 图谱视图的三档标签（`high` / `medium` / `low`）。

    分档只是 `graph_view` 的**呈现词汇**（渲染件样例即三档），不是第二套置信度语义——
    浮点原值与有效值在**列表视图**并置呈现（[_MEMORY_LIST_COLUMNS]），故「衰减了多少」
    仍可追溯。缺值给 `low`（渲染件回退 `低`），不冒充 `medium`。
    """
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "low"
    if number >= 0.7:
        return "high"
    if number >= 0.4:
        return "medium"
    return "low"


def _fmt_confidence(value: Any) -> str:
    """置信度 → 两小数浮点串（有效值与存储值同款呈现，不四舍五入成档）。"""
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return "—"


class MemorySurface(SurfaceBase):
    """记忆区：持仓 / 关注、图谱三视图、详情与导入导出（[T-UI-009.2] / [T-UI-014]）。"""

    def api_memory_positions(self, scope: str) -> dict[str, Any]:
        """记忆区的**持仓 / 关注行情读面**（`table`）——两条端点，一条一段。

        这是 [13-visual-design §2.2] 涨跌三路冗余编码与 `--font-num` 的**首个真实消费方**：
        描述只承载**数值**（涨跌幅给带符号百分数），编码由渲染件一次产出——故「颜色与符号
        恒同时出现」是结构上成立的，不靠本层自觉（[D-105] ②）。

        三种缺口**互不冒充**（[01 §5] 六态）：面未接线 ⇒ `unavailable` + 点名；记忆里这段为空
        ⇒ `empty` + 原因；行情源没注入 / 本地行情库非 `ok` ⇒ `unavailable` + 原因。某标的
        在缓存里没有日线**不是**缺口——那一行的行情列给 `None`，由渲染面出「无数据」。
        """
        if scope not in _POSITION_SCOPES:
            return envelope_payload(
                ResultEnvelope.validation_failed(f"不认识的面：{scope!r}")
            )
        return self._call_face(
            f"memory-{scope}", self.memory, "记忆面",
            lambda face: self._positions_envelope(face, scope),
        )

    def api_memory_browse(
        self, *, view: str, topic: str = "", task_type: str = "chat"
    ) -> dict[str, Any]:
        """三视图读面（`graph` / `list` / `timeline`）——三条走**同一条**查询层（[04 §3.1]）。

        视图名由**查询串**给（[11-sitemap §2.2]：带标识的面走查询串，路径集仍是有限
        字面量集合）。面未接线 ⇒ `unavailable` + 点名；候选集为空 ⇒ `empty` + 引导
        （**不静默留空**）；视图名不认识 ⇒ `validation_failed`。
        """
        return self._call_face(
            "memory-browse", self.graph, "记忆图谱面",
            lambda face: self._browse_envelope(face, view=view, topic=topic, task_type=task_type),
        )

    def _browse_envelope(
        self, face: Any, *, view: str, topic: str, task_type: str
    ) -> ResultEnvelope:
        if view not in ("graph", "list", "timeline"):
            return ResultEnvelope.validation_failed(
                f"不认识的三视图：{view!r}（须为 graph / list / timeline）"
            )
        payload = face.browse(view=view, topic=topic, task_type=task_type)
        if payload.get("empty"):
            return ResultEnvelope.empty(f"记忆里还没有{_MEMORY_VIEW_LABELS[view]}内容")
        return ResultEnvelope.ok(self._browse_description(payload))

    def api_memory_profile(self) -> dict[str, Any]:
        """偏好画像卡读面：`identity` / `evolution` 摘要，**不超过 5 条**标签（[11-sitemap §2 记忆]）。"""
        return self._call_face(
            "memory-profile", self.graph, "记忆图谱面",
            lambda face: self._profile_envelope(face),
        )

    def _profile_envelope(self, face: Any) -> ResultEnvelope:
        payload = face.profile()
        if not payload.get("tags"):
            return ResultEnvelope.empty("还没有可用的偏好画像（无 identity / evolution 节点）")
        return ResultEnvelope.ok(self._profile_description(payload))

    def _profile_description(self, payload: Mapping[str, Any]) -> UiDescription:
        """画像卡 → `report_card`（复用既有型；**不超过 5 条**关键标签，超出显式收窄）。"""
        labels = payload.get("labels") or {}
        types = labels.get("types") or {}
        tags = list(payload.get("tags") or [])
        lines = [
            f"{types.get(tag.get('kind'), tag.get('kind'))}：{tag.get('label')}"
            for tag in tags
        ]
        total = int(payload.get("total") or len(tags))
        limit = int(payload.get("limit") or len(tags))
        if payload.get("truncated"):
            hint = f"另有 {total - limit} 条未显示（画像卡上限 {limit} 条）"
        else:
            hint = f"共 {total} 条"
        return self._gated_description(
            "report_card",
            slots={"sections": [
                {"title": "当前画像", "lines": lines},
                {"title": "说明", "lines": [hint]},
            ]},
            text_kinds={"sections": "data"},
            title="偏好画像",
        )

    def api_memory_node(self, *, node_id: str) -> dict[str, Any]:
        """单节点详情读面（`?node=<id>`）——缺 id ⇒ `validation_failed`；无此节点 ⇒ `unavailable`。"""
        if not node_id:
            return envelope_payload(ResultEnvelope.validation_failed("缺少 node 参数（节点 id）"))
        return self._call_face(
            "memory-node", self.graph, "记忆图谱面",
            lambda face: self._node_envelope(face, node_id),
        )

    def _node_envelope(self, face: Any, node_id: str) -> ResultEnvelope:
        payload = face.node(node_id=node_id)
        if payload is None:
            return ResultEnvelope.unavailable(
                f"记忆里没有这个节点：{node_id}", last_updated_at=_now()
            )
        return ResultEnvelope.ok(self._node_description(payload))

    def _node_description(self, payload: Mapping[str, Any]) -> UiDescription:
        """节点详情 → `report_card`（复用既有型）。置信度按 [D-111] A1 标注为**存储值**。"""
        labels = payload.get("labels") or {}
        types = labels.get("types") or {}
        sources = labels.get("sources") or {}
        privacy = labels.get("privacy") or {}
        relations = labels.get("relations") or {}
        fields = payload.get("fields") or {}
        lines = [
            f"类型：{types.get(payload.get('type'), payload.get('type') or '—')}",
            f"标识：{payload.get('node_id') or '—'}",
            f"置信度（存储值·非有效值）：{_fmt_confidence(payload.get('confidence'))}",
            f"来源：{sources.get(payload.get('source'), payload.get('source') or '—')}",
            f"隐私分级：{privacy.get(payload.get('privacy'), payload.get('privacy') or '—')}",
            f"创建：{payload.get('created_at') or '—'}",
            f"更新：{payload.get('updated_at') or '—'}",
        ]
        field_lines = [f"{key}：{_fmt_value(value)}" for key, value in sorted(fields.items())]
        edges = payload.get("edges") or []
        edge_lines = [
            f"{relations.get(item['edge']['relation'], item['edge']['relation'])} → {item['other']}"
            for item in edges
        ]
        sections = [
            {"title": "基本信息", "lines": lines},
            {"title": "专属字段", "lines": field_lines or ["（无）"]},
            {"title": "相接关系", "lines": edge_lines or ["（无）"]},
        ]
        return self._gated_description(
            "report_card",
            slots={"sections": sections},
            text_kinds={"sections": "data"},
            title=str(payload.get("label") or "节点详情"),
        )

    def api_memory_node_revisions(self, *, node_id: str) -> dict[str, Any]:
        """修正历史读面（`?node=<id>&view=revisions`）——旧值保留供审计（[04 §1]）。"""
        if not node_id:
            return envelope_payload(ResultEnvelope.validation_failed("缺少 node 参数（节点 id）"))
        return self._call_face(
            "memory-node-revisions", self.graph, "记忆图谱面",
            lambda face: self._revisions_envelope(face, node_id),
        )

    def _revisions_envelope(self, face: Any, node_id: str) -> ResultEnvelope:
        payload = face.revisions(node_id=node_id)
        if payload is None:
            return ResultEnvelope.unavailable(
                f"记忆里没有这个节点：{node_id}", last_updated_at=_now()
            )
        revisions = payload.get("revisions") or []
        if not revisions:
            return ResultEnvelope.empty("该节点没有修正历史")
        return ResultEnvelope.ok(self._revisions_description(payload))

    def _revisions_description(self, payload: Mapping[str, Any]) -> UiDescription:
        """修正历史 → `timeline_view`（最新修正在上；旧值原样呈现、**不覆盖**）。"""
        entries = [
            {
                "id": f"{payload.get('node_id')}-r{index}",
                "at": _iso_day(item.get("replaced_at")),
                "kind": "correction",
                "content": item.get("reason") or "修正（未填缘由）",
                "state": "ok",
                "previous": _fmt_value(item.get("previous")),
            }
            for index, item in enumerate(payload.get("revisions") or [])
        ]
        return self._gated_description(
            "timeline_view",
            slots={
                "entries": entries,
                "labels": {
                    "kinds": {"correction": "修正"},
                    "states": dict(_TIMELINE_STATE_LABELS),
                },
                "empty_hint": "该节点没有修正历史",
            },
            text_kinds={"entries": "data", "labels": "generated", "empty_hint": "generated"},
            title=f"修正历史 · {payload.get('label') or payload.get('node_id')}",
        )

    def api_memory_export_plan(self) -> dict[str, Any]:
        """导出计划读面：出「本次导出包含的公开信息」清单（`report_card`；[04 §8] 第二步）。"""
        return self._call_face(
            "memory-export-plan", self.graph, "记忆图谱面",
            lambda face: self._memory_export_plan_envelope(face),
        )

    def _memory_export_plan_envelope(self, face: Any) -> ResultEnvelope:
        plan = face.export_plan()
        return ResultEnvelope.ok(self._memory_export_plan_description(plan))

    def _memory_export_plan_description(self, plan: Mapping[str, Any]) -> UiDescription:
        lines = [f"包含：{item}" for item in plan.get("included") or []]
        if not lines:
            lines = ["本次导出没有可公开的信息"]
        return self._gated_description(
            "report_card",
            slots={"sections": [
                {"title": "本次导出包含以下公开信息", "lines": lines},
                {
                    "title": "隐私过滤",
                    "lines": [f"已过滤（未包含）的节点数：{plan.get('excluded_nodes', 0)}"],
                },
                {"title": "确认门", "lines": ["确认后才会生成导出产物；未经确认不导出。"]},
            ]},
            text_kinds={"sections": "data"},
            title="导出记忆片段",
        )

    def api_memory_export(self, body: dict[str, Any]) -> dict[str, Any]:
        """导出**产出**（写面）：体 `{confirmed_by}`——非 `user` 即 `validation_failed`。"""
        return self._call_face(
            "memory-export", self.graph, "记忆图谱面",
            lambda face: face.export_memory(confirmed_by=str(body.get("confirmed_by") or "")),
        )

    def api_memory_onboarding(self) -> dict[str, Any]:
        """Onboarding 状态读面：问题清单 + 空维度（`setting_panel` 复用面以只读视图承载）。"""
        return self._call_face(
            "memory-onboarding", self.graph, "记忆图谱面",
            lambda face: self._onboarding_envelope(face),
        )

    def _onboarding_envelope(self, face: Any) -> ResultEnvelope:
        state = face.onboarding_state()
        return ResultEnvelope.ok(self._onboarding_description(state))

    def _onboarding_description(self, state: Mapping[str, Any]) -> UiDescription:
        lines = [
            f"{question['prompt']}（留空即跳过）"
            for question in state.get("questions") or []
        ]
        if state.get("has_profile"):
            lines = ["已有初始画像。"] + lines
        return self._gated_description(
            "report_card",
            slots={"sections": [
                {"title": "建立初始画像", "lines": lines or ["（无问题）"]},
                {
                    "title": "状态",
                    "lines": ["已有画像" if state.get("has_profile") else "尚未建立画像"],
                },
            ]},
            text_kinds={"sections": "data"},
            title="Onboarding 引导",
        )

    def api_memory_onboarding_submit(self, body: dict[str, Any]) -> dict[str, Any]:
        """Onboarding 提交（写面）：体 `{answers: {question_id: value}}`——落初始画像节点。"""
        answers = body.get("answers")
        if not isinstance(answers, Mapping):
            return envelope_payload(ResultEnvelope.validation_failed("answers 须为对象"))
        return self._call_face(
            "memory-onboarding-submit", self.graph, "记忆图谱面",
            lambda face: face.submit_onboarding(answers=answers),
        )

    def _browse_description(self, payload: Mapping[str, Any]) -> UiDescription:
        """三视图 → 描述：`graph_view` / 列表 `table` / `timeline_view`（复用既有三型）。

        **不新登记组件型**——三视图与画像卡都由 [01 §12] 已登记并实现的型承载，
        故本叶不触 [铁律 8]（[D-111] ③）。
        """
        view = str(payload.get("view") or "list")
        slices = list(payload.get("slices") or [])
        labels = payload.get("labels") or {}
        if view == "graph":
            return self._gated_description(
                "graph_view",
                slots={
                    "nodes": [
                        {
                            "id": item["id"],
                            "type": item["type"],
                            "label": item["label"],
                            "confidence": _confidence_bucket(item.get("confidence")),
                            "privacy": item.get("privacy"),
                        }
                        for item in slices
                    ],
                    "edges": list(payload.get("edges") or []),
                    "labels": {
                        "types": labels.get("types") or {},
                        "privacy": labels.get("privacy") or {},
                        "relations": labels.get("relations") or {},
                    },
                    "empty_hint": "记忆里还没有图谱内容",
                },
                text_kinds={
                    "nodes": "data",
                    "edges": "data",
                    "labels": "generated",
                    "empty_hint": "generated",
                },
                title="记忆图谱",
            )
        if view == "timeline":
            return self._gated_description(
                "timeline_view",
                slots={
                    "entries": [
                        {
                            "id": item["id"],
                            "at": _iso_day(item.get("updated_at") or item.get("as_of")),
                            "kind": item["type"],
                            "content": item["label"],
                            "state": "ok",
                            "confidence": _confidence_bucket(item.get("confidence")),
                            "source": item.get("source"),
                        }
                        for item in slices
                    ],
                    "labels": {
                        "kinds": labels.get("types") or {},
                        "states": dict(_TIMELINE_STATE_LABELS),
                        "sources": labels.get("sources") or {},
                    },
                    "empty_hint": "记忆里还没有时间线内容",
                },
                text_kinds={
                    "entries": "data",
                    "labels": "generated",
                    "empty_hint": "generated",
                },
                title="记忆时间线",
            )
        # 列表视图：复用 `table`（与持仓 / 关注两表同型），类型 / 来源在描述层按文案表解析
        types = labels.get("types") or {}
        sources = labels.get("sources") or {}
        rows = [
            {
                "id": item["id"],
                "label": item["label"],
                "type": types.get(item["type"], item["type"]),
                "confidence": item.get("confidence"),
                "effective_confidence": item.get("effective_confidence"),
                "updated_at": item.get("updated_at"),
                "source": sources.get(item.get("source"), item.get("source")),
            }
            for item in slices
        ]
        return self._gated_description(
            "table",
            slots=table_slots(_MEMORY_LIST_COLUMNS, rows),
            text_kinds=table_text_kinds(),
            title="记忆列表",
        )

    def _positions_envelope(self, face: Any, scope: str) -> ResultEnvelope:
        label, empty_reason = _POSITION_SCOPES[scope]
        view = face.positions(scope)
        if not view.get("available"):
            return ResultEnvelope.unavailable(
                str(view.get("reason") or "记忆面未接线"), last_updated_at=_now()
            )
        rows = view.get("rows") or []
        if not rows:
            return ResultEnvelope.empty(empty_reason)
        return ResultEnvelope.ok(
            self._gated_description(
                "table",
                slots=table_slots(_POSITION_COLUMNS, rows),
                text_kinds=table_text_kinds(),
                title=f"{label}行情",
            )
        )
