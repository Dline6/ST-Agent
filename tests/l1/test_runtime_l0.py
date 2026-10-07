"""T-L1-006.1 测试：组合根的 L0 面装配（02 §3 / §4 / §6；未决 Q-001 → D-005）。

GWT 对照（任务文件 4 条）：
- GWT-1 装配即用且不覆盖既有配置
- GWT-2 出网唯一出口、缺省 fail-closed
- GWT-3 映射归属裁定落地（维持 L1：`config/llm-provider-host/`，注入沙箱）
- GWT-4 会话级单次解锁（口令派生每会话每存储只跑一次；不设进程内密钥缓存）
"""

from __future__ import annotations

from pathlib import Path

import pytest

import st_agent.l0.storage.store as store_mod
from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.l0.storage import Store
from st_agent.l1.runtime import L1Runtime, build_l1_runtime, open_runtime

PASS = "correct horse battery staple"
LOCAL_CAP = {"max_context_tokens": 4_096, "supports_structured_output": False}
PROVIDER_HOST_PATH = "llm-provider-host/openai-compatible.json"


@pytest.fixture()
def store(tmp_path: Path) -> Store:
    return Store.create(tmp_path / "root", PASS)


class _FakeMarketQuery:
    """恒空取数源（本叶不关心官方 Pack 的执行面，只为满足必填注入点）。"""

    def query(self, sql: str, params: tuple = ()):
        return ResultEnvelope.empty("本叶不取数")


_FAKE_MARKET = _FakeMarketQuery()


def _build(store: Store, **kwargs) -> L1Runtime:
    # 本套件断言「经网关的出网面」，故显式开审计——产品缺省是**关**（02 §6 / D-073），
    # 默认关的语义由 tests/l0/test_gateway_audit_mode.py 覆盖。
    kwargs.setdefault("audit", True)
    return build_l1_runtime(store, market_query=_FAKE_MARKET, **kwargs)


def _seed(rt: L1Runtime) -> None:
    """写入三类既有配置（端点 / 凭据 / 映射），供「不覆盖」断言对照。"""
    rt.vault.add("openai-main", "llm_api_key", "sk-test-value")
    rt.endpoints.register(
        "cloud-main", kind="cloud", provider="openai-compatible",
        capability=LOCAL_CAP, credential_id="openai-main",
    )
    rt.provider_hosts.register("openai-compatible", "api.example.com")


class TestGwt1AssemblyReady:
    def test_facades_are_wired_to_the_same_store(self, store: Store):
        rt = _build(store)
        assert isinstance(rt, L1Runtime)
        assert rt.store is store
        assert rt.endpoints.list_endpoints() == ()
        assert rt.vault.list_credentials() == ()
        assert rt.provider_hosts.list() == ()

    def test_rebuild_keeps_existing_config_verbatim(self, store: Store):
        _seed(_build(store))
        before = (
            store.list_files("config"),
            [e.endpoint_id for e in _build(store).endpoints.list_endpoints()],
        )

        again = _build(store)

        assert store.list_files("config") == before[0]
        assert [e.endpoint_id for e in again.endpoints.list_endpoints()] == before[1]
        assert [c.credential_id for c in again.vault.list_credentials()] == ["openai-main"]
        assert [e.config_id for e in again.provider_hosts.list()] == [
            "llm-provider-host/openai-compatible"
        ]
        assert again.endpoints.get("cloud-main").credential_id == "openai-main"


class TestGwt2EgressSingleChannel:
    def test_gateway_without_sender_is_unavailable(self, store: Store):
        rt = _build(store)

        env = rt.gateway.execute(
            "index_browse", "index.example.com", initiator="t", purpose="官方索引浏览",
        )

        assert env.status == "unavailable"
        (event,) = rt.gateway.query(kind="index_browse")
        assert event.status == "unavailable"

    def test_llm_without_transport_is_unavailable(self, store: Store):
        rt = _build(store)
        rt.endpoints.register(
            "local-main", kind="local", provider="local-llama", capability=LOCAL_CAP,
        )

        events = list(rt.llm.invoke(
            "local-main", "你好", initiator="t", purpose="装配自检",
        ))

        assert [e.kind for e in events] == ["error"]
        assert events[0].error_envelope.status == "unavailable"

    def test_injected_sender_goes_through_the_audited_gateway(self, store: Store):
        def sender(kind, host, timeout_ms):
            return (11, 22, ("payload",))

        rt = _build(store, sender=sender)

        env = rt.gateway.execute(
            "data_fetch", "data.example.com", initiator="t", purpose="取数",
        )

        assert env.status == "ok"
        assert env.data == {"bytes_in": 22}
        (event,) = rt.gateway.query(kind="data_fetch")
        assert (event.target_host, event.status) == ("data.example.com", "ok")


