"""T-INT-002 · M1 集成关卡的装配 rig（**真装配**，离线可跑）。

与 M0 的 ``rig.py`` 同一取向：真实 ``Store``（口令派生、落盘加密、清单校验）+
真实 ``MarketDb`` + 真实官方 Pack 流水线；只把**发包**换成可观测注入、把**意图
理解器**换成确定性脚本（任务 A4——离线关卡不随本机 ``.env`` 有无而变）。

差别在装配的**入口**：M0 在测试里手拼 `open_runtime`；M1 走**生产组合根**
:func:`st_agent.app.build_m1_runtime`——本关卡要证明的正是这个生产装配根存在且
把 L0→L1→L2→L3 装到同一个 `Store` 上（此前仓库没有任何生产装配根）。

``DeterministicUnderstander`` 按**子串规则表**产出 `IntentDraft`：规则即「这句
话应被理解成什么意图」，与 LLM 无关；真实端点路径由 `tests/live/` 兜（GWT-7）。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rig import PASS, ROOT_NAME, MarketData, RecordingSender, seed_market_db
from st_agent.app import M1Runtime, build_m1_runtime
from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.l3.intent import IntentDraft

__all__ = ["M1Rig", "DeterministicUnderstander", "seeded_m1", "seeded_m1_plaintext"]


class DeterministicUnderstander:
    """按规则表把输入收敛为 `IntentDraft`（离线关卡的理解端口，任务 A4）。"""

    def __init__(self, rules: list[tuple[str, IntentDraft]]) -> None:
        self._rules = rules
        self.seen: list[str] = []

    def understand(self, text: str) -> ResultEnvelope:
        self.seen.append(text)
        for needle, draft in self._rules:
            if needle in text:
                return ResultEnvelope.ok(draft)
        # 未命中 → 未收敛（§3.2：给方向候选，不硬猜）
        return ResultEnvelope.ok(
            IntentDraft(intent=None, directions=["查询退市风险", "修改盯盘阈值"])
        )


RISK_QUERY = IntentDraft(
    intent="query", target="sk_delisting_risk_scan_v1.0",
    understood={"threshold": 1.0},
)
CONFIGURE_QUERY = IntentDraft(
    intent="configure", target="sk_risk_alert_v1.0",
    understood={"lookahead_days": 3},
)
ANALYZE_QUERY = IntentDraft(intent="analyze", target=None)
MEMORY_OP_QUERY = IntentDraft(intent="memory_op", target=None)

DEFAULT_RULES: list[tuple[str, IntentDraft]] = [
    ("退市", RISK_QUERY),
    ("阈值", CONFIGURE_QUERY),
    ("分析", ANALYZE_QUERY),
    ("冲突", MEMORY_OP_QUERY),
]


@dataclass(frozen=True)
class M1Rig:
    """一次 M1 装配的全部句柄。"""

    root: Path
    m1: M1Runtime
    feed: MarketData
    sender: RecordingSender
    understander: DeterministicUnderstander


def seeded_m1(
    root: Path, *, rules: list[tuple[str, IntentDraft]] | None = None,
) -> M1Rig:
    """已播种数据面的 M1 全栈装配（生产组合根 `build_m1_runtime`；加密根）。"""
    return _assemble(root, PASS, rules)


def seeded_m1_plaintext(
    root: Path, *, rules: list[tuple[str, IntentDraft]] | None = None,
) -> M1Rig:
    """**免口令明文根**（L0 新默认，[02 §2.2] / [D-073]）上的 M1 全栈装配。

    证明组合根在 `T-L0-018.1` 的新默认之下端到端仍成立——L0 单任务用例覆盖
    「`Store` 明文可读写」，但覆盖不到「组合根 + L2/L3 叠在**明文根**上」这层
    装配面（GWT-8）。
    """
    return _assemble(root, None, rules)


def _assemble(
    root: Path, passphrase: str | None, rules: list[tuple[str, IntentDraft]] | None,
) -> M1Rig:
    """按口令口径装配一次 M1 全栈（`passphrase=None` ⇒ 明文根，新默认）。"""
    seed_market_db(root, passphrase)
    feed = MarketData()
    sender = RecordingSender()
    understander = DeterministicUnderstander(
        rules if rules is not None else DEFAULT_RULES
    )
    m1 = build_m1_runtime(
        root, passphrase, market_query=feed, sender=sender, llm_env={},
        understander=understander,
    )
    return M1Rig(root=root, m1=m1, feed=feed, sender=sender,
                 understander=understander)
