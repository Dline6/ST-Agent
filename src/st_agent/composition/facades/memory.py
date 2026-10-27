"""记忆与图谱门面：持仓读面、三视图、节点详情与修正历史。"""

from __future__ import annotations

from collections.abc import Mapping
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
from st_agent.l2.memory.models import (
    MEMORY_NODE_ADAPTER,
    NODE_TYPES,
    PRIVACY_LEVELS,
    SOURCES,
    AttentionNode,
)


POSITION_SCOPES: tuple[str, ...] = ("holdings", "watchlist")
"""持仓 / 关注两段的**面键**（与 [`AttentionNode`](../src/st_agent/l2/memory/models.py) 的同名字段
逐名对应，[04 §3.1]）。表现层按它出两条读面，描述**不接受** URL / 方法（[01 §12] 的动作不进描述）。"""


@dataclass(frozen=True)
class MemoryPositionsFacade:
    """记忆区的**持仓 / 关注读面**（组合根所有；`ui` 只经鸭子端口消费，[`T-UI-009.2`]）。

    两件事都在组合根做完，故表现层不必（也不得）import L2 / L0 的类型：

    - **取数**——持仓与关注取自记忆的 `attention` 节点（[04 §3.1]），行情取自本地市场库的
      最新日线视图（`v_k_line_latest` 的收盘 / 涨跌幅 / 换手率 + `security` 的名称）。
      **只读、不触网**：`market` 是鸭子类型（`MarketDb.query`），与 L1 执行器同一取数面。
    - **据实报缺口**——行情源未注入、或本地行情库查询非 `ok`，都**不**退化成「只有代码、
      没有行情」的半表（那会让人以为行情本身就是空的），而是 `available=False` + 写明原因，
      由表现层回 `unavailable`。某标的在缓存里**没有**日线则只该行的行情列缺席（`None`），
      由渲染面出「无数据」——那是真实情况，不冒充 0，也不读作「平」。

    一处**优先级**：本段在记忆里**没有**标的时回空行集（表现为 `empty`），**先于**行情缺口的
    判定——对着一个空持仓说「行情源不可用」是噪声，不是信息。
    """

    reader: MemoryReader
    market: Any = None

    _QUOTES_SQL = (
        "SELECT k.code AS code, k.close AS close, k.pct_chg AS pct_chg, k.turn AS turn,"
        " (SELECT s.code_name FROM security s WHERE s.code = k.code) AS code_name"
        " FROM v_k_line_latest k WHERE k.code IN ({placeholders})"
    )

    def positions(self, scope: str) -> dict[str, Any]:
        """一段（``holdings`` / ``watchlist``）的行集；`available=False` 时 `reason` 点名缺口。"""
        if scope not in POSITION_SCOPES:
            raise ValueError(f"不认识的面：{scope!r}（只认 {POSITION_SCOPES}）")
        codes = self.codes(scope)
        if not codes:
            return {"available": True, "reason": "", "rows": []}
        if self.market is None:
            return {
                "available": False,
                "reason": "行情源未注入：本次装配未带本地行情库（装配归生产入口）",
                "rows": [],
            }
        envelope = self.market.query(
            self._QUOTES_SQL.format(placeholders=", ".join("?" for _ in codes)), codes
        )
        if getattr(envelope, "status", None) != "ok":
            reason = getattr(envelope, "reason", "") or "本地行情库未给出原因"
            return {"available": False, "reason": f"本地行情库暂不可用：{reason}", "rows": []}
        quotes = {row["code"]: row for row in (envelope.data or {}).get("rows") or []}
        return {
            "available": True,
            "reason": "",
            "rows": [
                {
                    "code": code,
                    "code_name": quotes.get(code, {}).get("code_name"),
                    "close": quotes.get(code, {}).get("close"),
                    "pct_chg": quotes.get(code, {}).get("pct_chg"),
                    "turn": quotes.get(code, {}).get("turn"),
                }
                for code in codes
            ],
        }

    def codes(self, scope: str) -> tuple[str, ...]:
        """记忆里的证券代码——去重**保序**（同一标的在一次读面里只占一行）。"""
        result = self.reader.query(
            SliceQuery(task_type="chat", topic="", token_budget=None, view="list")
        )
        found: list[str] = []
        for slice_ in result.slices:
            node = slice_.node
            if isinstance(node, AttentionNode):
                found.extend(getattr(node, scope, ()) or ())
        return tuple(dict.fromkeys(found))


