"""L1 运行时组合根（T-L1-006；未决 Q-001 → 决议 D-005）。

给定一个**已解锁**的 ``Store``，把分散的子系统装配成可用的 L1 运行时：

- L0 面（``T-L1-006.1``）：``EndpointRegistry`` / ``CredentialVault`` /
  ``EgressGateway`` / ``LlmClient``
- L1 能力面（``T-L1-006.2``）：``SkillRegistry``（官方 Pack 播种）/
  ``SkillSandbox`` / ``SkillRunner`` / 输出复用 / 工作流库
- 外围接线（``T-L1-006.3`` / ``T-L1-006.4``）：MCP Hub 的「删除即回收」、
  调度器与在线翻转补跑

两条边界有意写死在此：

- **解锁不属组合根**——口令派生 / 解密由 L0 存储子系统承担（``Store.open`` /
  ``Store.create``）；组合根只接收已解锁句柄。``open_runtime`` 是**会话入口**：
  它解锁**一次**并把该 ``Store`` 交给运行时，此后整个会话复用同一句柄，运行期
  不再重新派生（口令与派生密钥均不落盘、也不在进程内另设密钥缓存——避免口令 /
  校验值长驻内存；这是 L0 册 ``B2`` 的收口口径）。
- **出网与取数一律注入**——发包实现（``sender``）、LLM 传输（``transport``）、
  官方 Pack 的取数源（``market_query``）、调度权限来源（``permissions``）都是
  调用方注入的鸭子类型；缺省即 fail-closed（不臆测、不直连、不自动放行）。
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from st_agent.l0.llm.client import LlmClient
from st_agent.l0.llm.errors import LlmNotFoundError
from st_agent.l0.llm.http_transport import (
    OpenAiRoute,
    openai_compat_sender_factory,
    provider_hosts_of,
)
from st_agent.l0.llm.registry import EndpointRegistry
from st_agent.l0.net.gateway import EgressGateway
from st_agent.l0.secrets.errors import CredentialNotFoundError
from st_agent.l0.secrets.vault import CredentialVault
from st_agent.l0.storage.store import Store
from st_agent.l1.mcp.lifecycle import McpHubStateMachine
from st_agent.l1.mcp.mapping import McpSkillMapper
from st_agent.l1.mcp.permissions import McpPermissionBook
from st_agent.l1.mcp.registry import McpServerRegistry
from st_agent.l1.reuse.freshness import MarketFreshnessOracle
from st_agent.l1.reuse.registry import OutputRegistry
from st_agent.l1.registry.facade import ConfigRegistryFacade
from st_agent.l1.registry.families import (
    McpHubPolicyFamily,
    ProviderHostFamily,
    RetentionFamily,
    SchedulerPolicyFamily,
)
from st_agent.l1.runner.runner import SkillRunner
from st_agent.l1.sandbox.provider_hosts import ProviderHostRegistry
from st_agent.l1.sandbox.sandbox import SkillSandbox
from st_agent.l1.scheduler.models import ScheduledRun
from st_agent.l1.scheduler.policy import SchedulerPolicy
from st_agent.l1.scheduler.scheduler import Scheduler, skill_of
from st_agent.l1.skills.ids import base_of
from st_agent.l1.skills.official.install import install_official_pack
from st_agent.l1.skills.permissions import SkillPermissionBook
from st_agent.l1.skills.registry import SkillRegistry
from st_agent.l1.workflow.store import WorkflowStore

__all__ = [
    "DOTENV_NAME",
    "LLM_CREDENTIAL_ID",
    "LLM_ENDPOINT_ID",
    "LLM_ENV_KEYS",
    "LLM_PROVIDER",
    "L1Runtime",
    "LlmBootstrap",
    "SkillPermissionSource",
    "VaultEnvResolver",
    "build_l1_runtime",
    "llm_env_from_ambient",
    "load_dotenv",
    "open_runtime",
]

DOTENV_NAME = ".env"
"""引导装载默认查找的文件名（开发 / 自用通道，**不进产品面文档**）。"""

LLM_ENV_KEYS = ("LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL")
"""引导装载取的三键（三键齐备才装；缺任一即 fail-closed）。"""

LLM_ENDPOINT_ID = "cloud-main"
LLM_CREDENTIAL_ID = "llm-api-key"
LLM_PROVIDER = "openai-compatible"

LLM_CAPABILITY = {"max_context_tokens": 128_000, "supports_structured_output": True}
"""引导装载的端点能力声明。

