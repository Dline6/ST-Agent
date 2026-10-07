"""T-L0-016 真实端点冒烟（live 用例，默认排除，不进 CI）。

用本机已配的 OpenAI 兼容端点把「Store → EndpointRegistry → CredentialVault →
LlmClient → EgressGateway（按次 sender）→ 真实 HTTP/SSE」整条链路走通一次，
证明发送器在**真网络**上可用（离线分支由 `tests/l0/test_llm_http_transport.py`
以注入替身覆盖）。

配置取自环境变量 ``LLM_API_KEY`` / ``LLM_BASE_URL`` / ``LLM_MODEL``；缺省回落
到仓库根的 ``.env``（不入库，仅本机）。跑法：

    python -m pytest -m live -s tests/live/test_llm_live.py

明文（prompt / key / 回复）只落 pytest 临时目录里的加密分区，不进 Git。
"""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from st_agent.l0.llm import (
    EndpointRegistry,
    LlmClient,
    OpenAiRoute,
    openai_compat_sender_factory,
    provider_hosts_of,
)
from st_agent.l0.net import EgressGateway
from st_agent.l0.secrets import CredentialVault
from st_agent.l0.storage import Store
from st_agent.l1.runtime import open_runtime

pytestmark = pytest.mark.live

REPO_ROOT = Path(__file__).resolve().parents[2]
THROWAWAY_PASS = "live-throwaway-passphrase"
PROMPT = "用一句话说明什么是股票。"


def _config(name: str) -> str:
    """先取环境变量，缺省回落仓库根 ``.env``（仅本机、不入库）。"""
    value = (os.environ.get(name) or "").strip()
    if value:
        return value
    env_file = REPO_ROOT / ".env"
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith(f"{name}="):
                return line.split("=", 1)[1].strip()
    return ""


def test_real_endpoint_streams(tmp_path: Path) -> None:
    key = _config("LLM_API_KEY")
    base_url = _config("LLM_BASE_URL")
    model = _config("LLM_MODEL")
    if not (key and base_url and model):
        pytest.skip("需 LLM_API_KEY / LLM_BASE_URL / LLM_MODEL（环境变量或仓库根 .env）")

    root = tmp_path / "root"
    store = Store.create(root, THROWAWAY_PASS)
    gateway = EgressGateway(store, audit=True)                       # 构造期无 sender：全走按次
    vault = CredentialVault(store)
    vault.add("live-key", "llm_api_key", key)
    registry = EndpointRegistry(store)
    registry.register("live", kind="cloud", provider="openai-compatible",
                      capability={"max_context_tokens": 32_000},
                      credential_id="live-key")

    routes = {"openai-compatible": OpenAiRoute(base_url=base_url, model=model)}
    transport = gateway.llm_transport(
        provider_hosts_of(routes),
        sender_factory=openai_compat_sender_factory(routes),
    )
    client = LlmClient(store, registry, vault, transport)

    events = list(client.invoke("live", PROMPT, initiator="live-smoke", purpose="真实端点冒烟"))
    if events[0].kind == "error":
        pytest.fail(f"真实调用失败：{events[0].error_envelope.status} · "
                    f"{events[0].error_envelope.reason}")

    assert events[-1].kind == "done"
    text = "".join(e.text for e in events if e.kind == "chunk")
    assert text.strip(), "回复为空"
    print(f"\n端点回复：{text[:200]}")

    (audit,) = gateway.query(kind="llm_call")
    assert audit.status == "ok"
    assert audit.target_host == urlsplit(base_url).hostname
    assert audit.bytes_out == len(PROMPT.encode("utf-8"))

    blob = "\n".join(
        p.read_bytes().decode("utf-8", errors="replace")
        for p in root.rglob("*") if p.is_file()
    )
    assert PROMPT not in blob and key not in blob, "明文出现在落盘文件里"


def test_open_runtime_bootstraps_from_dotenv(tmp_path: Path) -> None:
    """T-L1-011 GWT-1/2 的真实验证：组合根**按环境**装载并真的调通一次。

    不传 ``llm_env`` ⇒ 走缺省取值面（环境变量 → ``<cwd>/.env``）；用例须从仓库
    根运行（``.env`` 所在处）。
    """
    if not (_config("LLM_API_KEY") and _config("LLM_BASE_URL") and _config("LLM_MODEL")):
        pytest.skip("需 LLM_API_KEY / LLM_BASE_URL / LLM_MODEL（环境变量或仓库根 .env）")

    class _Feed:
        def query(self, sql: str, params: tuple = ()):
            raise AssertionError("本用例不取数")

    root = tmp_path / "bootstrapped"
    # audit=True：本用例断言「真实调用经网关并留下审计记录」；产品缺省是**关**
    # （02 §6 / D-073）——默认关的语义由 tests/l0/test_gateway_audit_mode.py 覆盖。
    rt = open_runtime(root, THROWAWAY_PASS, create=True, market_query=_Feed(),
                      audit=True)

    assert rt.endpoints.get("cloud-main").credential_id == "llm-api-key"
    assert rt.provider_hosts.resolve("openai-compatible") == urlsplit(
        _config("LLM_BASE_URL")).hostname

    events = list(rt.llm.invoke("cloud-main", PROMPT,
                                initiator="live-bootstrap", purpose="组合根冒烟"))
    if events[0].kind == "error":
        pytest.fail(f"组合根真实调用失败：{events[0].error_envelope.status} · "
                    f"{events[0].error_envelope.reason}")

    assert events[-1].kind == "done"
    text = "".join(e.text for e in events if e.kind == "chunk")
    assert text.strip(), "回复为空"
    print(f"\n组合根端点回复：{text[:200]}")

    audits = rt.gateway.query(kind="llm_call")
    # 启动期能力探针（T-AGT-002）+ 本次调用各留一条；两条的发起方同为该端点
    assert len(audits) >= 2, "启动探针与本次调用应各留一条 llm_call 审计"
    audit = audits[-1]
    assert audit.status == "ok" and audit.initiator == "llm-endpoint:cloud-main"

    # T-AGT-002：启动探测已在**真实端点**上落定工具调用能力（真回了调用才为 true）
    assert rt.endpoints.get("cloud-main").capability.supports_function_calling is True, \
        "真实端点未探到工具调用支持——检查端点是否支持原生 function calling"

    blob = "\n".join(
        p.read_bytes().decode("utf-8", errors="replace")
        for p in root.rglob("*") if p.is_file()
    )
    key = _config("LLM_API_KEY")
    assert PROMPT not in blob and key not in blob, "明文出现在落盘文件里"