# ── 记忆区图谱面的文案表（生成的系统文案；`data` 槽不取此表） ────────────────────
#
# 节点类型名 / 关系名 / 来源名 / 隐私分级名是**系统文案**（[D-064] 的生成文案槽），
# 前端**不自带词表**——否则那批文案会绕过回环边界的中性校验门（[01 §12]）。用户原话
# （thesis.view / history.event …）一律作 `data` 槽原样呈现（[D-053]）。
_NODE_TYPE_LABELS: dict[str, str] = {
    "identity": "身份画像",
    "attention": "关注对象",
    "thesis": "观点",
    "history": "历史事件",
    "pattern": "行为模式",
    "evolution": "偏好演化",
}


_NODE_SOURCE_LABELS: dict[str, str] = {
    "user_stated": "用户明确表达",
    "inferred": "副驾推断",
}


_NODE_PRIVACY_LABELS: dict[str, str] = {
    "public": "公开",
    "private": "私有",
    "sensitive": "敏感",
}


_EDGE_TYPE_LABELS: dict[str, str] = {
    "related_to": "一般关联",
    "evolves_from": "演化自",
    "derived_from": "派生自",
    "refers_to": "引用",
}


_PROFILE_TAG_LIMIT = 5
"""画像卡「当前画像」摘要的标签上限（[11-sitemap §2 记忆]：不超过 5 条关键标签）。"""


def _checked_node(node: Any) -> Any:
    """经 :data:`MEMORY_NODE_ADAPTER` 解析节点（六类判别联合的单一入口，不手写分支）。"""
    return MEMORY_NODE_ADAPTER.validate_python(node)


def _node_label(node: Any) -> str:
    """节点的一句话标签（取该类型**首个非空专属值**；可能含用户原话，故作 `data` 槽）。"""
    try:
        checked = _checked_node(node)
    except Exception:  # noqa: BLE001 —— 解析失败即退回类型名，不因一个坏节点炸整页
        return ""
    for field in ("view", "event", "pattern", "dimension", "risk_preference", "subject"):
        value = getattr(checked, field, None)
        if isinstance(value, str) and value.strip():
            return value
    for field in ("sector_preferences", "theme_interests", "holdings", "watchlist"):
        value = getattr(checked, field, ()) or ()
        if value:
            return "、".join(str(item) for item in value)
    return ""


def _node_own_fields(node: Any) -> dict[str, Any]:
    """该节点的**类型专属字段**（去掉 §1 通用字段），值为 JSON 可序列化形态。

    标签（`label`）本就在专属字段里，故此处**不再另加**，免得渲染面重复一行。
    """
    base = {
        "type", "memory_node_id", "confidence", "source", "provenance",
        "privacy_level", "revision_history", "created_at", "updated_at",
    }
    dump = node.model_dump(mode="json")
    return {key: value for key, value in dump.items() if key not in base}


def _edge_entry(edge: Any) -> dict[str, Any]:
    """一条边的描述项（起止节点 id + 关系类型；关系名由渲染面经 `labels` 出）。"""
    return {
        "from": edge.source_id,
        "to": edge.target_id,
        "relation": edge.edge_type,
    }


def _slice_entry(sl_ice: Any) -> dict[str, Any]:
    """一条记忆切片 → 三视图共用的一行（`data` 槽，记忆本体原样）。"""
    node = sl_ice.node
    return {
        "id": node.memory_node_id,
        "type": node.type,
        "label": _node_label(node),
        "confidence": node.confidence,
        "effective_confidence": sl_ice.confidence,
        "source": node.source,
        "privacy": node.privacy_level,
        "as_of": sl_ice.as_of.isoformat(),
        "updated_at": node.updated_at.isoformat(),
        "edges": [_edge_entry(edge) for edge in sl_ice.edges],
    }


