"""官方 Skill Pack 种子（T-L1-001.1；Story-02 In Scope 最小集，可增不可缺）。

公共认知 Bundle 6 个 + 主动服务 Bundle 6 个 = 12 个。种子为 ``register``
kwargs（不含 skill_id：版本拼接触发 A1 规则）；幂等写入由
``ensure_official_pack`` 保证（已存在即跳过，不静默覆盖）。
"""

from __future__ import annotations

__all__ = ["OFFICIAL_PACK", "ensure_official_pack"]

_OFFICIAL = "official"
_FOLLOW = "follow-latest"


def _param(name, type, default, description, **kw):
    spec = {"name": name, "type": type, "default": default, "description": description}
    spec.update(kw)
    return spec


OFFICIAL_PACK: tuple[dict, ...] = (
    # ── 公共认知 Bundle ──
    dict(base="sk_st_list_sync", name="ST名单同步",
         description="同步交易所最新 ST 与 *ST 名单，输出新增与移除对照",
         version="1.0",
         input_schema={"type": "object", "properties": {}},
         output_schema={"type": "object", "properties": {"diff": {"type": "object"}}},
         parameters=(_param("exchange", "enum", "all", "目标交易所范围",
                            choices=("all", "sh", "sz", "bj")),),
         dependencies=(), permissions=("net_access:<*.baostock.com>",),
         offline_level="degraded", version_policy=_FOLLOW, source=_OFFICIAL),
    dict(base="sk_delisting_risk_scan", name="退市风险扫描",
         description="识别退市高危信号，输出触发原因与证据引用",
         version="1.0",
         input_schema={"type": "object", "properties": {"stock_id": {"type": "string"}}},
         output_schema={"type": "object", "properties": {"risk_level": {"type": "string"}}},
         parameters=(_param("threshold", "number", 0.8, "触发阈值",
                            min_value=0.0, max_value=1.0),),
         dependencies=("sk_st_list_sync_v1.0",),
         permissions=("local_read:<data/cache/**>",),
         offline_level="full", version_policy=_FOLLOW, source=_OFFICIAL),
    dict(base="sk_unhat_eligibility_check", name="摘帽条件评估",
         description="动态评估摘帽条件满足情况，输出条件清单与满足度",
         version="1.0",
         input_schema={"type": "object", "properties": {"stock_id": {"type": "string"}}},
         output_schema={"type": "object", "properties": {"conditions": {"type": "array"}}},
         parameters=(_param("fiscal_years", "integer", 2, "考察财年数",
                            min_value=1, max_value=5),),
         dependencies=(), permissions=("local_read:<data/cache/**>",),
         offline_level="full", version_policy=_FOLLOW, source=_OFFICIAL),
    dict(base="sk_sector_heatmap", name="板块热力图",
         description="行业属性标记与板块热力图数据输出，附风险评级",
         version="1.0",
         input_schema={"type": "object", "properties": {"sector": {"type": "string"}}},
         output_schema={"type": "object", "properties": {"heatmap": {"type": "object"}}},
         parameters=(_param("top_n", "integer", 20, "返回板块数量",
                            min_value=1, max_value=100),),
         dependencies=(), permissions=("local_read:<data/cache/**>",),
         offline_level="full", version_policy=_FOLLOW, source=_OFFICIAL),
    dict(base="sk_sentiment_flow_analysis", name="情绪资金流向分析",
         description="情绪与资金流向的中性化分析，输出高换手与高热度预警",
         version="1.0",
         input_schema={"type": "object", "properties": {"stock_id": {"type": "string"}}},
         output_schema={"type": "object", "properties": {"warnings": {"type": "array"}}},
         parameters=(_param("window_days", "integer", 20, "统计窗口天数",
                            min_value=5, max_value=120),),
         dependencies=(), permissions=("local_read:<data/cache/**>",),
         offline_level="full", version_policy=_FOLLOW, source=_OFFICIAL),
    dict(base="sk_fundamental_screening", name="基本面筛选",
         description="基本面筛选与安全边际评估，输出机构优选池",
         version="1.0",
         input_schema={"type": "object", "properties": {"universe": {"type": "string"}}},
         output_schema={"type": "object", "properties": {"pool": {"type": "array"}}},
         parameters=(_param("margin", "number", 0.3, "安全边际下限",
                            min_value=0.0, max_value=1.0),),
         dependencies=(), permissions=("local_read:<data/cache/**>",),
         offline_level="full", version_policy=_FOLLOW, source=_OFFICIAL),
    # ── 主动服务 Bundle ──
    dict(base="sk_stock_watch", name="多标的盯盘",
         description="多标的盯盘，触发条件含关键词与财报日与异动与换手率",
         version="1.0",
         input_schema={"type": "object", "properties": {"stocks": {"type": "array"}}},
         output_schema={"type": "object", "properties": {"triggered": {"type": "array"}}},
         parameters=(_param("frequency_minutes", "integer", 60, "推送频率分钟数",
                            min_value=5, max_value=1440),),
         dependencies=(), permissions=("net_access:<*.baostock.com>",),
         offline_level="degraded", version_policy=_FOLLOW, source=_OFFICIAL),
    dict(base="sk_data_aggregate", name="数据聚合",
         description="数据聚合输出，形态含表格卡与趋势图与简报",
         version="1.0",
         input_schema={"type": "object", "properties": {"query": {"type": "object"}}},
         output_schema={"type": "object", "properties": {"cards": {"type": "array"}}},
         parameters=(_param("format", "enum", "brief", "输出形态",
                            choices=("table", "trend", "brief")),),
         dependencies=(), permissions=("local_read:<data/cache/**>",),
         offline_level="full", version_policy=_FOLLOW, source=_OFFICIAL),
    dict(base="sk_risk_alert", name="风险预警",
         description="风险预警输出，维度含退市倒计时与流动性枯竭",
         version="1.0",
         input_schema={"type": "object", "properties": {"stocks": {"type": "array"}}},
         output_schema={"type": "object", "properties": {"alerts": {"type": "array"}}},
         parameters=(_param("lookahead_days", "integer", 30, "前瞻天数",
                            min_value=1, max_value=365),),
         dependencies=("sk_delisting_risk_scan_v1.0",),
         permissions=("local_read:<data/cache/**>",),
         offline_level="full", version_policy=_FOLLOW, source=_OFFICIAL),
    dict(base="sk_opportunity_mine", name="机会挖掘",
         description="机会挖掘输出，经风格与板块偏好过滤，复用公共认知输出",
         version="1.0",
         input_schema={"type": "object", "properties": {"preference": {"type": "object"}}},
         output_schema={"type": "object", "properties": {"candidates": {"type": "array"}}},
         parameters=(_param("max_results", "integer", 20, "返回候选数量",
                            min_value=1, max_value=200),),
         dependencies=("sk_fundamental_screening_v1.0",),
         permissions=("local_read:<data/cache/**>",),
         offline_level="full", version_policy=_FOLLOW, source=_OFFICIAL),
    dict(base="sk_portfolio_stress_test", name="组合压力测试",
         description="组合压力测试，规则化情景推演输出",
         version="1.0",
         input_schema={"type": "object", "properties": {"portfolio": {"type": "object"}}},
         output_schema={"type": "object", "properties": {"scenarios": {"type": "array"}}},
         parameters=(_param("shock", "number", 0.2, "冲击幅度",
                            min_value=0.0, max_value=1.0),),
         dependencies=(), permissions=("local_read:<data/cache/**>",),
         offline_level="full", version_policy=_FOLLOW, source=_OFFICIAL),
    dict(base="sk_strategy_design", name="策略设计回测",
         description="策略设计与回测，链路含选股与信号与策略与回测",
         version="1.0",
         input_schema={"type": "object", "properties": {"universe": {"type": "string"}}},
         output_schema={"type": "object", "properties": {"report": {"type": "object"}}},
         parameters=(_param("lookback_years", "integer", 3, "回测年限",
                            min_value=1, max_value=10),),
         dependencies=("sk_data_aggregate_v1.0",),
         permissions=("local_read:<data/cache/**>",),
         offline_level="full", version_policy=_FOLLOW, source=_OFFICIAL),
)


def ensure_official_pack(registry) -> tuple:
    """幂等播种官方 Pack：已存在版本即跳过，返回本次新增的 skill_id。"""
    added: list[str] = []
    for seed in OFFICIAL_PACK:
        seed = dict(seed)
        base = seed.pop("base")
        version = seed.pop("version", "1.0")
        params = seed.pop("parameters", ())
        try:
            desc = registry.register(base, version=version, parameters=params, **seed)
        except Exception as exc:
            from st_agent.l1.skills.errors import SkillExistsError
            if isinstance(exc, SkillExistsError):
                continue
            raise
        added.append(desc.skill_id)
    return tuple(added)