class TestGwt3ProviderHostOwnership:
    """D-005 复核结论：映射**维持 L1 沙箱持有**，由组合根构造并注入（不迁 L0）。"""

    def test_mapping_lives_in_the_l1_owned_config_prefix(self, store: Store):
        rt = _build(store)

        rt.provider_hosts.register("openai-compatible", "api.example.com")

        assert PROVIDER_HOST_PATH in store.list_files("config")
        assert rt.provider_hosts.resolve("openai-compatible") == "api.example.com"

    def test_runtime_exposes_the_very_registry_the_sandbox_consumes(self, store: Store):
        rt = _build(store)
        rt.provider_hosts.register("openai-compatible", "api.example.com")

        # 装配不迁移归属：同一个 `Store` 上的同类门面读到同一条映射（供 `.2` 注入沙箱）
        assert _build(store).provider_hosts.list() == rt.provider_hosts.list()

    def test_build_writes_only_the_official_pack_seed(self, store: Store):
        """装配只新增官方 Pack 描述体（`.2` 面），不迁移归属、不写其他配置。"""
        _build(store)
        after = set(store.list_files("config"))

        assert after and all(n.startswith("skill-registry/") for n in after)
        _build(store)
        assert set(store.list_files("config")) == after


class TestGwt4SessionUnlock:
    def _counter(self, monkeypatch) -> list[int]:
        calls: list[int] = []
        real = store_mod.derive_master_key

        def counting(passphrase, salt):
            calls.append(1)
            return real(passphrase, salt)

        monkeypatch.setattr(store_mod, "derive_master_key", counting)
        return calls

    def test_unlock_derives_once_per_session(self, tmp_path: Path, monkeypatch):
        root = tmp_path / "root"
        Store.create(root, PASS)
        calls = self._counter(monkeypatch)

        # llm_env={}：本用例只测解锁，显式关闭引导装载——否则会随本机 `.env`
        # 播种端点并触发启动期能力探针（T-AGT-002），使单测发真实出网
        rt = open_runtime(root, PASS, market_query=_FAKE_MARKET, llm_env={})

        # 主密钥 + secrets 独立派生（02 §2.2），各一次
        assert len(calls) == 2

        # 运行期取用不再重新解锁（组合根持该句柄供整个会话复用）
        rt.endpoints.list_endpoints()
        rt.vault.list_credentials()
        rt.provider_hosts.list()
        rt.gateway.execute("index_browse", "index.example.com",
                           initiator="t", purpose="官方索引浏览")
        assert len(calls) == 2

    def test_no_in_process_key_cache(self, tmp_path: Path, monkeypatch):
        """不引入进程内密钥缓存——口令/校验值不长驻内存，代价是每次入口各自解锁。"""
        root = tmp_path / "root"
        Store.create(root, PASS)
        calls = self._counter(monkeypatch)

        open_runtime(root, PASS, market_query=_FAKE_MARKET, llm_env={})
        open_runtime(root, PASS, market_query=_FAKE_MARKET, llm_env={})

        assert len(calls) == 4

    def test_create_flag_initialises_a_fresh_store(self, tmp_path: Path):
        root = tmp_path / "fresh"

        # llm_env={} 显式关闭引导装载（T-L1-011）——本用例断言「全新存储为空」，
        # 不能随本机 .env 的有无而变（否则 CI 无 .env 过、本机有 .env 挂）
        rt = open_runtime(root, PASS, create=True, market_query=_FAKE_MARKET, llm_env={})

        assert rt.endpoints.list_endpoints() == ()
        assert rt.vault.list_credentials() == ()
        # 二次以既有存储打开（不经 create）可行
        again = open_runtime(root, PASS, market_query=_FAKE_MARKET, llm_env={})
        assert again.endpoints.list_endpoints() == ()

    def test_wrong_passphrase_is_rejected_by_l0(self, tmp_path: Path):
        root = tmp_path / "root"
        Store.create(root, PASS)

        with pytest.raises(Exception) as excinfo:
            open_runtime(root, "wrong passphrase", market_query=_FAKE_MARKET)

        assert "主密码错误" in str(excinfo.value) or "StorageOpenError" in type(
            excinfo.value
        ).__name__
