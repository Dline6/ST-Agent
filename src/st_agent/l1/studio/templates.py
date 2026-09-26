"""官方模板库（T-L1-003.5；03 §4 模板库与空状态）。

官方预置一批工作流模板（纯数据种子，随包发布），用户可**浏览 / 加载 / fork**：
fork 产出用户自有副本，模板本身逐字段不变。

落地口径（[03 §4] 模板库落地口径表）：

- **模板标识**：``template_id`` **不进** 01 §1 契约 ID 表——模板是随包内置的
  纯数据常量，只在 L1 进程内寻址（不持久化、不分享、不跨会话）；跨层只用
  fork 产物的 ``flow_id``
- **种子形态**：与 ``skills/pack.py::OFFICIAL_PACK`` 同构——字段 kwargs 式纯
  数据、**可增不可缺**、**不含身份字段** ``flow_id`` / ``version``（由接收方
  按用途派生）
- **装载校验**：种子在**装载期**即经 ``checked_dag`` + ``validate_dag`` 构造并
  校验（与注册表无关的那部分：§6 命名 / 标识重复 / 连线 / 成环 / 分组 /
  ``skill_id`` 形态）；种子有错即装载失败，不留到运行期静默
- **浏览面**：``list`` 逐条给 名称 / 说明 / 节点数 / 引用 Skill 清单 /
  **缺失 Skill 清单**——依赖提示走 §3.2 的 ``missing_dependencies``，
  与复合 Skill 依赖缺失**同一判定件**，不另造
- **预览面**：``load`` 返回模板的只读预览，身份取临时位 ``wf_<注册名>_v0.0``
  （``0.0`` 表未发布，与编辑面画布期临时身份同口径）
- **fork**：base 由种子的 ASCII ``flow_name`` 派生；与既有 base 冲突即**加
  序号**（``_2`` / ``_3`` …），**不拒**——模板名由系统派生、用户对 base 无
  选择权（与编辑面「接受」的重名即拒口径**有意不同**）
- **fork 落盘**：**不落盘**——fork 产物即「来自模板的草稿」，交
  :mod:`st_agent.l1.studio.canvas` 编辑、「接受」才是落盘点
  （:mod:`st_agent.l1.studio.draft`），故天然带 ``ChangeRecord`` 变更留痕

依赖缺失时**不静默通过**：``load`` / ``fork`` 抛 ``CompositeDependencyError``
（携逐条 ``DependencyGap``），与 §3.2「依赖缺失不落半成品」同一口径。
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from pydantic import BaseModel, ConfigDict

from st_agent.contracts.registry_types import SemVer
from st_agent.l1.studio.errors import TemplateError
from st_agent.l1.studio.ids import SLUG_PATTERN
from st_agent.l1.workflow.composite import (
    CompositeDependencyError,
    missing_dependencies,
)
from st_agent.l1.workflow.errors import WorkflowValidationError
from st_agent.l1.workflow.ids import base_of, flow_id_for, parse_flow_id
from st_agent.l1.workflow.models import WorkflowDAG, checked_dag
from st_agent.l1.workflow.store import WorkflowStore
from st_agent.l1.workflow.validate import validate_dag

__all__ = [
    "OFFICIAL_TEMPLATES",
    "TemplateLibrary",
    "TemplateSummary",
]

_PREVIEW_VERSION = SemVer(major=0, minor=0)
"""预览身份的版本位——``0.0`` 表「未发布」（与 ``studio.draft`` 同口径）。"""

_FIRST_VERSION = SemVer(major=1, minor=0)
"""fork 副本的首版版本位（落盘仍由「接受」承担，此处只定身份）。"""

_DAG_FIELDS: tuple[str, ...] = (
    "name", "description", "nodes", "edges", "groups", "schedule",
)
"""种子可携带的 ``WorkflowDAG`` 正文字段（身份字段 ``flow_id`` / ``version`` 除外）。"""


# ───────────────────────── 官方种子（可增不可缺） ─────────────────────────
#
# 形态与 ``skills/pack.py::OFFICIAL_PACK`` 同构：每项是构造 ``WorkflowDAG``
# 正文的 kwargs（不含 ``flow_id`` / ``version``）+ 两个模板自有字段：
#   - ``template_id``：进程内寻址键（03 §4：不进 01 §1 契约 ID 表）
#   - ``flow_name``：ASCII 注册名，供 ``flow_id`` 的 base 派生（展示名可含中文，
#     拼不出 flow_id；口径同 ``studio.ids.workflow_base``）

OFFICIAL_TEMPLATES: tuple[dict[str, Any], ...] = (
    dict(
        template_id="daily_st_brief",
        flow_name="daily_st_brief",
        name="每日 ST 简报",
        description="同步 ST 与 *ST 名单、扫描退市风险与板块热力，聚合为当日简报",
        nodes=(
            dict(node_id="list_sync", skill_id="sk_st_list_sync_v1.0"),
            dict(node_id="risk_scan", skill_id="sk_delisting_risk_scan_v1.0"),
            dict(node_id="sector_heat", skill_id="sk_sector_heatmap_v1.0"),
            dict(node_id="brief", skill_id="sk_data_aggregate_v1.0",
                 params={"format": {"kind": "literal", "value": "brief"}}),
        ),
        edges=(
            dict(edge_id="e_sync_scan", from_node="list_sync", to_node="risk_scan"),
            dict(edge_id="e_scan_brief", from_node="risk_scan", to_node="brief"),
            dict(edge_id="e_heat_brief", from_node="sector_heat", to_node="brief"),
        ),
        groups=(
            dict(group_id="recognition", name="认知",
                 node_ids=("list_sync", "risk_scan")),
        ),
        schedule={"mode": "cron", "cron": "0 8 * * *"},
    ),
    dict(
        template_id="delisting_risk_scan",
        flow_name="delisting_risk_scan",
        name="退市风险扫描",
        description="识别退市高危信号并联动摘帽条件评估，输出风险预警清单",
        nodes=(
            dict(node_id="list_sync", skill_id="sk_st_list_sync_v1.0"),
            dict(node_id="risk_scan", skill_id="sk_delisting_risk_scan_v1.0",
                 params={"threshold": {"kind": "literal", "value": 0.8}}),
            dict(node_id="unhat", skill_id="sk_unhat_eligibility_check_v1.0"),
            dict(node_id="alert", skill_id="sk_risk_alert_v1.0",
                 params={"lookahead_days": {"kind": "literal", "value": 30}}),
        ),
        edges=(
            dict(edge_id="e_sync_scan", from_node="list_sync", to_node="risk_scan"),
            dict(edge_id="e_scan_unhat", from_node="risk_scan", to_node="unhat"),
            dict(edge_id="e_unhat_alert", from_node="unhat", to_node="alert"),
        ),
        schedule={"mode": "manual"},
    ),
    dict(
        template_id="strategy_backtest_pipeline",
        flow_name="strategy_backtest_pipeline",
        name="策略回测流水线",
        description="基本面筛选与情绪资金流向分析产出候选池，设计策略回测并做组合压力测试",
        nodes=(
            dict(node_id="screen", skill_id="sk_fundamental_screening_v1.0",
                 params={"margin": {"kind": "literal", "value": 0.3}}),
            dict(node_id="sentiment", skill_id="sk_sentiment_flow_analysis_v1.0",
                 params={"window_days": {"kind": "literal", "value": 20}}),
            dict(node_id="design", skill_id="sk_strategy_design_v1.0",
                 params={"lookback_years": {"kind": "literal", "value": 3}}),
            dict(node_id="stress", skill_id="sk_portfolio_stress_test_v1.0",
                 params={"shock": {"kind": "literal", "value": 0.2}}),
        ),
        edges=(
            dict(edge_id="e_screen_design", from_node="screen", to_node="design"),
            dict(edge_id="e_sentiment_design", from_node="sentiment", to_node="design"),
            dict(edge_id="e_design_stress", from_node="design", to_node="stress"),
        ),
        schedule={"mode": "manual"},
    ),
)


class TemplateSummary(BaseModel):
    """模板库浏览面的一条（GWT-1 的名称 / 说明 / 节点数 / 引用 Skill 清单）。"""

    model_config = ConfigDict(frozen=True)

    template_id: str
    name: str
    description: str
    node_count: int
    skill_ids: tuple[str, ...]
    """模板引用的 Skill（按 nodes 顺序去重）。"""
    missing_skill_ids: tuple[str, ...] = ()
    """未注册（或该版本未注册）的 Skill（GWT-3 的「浏览」面提示；按 nodes 顺序去重）。"""


class _Template:
    """一条已装载并通过校验的模板（正文 + 预览身份）。"""

    __slots__ = ("body", "preview", "template_id", "flow_name", "base")

    def __init__(self, template_id: str, flow_name: str, body: dict[str, Any],
                 base: str) -> None:
        self.template_id = template_id
        self.flow_name = flow_name
        self.base = base
        self.body = body
        self.preview = _build_dag(body, f"{base}_v0.0")

    def dag(self, base: str, version: SemVer) -> WorkflowDAG:
        """按给定身份构造一份**独立**的工作流定义（正文逐字段取自模板）。"""
        return _build_dag(self.body, flow_id_for(base, version))


def _build_dag(body: dict[str, Any], flow_id: str) -> WorkflowDAG:
    """由模板正文 + 身份拼出一份 ``WorkflowDAG``（正文**深拷贝**，模板不被共享）。"""
    try:
        _, version = parse_flow_id(flow_id)
    except WorkflowValidationError as exc:
        raise TemplateError(f"模板派生出的身份非法：{exc}") from exc
    try:
        return checked_dag(flow_id=flow_id, version=version, **deepcopy(body))
    except WorkflowValidationError as exc:
        raise TemplateError(f"模板工作流非法（身份 {flow_id}）：{exc}") from exc


def _load_seeds(seeds: tuple[dict[str, Any], ...]) -> tuple[_Template, ...]:
    """装载全部种子（**装载期**即校验与注册表无关的那部分；有错即失败）。"""
    loaded: list[_Template] = []
    seen: set[str] = set()
    for seed in seeds:
        raw = dict(seed)
        template_id = raw.pop("template_id", None)
        flow_name = raw.pop("flow_name", None)
        if not isinstance(template_id, str) or not template_id:
            raise TemplateError(f"模板种子缺 template_id：{seed!r}")
        if template_id in seen:
            raise TemplateError(f"模板标识重复：{template_id!r}")
        seen.add(template_id)
        if not isinstance(flow_name, str) or not SLUG_PATTERN.match(flow_name):
            raise TemplateError(
                f"模板 {template_id!r} 的 flow_name {flow_name!r} 形态非法"
                "（须为小写字母数字起头、可含 _、≤61 字符）")
        unknown = set(raw) - set(_DAG_FIELDS)
        if unknown:
            raise TemplateError(
                f"模板 {template_id!r} 含无法识别的字段：{sorted(unknown)}")
        body = {k: raw[k] for k in _DAG_FIELDS if k in raw}
        tpl = _Template(template_id, flow_name, body, f"wf_{flow_name}")
        _verify(tpl)
        loaded.append(tpl)
    if not loaded:
        raise TemplateError("模板种子为空——官方模板库至少须含一条")
    return tuple(loaded)


def _verify(tpl: _Template) -> None:
    """种子装载期校验（与注册表无关的部分：§6 命名 / 标识 / 连线 / 成环 / 分组）。"""
    result = validate_dag(tpl.preview)
    if result.ok:
        return
    detail = "；".join(f"[{i.code}] {i.path}: {i.message}" for i in result.issues)
    raise TemplateError(f"模板 {tpl.template_id!r} 未过装载校验：{detail}")


class TemplateLibrary:
    """官方模板库门面（03 §4；无状态查询，不改种子、不写存储）。

    ``store`` 用于 fork 的 base 去重（读 ``config`` 分区既有工作流），
    ``registry`` 用于依赖提示（§3.2 ``missing_dependencies`` 同一判定件）。
    """

    def __init__(self, store, registry, *,
                 seeds: tuple[dict[str, Any], ...] = OFFICIAL_TEMPLATES) -> None:
        self._store = store
        self._registry = registry
        self._templates = _load_seeds(seeds)

    # ───────────────────────── 浏览 ─────────────────────────

    def list(self) -> tuple[TemplateSummary, ...]:
        """列出全部官方模板（按种子声明顺序；含缺失 Skill 清单供浏览面提示）。"""
        return tuple(self._summary(t) for t in self._templates)

    def get(self, template_id: str) -> TemplateSummary:
        """单条模板摘要（标识不存在 → ``TemplateError``）。"""
        return self._summary(self._require(template_id))

    # ───────────────────────── 加载 / fork ─────────────────────────

    def load(self, template_id: str) -> WorkflowDAG:
        """模板的**只读预览**（临时身份 ``wf_<注册名>_v0.0``，表未发布）。

        依赖缺失 → ``CompositeDependencyError``（逐条 ``DependencyGap``），不静默通过。
        """
        tpl = self._require(template_id)
        self._require_dependencies(tpl)
        return tpl.dag(tpl.base, _PREVIEW_VERSION)

    def fork(self, template_id: str) -> WorkflowDAG:
        """产**用户自有副本**：base 与既有工作流冲突即加序号（``_2`` / ``_3`` …）。

        本步**不落盘**——副本交编辑面编辑，「接受」（``studio.draft``）才是落盘点。
        依赖缺失 → ``CompositeDependencyError``，不静默通过。
        """
        tpl = self._require(template_id)
        self._require_dependencies(tpl)
        base = self._dedup_base(tpl.flow_name)
        return tpl.dag(base, _FIRST_VERSION)

    # ───────────────────────── 内部 ─────────────────────────

    def _require(self, template_id: str) -> _Template:
        for t in self._templates:
            if t.template_id == template_id:
                return t
        known = ", ".join(t.template_id for t in self._templates)
        raise TemplateError(f"模板不存在：{template_id!r}（现有：{known}）")

    def _summary(self, tpl: _Template) -> TemplateSummary:
        dag = tpl.preview
        skill_ids = _dedup(n.skill_id for n in dag.nodes)
        gaps = missing_dependencies(dag, self._registry)
        return TemplateSummary(
            template_id=tpl.template_id,
            name=dag.name,
            description=dag.description,
            node_count=len(dag.nodes),
            skill_ids=skill_ids,
            missing_skill_ids=_dedup(g.skill_id for g in gaps),
        )

    def _require_dependencies(self, tpl: _Template) -> None:
        gaps = missing_dependencies(tpl.preview, self._registry)
        if gaps:
            raise CompositeDependencyError(gaps)

    def _dedup_base(self, flow_name: str) -> str:
        """注册名去重：base 已存在即加序号（03 §4 模板库口径；**不拒**）。"""
        existing = {base_of(d.flow_id) for d in self._existing_flows()}
        candidate = flow_name
        ordinal = 2
        while f"wf_{candidate}" in existing:
            candidate = f"{flow_name}_{ordinal}"
            ordinal += 1
        if not SLUG_PATTERN.match(candidate):
            raise TemplateError(
                f"模板 {flow_name!r} 的注册名去重后 {candidate!r} 形态非法"
                "（超出长度上限）——请改用更短的 flow_name")
        return f"wf_{candidate}"

    def _existing_flows(self) -> tuple[WorkflowDAG, ...]:
        return WorkflowStore(self._store, self._registry).list_all()


def _dedup(values) -> tuple[str, ...]:
    """保序去重（浏览面的 Skill 清单按 nodes 顺序、同名只列一次）。"""
    out: list[str] = []
    for v in values:
        if v not in out:
            out.append(v)
    return tuple(out)