``.env`` 三键不含上下文规模，故取一个**不设限的宽松档**（宁可在提供方侧被拒，
也不在本地把一个合法 prompt 误判超限）。真实档位待设置页任务按端点配置。
"""


class VaultEnvResolver:
    """``env_refs`` → 取值的解析器（凭据库口径；03 §5.1 的注入点）。

    骨架期约定：**环境变量名即凭据标识**——``EnvResolver = Callable[[str], str]``
    只给出名字，别名表尚无口径，凭空造一张表就是拿约定当规格。取值经
    ``CredentialVault.use``（唯一明文出口）并留一条使用记录，归属取构造期给定的
    ``initiator`` / ``purpose``。
    """

    def __init__(
        self,
        vault: CredentialVault,
        *,
        initiator: str = "mcp-env-resolver",
        purpose: str = "MCP Server 环境变量注入",
    ) -> None:
        self._vault = vault
        self._initiator = initiator
        self._purpose = purpose

    def __call__(self, name: str) -> str:
        return self._vault.use(name, self._initiator, self._purpose)


class SkillPermissionSource:
    """调度执行的权限来源：由批准账本供给「已批准」，**不造**这一事实（``T-L1-009.2``）。

    实现 :class:`~st_agent.l1.scheduler.models.PermissionSource`（鸭子类型），
    组合根把它作为 ``Scheduler`` 的缺省权限来源。空账本 → 空集，仍交沙箱
    fail-closed（「声明 ≠ 批准」的口径不破）。

    目标到**实际执行的 Skill** 的解析复用
    :func:`~st_agent.l1.scheduler.scheduler.skill_of`（与 ``Scheduler`` 同源），
    故「批准的对象」与「执行的对象」恒为同一个。

    账本归属：

    - **MCP 派生 Skill**（其权限声明即所属 Server 的声明）→ 委托
      ``McpPermissionBook``：该事实在 MCP 侧已存在，不另存第二份；
    - **其余**（官方 / 自建 / 复合）→ ``SkillPermissionBook``，键为 **base**
      （能力身份；复合 Skill 取**独立批准**，不继承成员的批准态）。
    """

    def __init__(
        self,
        *,
        skills: SkillPermissionBook,
        mcp: McpPermissionBook | None = None,
        mcp_servers=None,
        mcp_mapper=None,
    ) -> None:
        self._skills = skills
        self._mcp = mcp
        self._mcp_servers = mcp_servers
        self._mcp_mapper = mcp_mapper

    def approved_for(self, target) -> tuple[str, ...]:
        """该目标本次执行已获批准的权限声明集（无 → 空元组）。"""
        base = base_of(skill_of(target))
        server_id = self._server_of(base)
        if server_id is not None and self._mcp is not None:
            return self._mcp.approved_permissions(server_id)
        return self._skills.approved_permissions(base)

    def _server_of(self, base: str) -> str | None:
        """该 base 若由某 MCP Server 的 tool 派生而来，返其 ``server_id``。

        反查走公开面（``list_servers`` × ``mappings``），不给 done 模块加 API；
        派生 Skill 的 ``skill_id`` 由 ``(server, tool)`` 确定性派生，故 base 可比对。
        """
        if self._mcp_servers is None or self._mcp_mapper is None:
            return None
        for record in self._mcp_servers.list_servers():
            for mapping in self._mcp_mapper.mappings(record.server_id):
                if base_of(mapping.skill_id) == base:
                    return record.server_id
        return None


@dataclass(frozen=True)
class L1Runtime:
    """装配完成的 L1 运行时（组合根产物；持有一个**已解锁** ``Store``）。

    各字段即各子系统的门面，直接可用；跨层消费方（L3 对话 / L4 视角 / L5 投递）
    经本对象取用，不各自 new。

    :param provider_hosts: ``provider → host`` 映射（D-005 / Q-001 复核：**维持
        L1 归属**，由组合根构造并注入沙箱；LlmEndpoint 无 host 字段，迁 L0 须改
        :doc:`02 §4 </docs/技术架构-v2/02-L0-本地优先基座>` 而收益为零）
    :param sandbox: 已携端点注册表与映射的执行沙箱——缺任一即 LLM 出口
        fail-closed（``T-L1-001.5`` 假设 A3）
    :param mcp_servers: MCP Server 注册表，其 ``on_server_removed`` 已由组合根接上
        回收（映射记录 + 派生 Skill + 生命周期当前态记录），故移除走
        :meth:`remove_mcp_server` 而非直接调 ``remove_server``
    """

    store: Store
    endpoints: EndpointRegistry
    vault: CredentialVault
    gateway: EgressGateway
    llm: LlmClient
    provider_hosts: ProviderHostRegistry
    skills: SkillRegistry
    sandbox: SkillSandbox
    runner: SkillRunner
    outputs: OutputRegistry
    workflows: WorkflowStore
    mcp_servers: McpServerRegistry
    mcp_permissions: McpPermissionBook
    mcp_mapper: McpSkillMapper
    mcp_machine: McpHubStateMachine
    skill_permissions: SkillPermissionBook
    scheduler: Scheduler
    config_registry: ConfigRegistryFacade
    """统一配置注册表门面（[01 §7](../../../docs/技术架构-v2/01-平台共享契约.md)；
    决策 [D-067](../../../项目管理/决策日志.md)）。跨层消费方（L3 对话即配置）经它
    取登记项与落值；L2 侧 ``memory-policy`` 族由 app 组合根追加注入。"""

    def remove_mcp_server(self, server_id: str, *, trace_id: str | None = None) -> None:
        """移除一台 MCP Server（03 §5.2 的完整序列）。

        顺序有意如此：先 ``disable``（置开关 → 给进行中调用发取消信号 → 落
        ``disabled`` 状态留痕），再 ``remove_server``（删注册记录与批准态 → 触发
        组合根的回收回调：映射记录 ＋ 派生 Skill 的全部版本 ＋ 生命周期当前态
        记录）。``execution_log`` 的审计留痕（状态转移 / 通知 / 网络活动）不动。
        """
        self.mcp_machine.disable(server_id, trace_id=trace_id)
        self.mcp_servers.remove_server(server_id)

    def set_online(self, online: bool, *, now: datetime | None = None) -> tuple[ScheduledRun, ...]:
        """翻转在线态；**由离线翻回在线**时按策略处置待补到期点（03 §6 第三句）。

        在线态的唯一源头是 :class:`~st_agent.l0.net.gateway.EgressGateway`，本方法
        只做「翻转 + 在恢复这一跳补跑」——只在「离 → 在线」触发，重复置同一态或
        仍置离线都无副作用（补跑本身幂等，见 ``T-L1-005.3``）。

        :param now: 本次补跑的时刻（缺省取本机当前带时区时间）
        :return: 本次补跑 / 跳过的执行结果；未触发补跑即空元组
        """
        was_online = self.gateway.online
        self.gateway.set_online(online)
        if not online or was_online:
            return ()
        return self.scheduler.catch_up(now if now is not None else _local_now())


def build_l1_runtime(
    store: Store,
    *,
    market_query: Any,
    sender: Any | None = None,
    transport: Any | None = None,
    llm_config: "LlmBootstrap | None" = None,
    llm_post: Any | None = None,
    freshness_source: Any | None = None,
    mcp_transport_factory: Any | None = None,
    mcp_env_resolver: Any | None = None,
    permissions: Any | None = None,
    online: bool = True,
) -> L1Runtime:
    """装配 L1 运行时（L0 面 + L1 能力面 + MCP 面；调度接线见 ``.4``）。

    构造各门面**不写盘**（官方 Pack 的**播种**除外——幂等：已注册版本即跳过），
    故重复装配不重置任何用户配置。

    :param market_query: 官方 Pack 的取数源（``MarketQuerySource`` 鸭子类型，
        ``query(sql, params=()) -> ResultEnvelope``）。**必填**——它是「官方 Pack
        可执行」的前提，而唯一可用的缺省值是硬编码 ``MarketDb``，与「取数源经
        注入」的口径相悖（任务 A1）
    :param store: **已解锁**的 ``Store``（解锁不属组合根）
    :param sender: 按次发包实现（``Sender``）；缺省 ``None`` → 出网一律
        ``unavailable``（不臆测、不直连）
    :param transport: LLM 传输实现；缺省 ``None`` → 调用即 ``unavailable``。
        **显式传入即优先**（``llm_config`` / ``llm_post`` 随之失效）
    :param llm_config: 引导装载的 LLM 三键（:class:`LlmBootstrap`）；给定且未显式
        传 ``transport`` 时，组合根**幂等播种**端点 / 凭据 / ``provider → host``
        映射，并装配真实发送器（``T-L1-011``）。缺省 ``None`` → 不装，调用仍
        fail-closed。**本函数不读环境**——取值面由 ``open_runtime`` 或调用方解析
    :param llm_post: 真实发送器的 HTTP 发送口（缺省 ``urllib``）；测试注入替身
        以离线覆盖
    :param freshness_source: 新鲜度来源（须提供 ``freshness_verdict(domain)``，
        如 ``st_agent.l0.market.BaoStockSync``）；缺省 ``None`` → 不接新鲜度判定
        （执行器的 ``ctx.freshness`` 为 ``None``，口径不合即构造期拒绝）
    :param mcp_transport_factory: MCP 传输工厂（缺省按 Server 记录种类造真实传输）
    :param mcp_env_resolver: MCP ``env_refs`` 的取值器；缺省用
        :class:`VaultEnvResolver`（环境变量名即凭据标识，经凭据库取值并留痕）
    :param permissions: 调度执行的权限来源（``PermissionSource`` 鸭子类型，
        ``approved_for(target) -> tuple[str, ...]``）。缺省 ``None`` → 用组合根自建的
        :class:`SkillPermissionSource`（读 ``SkillPermissionBook`` 与
        ``McpPermissionBook``）；**账本为空即空集**，需要声明的定时执行由流水线拦成
        ``validation_failed``——组合根只**读**「已批准」这一事实、从不**造**它
        （``D-039`` 已否决「取声明即视为已批准」）。``T-L1-009`` 之前该事实无落点
        （L1 册 `D1`）
    :param online: 初始在线态（断网时经 ``L1Runtime.set_online`` 翻转）
    """
    gateway = EgressGateway(store, sender=sender, online=online)
    endpoints = EndpointRegistry(store)
    vault = CredentialVault(store)
    provider_hosts = ProviderHostRegistry(store)

    # 引导装载（T-L1-011）：显式 transport 优先；否则按 llm_config 幂等播种并装配真实发送器
    llm_transport = transport
    if llm_transport is None and llm_config is not None:
        llm_transport = _bootstrap_llm_transport(
            llm_config, endpoints=endpoints, vault=vault,
            provider_hosts=provider_hosts, gateway=gateway, post=llm_post,
        )
    llm = LlmClient(store, endpoints, vault, llm_transport)

    freshness = (
        MarketFreshnessOracle(freshness_source) if freshness_source is not None else None
    )
    # 账本先于注册表构造并注入——`unregister` 据此回收该 base 的批准记录
    # （T-L3-006 销 L1 册 D2 回收半；对位 McpServerRegistry.remove_server 的
    # permissions.forget）。同一实例经 L1Runtime.skill_permissions 对外供给。
    skill_permissions = SkillPermissionBook(store)
    skills = SkillRegistry(store, permission_book=skill_permissions)
    sandbox = SkillSandbox(store, endpoints=endpoints, provider_hosts=provider_hosts)
    outputs = OutputRegistry(store, freshness=freshness)
    runner = SkillRunner(
        store, skills, llm=llm, gateway=gateway,
        outputs=outputs, freshness=freshness, sandbox=sandbox,
    )
    workflows = WorkflowStore(store, skills)
    install_official_pack(skills, runner, market_query=market_query)

    machine: McpHubStateMachine | None = None
    mapper: McpSkillMapper | None = None

    def _on_server_removed(server_id: str) -> None:
        """移除序列的第二步：映射记录 ＋ 派生 Skill ＋ 生命周期当前态记录。

        两个回收都是**幂等**的，且顺序无关（各管一个前缀）；``mcp-lifecycle``
        的状态记录不在 ``recycle_server`` 的范围内（03 §5.2 的范围边界），故在此
        由组合根显式回收——这是 `T-L1-006.3` 销 L1 册 `B1` 的落点。
        """
        assert mapper is not None and machine is not None
        mapper.recycle_server(server_id)
        machine.recycle_server(server_id)

    mcp_servers = McpServerRegistry(
        store,
        gateway=gateway,
        transport_factory=mcp_transport_factory,
        env_resolver=(mcp_env_resolver if mcp_env_resolver is not None
                      else VaultEnvResolver(vault)),
        on_server_removed=_on_server_removed,
    )
    mapper = McpSkillMapper(store, servers=mcp_servers, skills=skills)
    machine = McpHubStateMachine(store, servers=mcp_servers, skills=skills, sandbox=sandbox)

    permission_source = (
        permissions
        if permissions is not None
        else SkillPermissionSource(
            skills=skill_permissions,
            mcp=mcp_servers.permissions,
            mcp_servers=mcp_servers,
            mcp_mapper=mapper,
        )
    )

    scheduler_policy = SchedulerPolicy(store)
    scheduler = Scheduler(
        store, workflows=workflows, skills=skills, sandbox=sandbox,
        runner=runner, permissions=permission_source, gateway=gateway,
        policy=scheduler_policy,
    )
    config_registry = _build_config_registry(
        store, skills=skills, scheduler_policy=scheduler_policy,
        mcp_config=machine.config, provider_hosts=provider_hosts,
    )

    return L1Runtime(
        store=store,
        endpoints=endpoints,
        vault=vault,
        gateway=gateway,
        llm=llm,
        provider_hosts=provider_hosts,
        skills=skills,
        sandbox=sandbox,
        runner=runner,
        outputs=outputs,
        workflows=workflows,
        mcp_servers=mcp_servers,
        mcp_permissions=mcp_servers.permissions,
        mcp_mapper=mapper,
        mcp_machine=machine,
        skill_permissions=skill_permissions,
        scheduler=scheduler,
        config_registry=config_registry,
    )


def _build_config_registry(
    store: Store,
    *,
    skills: SkillRegistry,
    scheduler_policy: SchedulerPolicy,
    mcp_config,
    provider_hosts: ProviderHostRegistry,
) -> ConfigRegistryFacade:
    """装配统一配置注册表门面 + L0 / L1 侧各族（[01 §7]；决策 [D-067]）。

    作用域参数族（skill / workflow）的描述体来源取 ``SkillRegistry.get_latest``；
    L2 侧 ``memory-policy`` 族**不在此注册**——L1 不 import L2（[铁律 7]），
    由 app 组合根追加注入。

    构造**不写盘**：各族只读既有条目，落值发生在 ``apply`` 时（故重复装配
    不重置任何用户配置，与 :func:`build_l1_runtime` 的既有口径一致）。
    """
    facade = ConfigRegistryFacade(store, resolve=skills.get_latest)
    facade.register_family(RetentionFamily(store))
    facade.register_family(SchedulerPolicyFamily(scheduler_policy))
    facade.register_family(McpHubPolicyFamily(mcp_config))
    facade.register_family(ProviderHostFamily(provider_hosts, store))
    return facade


def open_runtime(
    root: Path | str,
    passphrase: str,
    *,
    create: bool = False,
    llm_env: Mapping[str, str] | None = None,
    dotenv_path: Path | str | None = None,
    **kwargs: Any,
) -> L1Runtime:
    """会话入口：解锁存储**一次**并装配运行时。

    :param create: 盘上无存储时是否先初始化（``Store.create``）；缺省 ``False``
        即只打开既有存储（不存在或口令错 → L0 的 ``StorageOpenError``）
    :param llm_env: 引导装载的取值面（``LLM_API_KEY`` / ``LLM_BASE_URL`` /
        ``LLM_MODEL``）。缺省 ``None`` → 取**环境变量**（缺三键时回落 ``.env``，
        见 :func:`llm_env_from_ambient`）；传 ``{}`` 即**显式关闭**引导装载
    :param dotenv_path: ``.env`` 的位置；缺省 ``<cwd>/.env``
    :param kwargs: 透传给 :func:`build_l1_runtime`（其中 ``transport`` /
        ``llm_config`` 优先于 ``llm_env`` 的解析结果）

    口令派生每会话每存储只发生一次（``Store.open`` / ``Store.create`` 各跑一次
    PBKDF2：主密钥 + ``secrets`` 独立派生）；运行时持有该句柄供整个会话复用，
    **运行期不再重新解锁**，也不在进程内另设密钥缓存。
    """
    store = Store.create(root, passphrase) if create else Store.open(root, passphrase)
    env = llm_env if llm_env is not None else llm_env_from_ambient(dotenv_path)
    kwargs.setdefault("llm_config", LlmBootstrap.from_env(env))
    return build_l1_runtime(store, **kwargs)


def _local_now() -> datetime:
    """本机当前时间（带本地时区语义；01 §8 要求时间锚点带时区）。"""
    return datetime.now().astimezone()


# ───────────────────────── 引导装载：LLM 端点与会话（T-L1-011） ─────────────────────────

_INLINE_COMMENT = re.compile(r"\s+#")


@dataclass(frozen=True)
class LlmBootstrap:
    """引导装载的 LLM 三键（``.env`` / 环境变量；``T-L1-011``）。

    **开发 / 自用通道**——产品正典的端点配置入口是设置页
    （``T-L3-003`` / ``T-L3-004``，[01 §7](../../docs/技术架构-v2/01-平台共享契约.md)
    的配置注册表）；本类只解决「三键配好即可真实调用」，故**不做** 01 §7 登记。
    """

    api_key: str
    base_url: str
    model: str

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "LlmBootstrap | None":
        """三键**齐备**才产出；任一缺失 / 空白 → ``None``（fail-closed，不猜端点）。"""
        values = {key: str(env.get(key) or "").strip() for key in LLM_ENV_KEYS}
        if not all(values.values()):
            return None
        return cls(
            api_key=values["LLM_API_KEY"],
            base_url=values["LLM_BASE_URL"],
            model=values["LLM_MODEL"],
        )


def load_dotenv(path: Path | str) -> dict[str, str]:
    """读 ``KEY=VALUE`` 行（整行注释 / 行尾注释 / 成对引号剥除）。

    文件不存在或不可读 → 空字典——缺省路径下没有 ``.env`` 是常态，不是错误。
    """
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return {}
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if value.startswith("#"):
            value = ""
        else:
            value = _INLINE_COMMENT.split(value, maxsplit=1)[0].strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if key.strip():
            values[key.strip()] = value
    return values


def llm_env_from_ambient(dotenv_path: Path | str | None = None) -> dict[str, str]:
    """取值面：**环境变量优先**，三键不齐时回落 ``.env``（缺省 ``<cwd>/.env``）。"""
    present = {k: v for k, v in os.environ.items() if k in LLM_ENV_KEYS}
    if all(str(present.get(k, "")).strip() for k in LLM_ENV_KEYS):
        return present
    path = Path(dotenv_path) if dotenv_path is not None else Path.cwd() / DOTENV_NAME
    merged = load_dotenv(path)
    merged.update(present)
    return merged


def _bootstrap_llm_transport(
    config: LlmBootstrap,
    *,
    endpoints: EndpointRegistry,
    vault: CredentialVault,
    provider_hosts: ProviderHostRegistry,
    gateway: EgressGateway,
    post: Any | None = None,
):
    """播种三类记录（幂等）并装配真实发送器（``T-L1-011``）。

    ``base_url`` 非法（非 http/https、无主机）时 :class:`~st_agent.l0.llm.http_transport.OpenAiRoute`
    抛 ``ValueError``——这是**启动期**的配置错误，显式暴露胜过静默降级。
    """
    route = OpenAiRoute(base_url=config.base_url, model=config.model)
    _seed_llm_records(route, api_key=config.api_key, endpoints=endpoints, vault=vault,
                      provider_hosts=provider_hosts)
    routes = {LLM_PROVIDER: route}
    # 审计主机与请求面必须同源（provider_hosts_of），否则发送器拒绝发包
    return gateway.llm_transport(
        provider_hosts_of(routes),
        sender_factory=openai_compat_sender_factory(routes, post=post),
    )


def _seed_llm_records(
    route: OpenAiRoute,
    *,
    api_key: str,
    endpoints: EndpointRegistry,
    vault: CredentialVault,
    provider_hosts: ProviderHostRegistry,
) -> None:
    """端点 / 凭据 / ``provider → host`` 的**整组**幂等播种。

    **端点已存在即整组跳过**——与官方 Pack 播种同口径（``build_l1_runtime`` 的
    「只首次播种」），既不覆盖既有配置，也不留下「新凭据无人引用」这类半套记录。
    故换 Key 走凭据库 ``rotate``、换端点走 ``replace``，而不是改 ``.env`` 后指望
    它覆盖既有记录。三者为一组：端点供 ``LlmClient``、凭据供取 Key、映射供沙箱
    核对 ``net_access``。
    """
    try:
        endpoints.get(LLM_ENDPOINT_ID)
        return
    except LlmNotFoundError:
        pass
    endpoints.register(
        LLM_ENDPOINT_ID, kind="cloud", provider=LLM_PROVIDER,
        capability=dict(LLM_CAPABILITY), priority=10,
        purpose="日常（引导装载）", credential_id=LLM_CREDENTIAL_ID,
    )
    try:
        vault.get_view(LLM_CREDENTIAL_ID)
    except CredentialNotFoundError:
        vault.add(LLM_CREDENTIAL_ID, "llm_api_key", api_key)
    if provider_hosts.resolve(LLM_PROVIDER) is None:
        provider_hosts.register(LLM_PROVIDER, route.host)
