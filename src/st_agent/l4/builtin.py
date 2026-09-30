"""官方预置 7 视角常设阵容种子（[06 §1 阵容表](../../../docs/技术架构-v2/06-L4-多视角推理.md)）。

每条内置视角的 `skill_bundle` 复用 [`l1.skills.pack.OFFICIAL_PACK`](../l1/skills/pack.py) 现成 skill base
（任务 A1：无需为内置视角新建 Skill），`lens_id` 为**确定性摘要**（业务键＝视角名 → 稳定 ID，
同 [01 §1](../../../docs/技术架构-v2/01-平台共享契约.md) `announcement_id` 口径），播种幂等不产生随机漂移。

阵容（06 §1）：机会 / 风险 / 基本面 / 情绪 / 流动性 / 宏观 / 合规——`name` 全部过
[01 §6](../../../docs/技术架构-v2/01-平台共享契约.md) `check_name`（实现时已逐条实测通过）。
"""

from __future__ import annotations

from st_agent.contracts.identifiers import digest_id
from st_agent.l4.lens import BUILTIN_LENSES_COUNT, JudgingCriteria, Lens

__all__ = ["BUILTIN_LENSES", "builtin_lens_ids"]


def _lid(name: str) -> str:
    """内置视角的稳定 lens_id（按视角名确定性派生）。"""
    return digest_id("lens", "builtin", name)


def _seed(
    name: str, description: str, skill_bundle: tuple[str, ...],
    natural: str, *, high_at: float = 0.8, medium_at: float = 0.5,
) -> Lens:
    return Lens(
        lens_id=_lid(name), name=name, description=description,
        skill_bundle=skill_bundle,
        judging_criteria=JudgingCriteria(natural=natural),
        confidence_policy={"high_at": high_at, "medium_at": medium_at},
        kind="builtin", enabled=True,
    )


# 关注维度取自 06 §1 阵容表；skill_bundle 取自 OFFICIAL_PACK base（补 _v1.0 版本后缀成 skill_id）。
BUILTIN_LENSES: tuple[Lens, ...] = (
    _seed("机会视角", "识别潜在上涨、反转、摘帽机会", ("sk_opportunity_mine_v1.0", "sk_unhat_eligibility_check_v1.0"),
          "评估上涨、反转与摘帽的可行性与条件满足度"),
    _seed("风险视角", "识别退市、流动性、合规与系统性风险", ("sk_risk_alert_v1.0", "sk_delisting_risk_scan_v1.0"),
          "按退市倒计时、流动性枯竭与合规信号加权风险等级", high_at=0.7),
    _seed("基本面视角", "评估财务健康度、持续经营能力与安全边际", ("sk_fundamental_screening_v1.0",),
          "以财务健康度、持续经营能力与安全边际为准"),
    _seed("情绪视角", "刻画舆情热度、龙虎榜资金与散户行为模式", ("sk_sentiment_flow_analysis_v1.0", "sk_sector_heatmap_v1.0"),
          "以舆情热度与资金流向的中性化指标为准"),
    _seed("流动性视角", "评估换手率、成交量、买卖价差与大额冲击", ("sk_data_aggregate_v1.0", "sk_risk_alert_v1.0"),
          "以换手率、成交量、买卖价差与大额冲击为准"),
    _seed("宏观视角", "评估政策导向、赛道景气度与系统性因素", ("sk_sector_heatmap_v1.0", "sk_portfolio_stress_test_v1.0"),
          "以政策导向、赛道景气与系统性因素做情景推演"),
    _seed("合规视角", "核查监管红线、信息披露完整性与异常交易嫌疑", ("sk_delisting_risk_scan_v1.0", "sk_risk_alert_v1.0"),
          "以监管红线、信披完整性与异常交易嫌疑为准", high_at=0.9),
)

assert len(BUILTIN_LENSES) == BUILTIN_LENSES_COUNT, "内置视角数须与 06 §1 阵容表一致"


def builtin_lens_ids() -> tuple[str, ...]:
    """7 内置视角的 lens_id（幂等播种判重单位）。"""
    return tuple(lens.lens_id for lens in BUILTIN_LENSES)