@dataclass(frozen=True)
class GraphFacade:
    """L2 记忆图谱面：持图谱与读写口（图谱 / 列表 / 时间线三视图共享同一查询层）。

    **一视同仁地取数，不替表现层定形态**——三视图走**同一条**
    :meth:`MemoryReader.query`（[04 §3.1](../../docs/技术架构-v2/04-L2-记忆图谱.md)：
    `view` 只换排序、不换候选集），故「三视图共享查询层」是结构上成立的，不靠表现层自觉。
    用户原话（`label`）是 `data`、系统文案是一张**生成文案表**（渲染面经 `labels` 出），
    两者在描述层分流（[D-053] / [D-064]）。
    """

    graph: Any
    reader: Any = None
    writer: Any = None
    sharing: Any = None
    """片段分享面（[04 §8]；导出清单 + 确认门）——归 [`T-UI-014.3`]。"""
    onboarding: Any = None
    """Onboarding 协议面（[04 §7]）——归 [`T-UI-014.3`]。"""

    def view_labels(self) -> dict[str, Any]:
        """三视图共用的生成文案表（节点类型 / 来源 / 隐私分级 / 关系名）。"""
        return {
            "types": dict(_NODE_TYPE_LABELS),
            "sources": dict(_NODE_SOURCE_LABELS),
            "privacy": dict(_NODE_PRIVACY_LABELS),
            "relations": dict(_EDGE_TYPE_LABELS),
        }

    def browse(self, *, view: str, topic: str = "", task_type: str = "chat") -> dict[str, Any]:
        """三视图查询（`graph` / `list` / `timeline` 走**同一条**查询层）。

        「三视图共享同一查询层」的判据是**同候选集、只换排序**（[04 §3.1]）——故本方法
        只把 `view` 透给 `SliceQuery`，不在本层另起筛选。token 预算给 `None`（浏览视图
        不截断，[`SliceQuery`](../../src/st_agent/l2/memory/reader.py) 的口径）。
        """
        if view not in ("graph", "list", "timeline"):
            raise ValueError(f"不认识的三视图：{view!r}（须为 graph / list / timeline）")
        result = self.reader.query(
            SliceQuery(task_type=task_type, topic=topic, token_budget=None, view=view)
        )
        graph_text = self.graph
        payload: dict[str, Any] = {
            "view": view,
            "slices": [_slice_entry(sl_ice) for sl_ice in result.slices],
            "total": len(result.slices),
            "labels": self.view_labels(),
        }
        if view == "graph":
            # 图谱视图另出**去重后的边表**（同一条边被两端的切片各带一次）。
            seen: dict[tuple[str, str, str], dict[str, Any]] = {}
            for sl_ice in result.slices:
                for edge in sl_ice.edges:
                    key = (edge.source_id, edge.target_id, edge.edge_type)
                    seen.setdefault(key, _edge_entry(edge))
            payload["edges"] = list(seen.values())
        if not result.slices:
            # 查询层把「无候选 / 预算内无切片」当 `empty` 出（§3.1 的语义）——表现层据实
            # 转 `empty`，免得空记忆渲染成一张空表而不给引导（[01 §5] 六态不可混用）。
            payload["empty"] = True
        return payload

    def profile(self) -> dict[str, Any]:
        """偏好画像卡：`identity` / `evolution` 节点摘要，**不超过 5 条**标签。

        超出即显式收窄（回 `truncated` + 总条数），**不静默丢弃**——收窄是「摘要」的
        应有之义，但用户有权知道还有多少条没显示（[11-sitemap §2 记忆]）。
        """
        nodes = [
            node
            for node in self.graph.nodes()
            if node.type in ("identity", "evolution")
        ]
        nodes.sort(key=lambda node: node.updated_at, reverse=True)
        tags: list[dict[str, Any]] = []
        for node in nodes:
            label = _node_label(node)
            if not label:
                continue
            tags.append({"label": label, "kind": node.type, "source": node.memory_node_id})
        shown = tags[:_PROFILE_TAG_LIMIT]
        return {
            "tags": shown,
            "total": len(tags),
            "limit": _PROFILE_TAG_LIMIT,
            "truncated": len(tags) > _PROFILE_TAG_LIMIT,
            "labels": self.view_labels(),
        }

    def node(self, *, node_id: str) -> dict[str, Any] | None:
        """单节点详情（含相接边）；节点不存在返回 ``None``（表现层据实回 `unavailable`）。"""
        if not self.graph.has_node(node_id):
            return None
        node = self.graph.get_node(node_id)
        return {
            "node": node,
            "node_id": node.memory_node_id,
            "type": node.type,
            "label": _node_label(node),
            "source": node.source,
            "privacy": node.privacy_level,
            "created_at": node.created_at.isoformat(),
            "updated_at": node.updated_at.isoformat(),
            # **按 id 取单节点**不经切片查询，故这里的 `confidence` 是**存储值**（写入时刻的
            # 记录），不是 §5 的有效值——`effective=False` 使渲染面据实标注（[04 §5]）。
            "confidence": node.confidence,
            "effective": False,
            "fields": _node_own_fields(node),
            "edges": [
                {"edge": _edge_entry(edge), "other": _other_end(edge, node_id)}
                for edge in self.graph.edges_of(node_id)
            ],
            "labels": self.view_labels(),
        }

    def revisions(self, *, node_id: str) -> dict[str, Any] | None:
        """某节点的修正历史（**旧值保留供审计**，[04 §1] / §4）；节点不存在返回 ``None``。

        当前节点与历史**同出**，使渲染面能把「新值」与「旧值链」并置（[11-sitemap §2 记忆]：
        新值不覆盖历史）。条目按 `replaced_at` **降序**（最新修正在上，[D-111] A2）。
        """
        if not self.graph.has_node(node_id):
            return None
        node = self.graph.get_node(node_id)
        history = sorted(node.revision_history, key=lambda entry: entry.replaced_at, reverse=True)
        return {
            "node_id": node_id,
            "label": _node_label(node),
            "current": node.model_dump(mode="json"),
            "revisions": [
                {
                    "replaced_at": entry.replaced_at.isoformat(),
                    "reason": entry.reason or "",
                    "previous": entry.previous,
                }
                for entry in history
            ],
            "labels": self.view_labels(),
        }

    # ── 导入导出面板（[T-UI-014.3]；[04 §8] 的强制三步） ──────────────────────

    def export_plan(self) -> dict[str, Any]:
        """导出**计划**：过滤 `private` / `sensitive` → 出「本次包含哪些信息」清单（[04 §8]）。

        强制三步的**第一步与第二步同一次算齐**（`MemoryShare.plan` 的口径），故清单与载荷
        不会在两次读图之间分叉。清单**不含节点原文**（只报条数 + 中性措辞），故是生成文案。
        """
        plan = self.sharing.plan()
        return {
            "included": list(plan.included_summary),
            "excluded_nodes": plan.excluded_nodes,
            "node_count": len(plan.payload.nodes),
        }

    def export_memory(self, *, confirmed_by: str) -> dict[str, Any]:
        """导出**产出**：经确认门后落片段载荷（[04 §8] 第三步）。

        `confirmed_by` 只接受 ``user``（[`SHARE_CONFIRMATION`]）——**未经确认不导出**，
        且这里是**显式拒**而非静默成功（[11-sitemap §2 记忆] 的确认门）。
        """
        if confirmed_by != SHARE_CONFIRMATION:
            raise ValueError(
                "导出须经用户确认（confirmed_by 只接受 "
                f"{SHARE_CONFIRMATION!r}，得到 {confirmed_by!r}）"
            )
        payload = self.sharing.export(confirmed_by=confirmed_by)
        return {
            "node_ids": sorted(payload.node_ids()),
            "node_count": len(payload.nodes),
            "edge_count": len(payload.edges),
        }

    # ── Onboarding（[T-UI-014.3]；[04 §7]） ─────────────────────────────────

    def onboarding_state(self) -> dict[str, Any]:
        """Onboarding 状态：问题清单 + 空维度报告（供交互层决定是否出引导）。

        `has_profile` 判据＝**三个维度皆有值**（[D-111] A3）；问题清单取自
        `OnboardingProtocol.questions()`（官方 Pack 注入者优先，[D-111] A4）。
        """
        questions = self.onboarding.questions()
        empty = self.onboarding.empty_state()
        return {
            "questions": [
                {
                    "id": question.question_id,
                    "prompt": question.prompt,
                    "field": question.field,
                    "node_type": question.node_type,
                }
                for question in questions
            ],
            "empty_dimensions": sorted(empty.keys()),
            "has_profile": not empty,
        }

    def submit_onboarding(self, *, answers: Mapping[str, Any]) -> dict[str, Any]:
        """把 Onboarding 答案落成初始画像节点（[04 §7]：**至少三个维度**）。

        空白按**跳过**处理（`build_initial_nodes` 的口径）；全跳过时**不落任何节点**，
        由表现层据实报「未建立画像」（**不假装成功**，[11-sitemap §2 记忆]）。

        **`build_initial_nodes` 自己落盘**（经构造时注入的 `writer`）——故此处**不再**
        二次写入，只回报其产出（重复写会撞既有 id）。同类型多维度答案按 [04 §1] **合建
        一个节点**（「某维度无记忆」＝没有该字段，不是没有节点）。
        """
        nodes = self.onboarding.build_initial_nodes(answers)
        return {
            "created": [node.memory_node_id for node in nodes],
            "created_count": len(nodes),
        }


def _other_end(edge: Any, node_id: str) -> str:
    """边的另一端（详情页列相接边时用：从本节点看过去是谁）。"""
    return edge.target_id if edge.source_id == node_id else edge.source_id
