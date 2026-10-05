"""`T-INT-003` GWT-9 · 真实链路的多视角观点合成冒烟（live 用例，默认排除，不进 CI）。

M2 关卡的离线用例（``tests/integration/test_m2_deliberation.py``）把三个 L4 鸭子端口换成
脚本件；本文件证明**生产组合根的默认接法**在真网络上成立：

    build_m2_runtime(不传 synthesizer/reviewer) → LlmOpinionSynthesizer / LlmEvidenceReviewer
    → runtime.llm → EndpointRegistry/CredentialVault/EgressGateway（按次 sender）
    → 真实 HTTP/SSE

盲点维度目录默认件**不出网**（读本地市场库），随同一装配一并走到。

配置取自环境变量 / 仓库根 ``.env``（不入库），缺三键即 skip 并在执行日志写明原因
（不许默默略过，[工作流「测试分层」] / [D-059]）。

跑法：

    python -m pytest -m live tests/live/test_llm_live.py tests/live/test_llm_deliberation_live.py

"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

# 复用集成关卡的播种与取数面（tests/integration 不在本目录的 import 路径上）
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "integration"))

from st_agent.app import build_m2_runtime  # noqa: E402
from st_agent.contracts.neutrality import NeutralityGuard  # noqa: E402
from rig import ROOT_NAME, MarketData, RecordingSender, seed_market_db  # noqa: E402

pytestmark = pytest.mark.live

REPO_ROOT = Path(__file__).resolve().parents[2]
THROWAWAY_PASS = "m2-live-throwaway-passphrase"
TOPIC = "sh.600000 是否值得关注"

_GUARD = NeutralityGuard()


def _config(name: str) -> str:
    value = (os.environ.get(name) or "").strip()
    if value:
        return value
    env_file = REPO_ROOT / ".env"
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith(f"{name}="):
                return line.split("=", 1)[1].strip()
    return ""


def test_production_root_synthesizes_opinions_via_real_endpoint(tmp_path: Path) -> None:
    """默认 `LlmOpinionSynthesizer` 经真实端点把视角数据研判为结构化观点。"""
    if not (_config("LLM_API_KEY") and _config("LLM_BASE_URL") and _config("LLM_MODEL")):
        pytest.skip("需 LLM_API_KEY / LLM_BASE_URL / LLM_MODEL（环境变量或仓库根 .env）")

    root = tmp_path / ROOT_NAME
    seed_market_db(root, THROWAWAY_PASS)
    feed = MarketData()
    sender = RecordingSender()
    # 不传 llm_env / synthesizer / reviewer ⇒ 缺省取值面（环境变量 → .env）+ 三个真实现。
    # 显式 ``audit=True``：本用例断言「两次真出网各留一条审计」，循 test_llm_live.py / rig.py
    # 的既有先例——产品缺省是**关**（[02 §6] / [D-073]）；默认关的语义由
    # tests/l0/test_gateway_audit_mode.py 覆盖。本行随 `T-INT-002` 重跑同根因一并修（审计由
    # 默认开改默认关后，本用例与 M1 关卡 live 用例同样静默转红——CI 恒排除 live 故无人发现）。
    m2 = build_m2_runtime(
        root, THROWAWAY_PASS, market_query=feed, sender=sender, audit=True,
    )

    # 确认卡的取值面是鸭子类型（`analyze` 去向只消费 `.values`）——真实链路上它由
    # 澄清协议收敛，这里直接给出收敛后的结果，把验证面收敛到「合成端口真出网」。
    outcome = m2.analyze.analyze(
        SimpleNamespace(values={"topic": TOPIC, "mode": "quick"})
    )
    if outcome.envelope.status != "ok":
        pytest.fail(
            f"真实链路多视角编排未通：{outcome.envelope.status} · {outcome.envelope.reason}"
        )

    opinions = list(outcome.result.opinions)
    assert opinions, "快速模式应至少纳入一个视角"
    with_opinion = [o for o in opinions if o.stance != "insufficient-data"]
    assert with_opinion, "本地数据面就绪时，真实合成端点应产出至少一条可呈现的观点"
    for opinion in with_opinion:
        assert opinion.stance in ("positive", "negative", "neutral")
        for reason in opinion.key_reasons:
            assert _GUARD.check_output(reason).passed, (
                f"真实端点产出的理由命中中性化校验：{reason!r}"
            )
    # 盲点「维度 → 产出 Skill」目录（默认件读本地市场库，**不出网**）：本地缓存有表
    # 而官方 Skill 未覆盖的维度应被如实算出
    assert outcome.map is not None and outcome.view is not None
    assert {b.key for b in outcome.map.blind_spots} >= {"announcement"}

    # 证据重研判（另一个真实现）：对一条证据直接跑一轮研判，证明该端口也真过出网链路
    reviewer = m2.examiner._reviewer  # noqa: SLF001（装配断言：组合根装的是真实现）
    review = reviewer.review("run_" + "0" * 20, lens_ids=("lens_a", "lens_b"), as_of=None)
    assert review.validity in ("valid", "invalid", "unknown")
    assert review.freshness in ("fresh", "stale", "unknown")
    assert _GUARD.check_output(review.note).passed, (
        f"真实端点产出的研判说明命中中性化校验：{review.note!r}"
    )

    # 两次出网都经 L0 统一出口并留审计（02 §6 唯一出口）
    audits = m2.m1.runtime.gateway.query(kind="llm_call")
    assert len(audits) >= 2, "合成与重研判各应留一条出网审计"
    assert any(a.status == "ok" for a in audits)
    print(
        f"\n真实端点编排：mode={outcome.mode} 视角 {len(opinions)} 个（有结论 "
        f"{len(with_opinion)}），出网审计 {len(audits)} 条"
    )
