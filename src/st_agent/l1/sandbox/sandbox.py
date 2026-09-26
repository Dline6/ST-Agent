"""执行沙箱（T-L1-001.3；03 §1.5 + 01 §10 / §11）。其后由 T-L1-001.5 接进
``SkillRunner``：禁用拦截、``ctx.gateway`` / ``ctx.llm`` 受限包装、经 ``ctx.sandbox``
暴露会话，并补齐 LLM 出网路径（``SandboxedGateway.llm_transport`` 结构性 API +
``provider → host`` 映射按 01 §7 条目形态落盘）。

Skill 执行在受限环境内进行：文件访问限于声明的 ``local_read`` 范围、
网络限于声明的 ``net_access`` 模式、命令执行需 ``exec_command`` 审批；
运行时行为越界 → 拦截 + ``BehaviorViolation`` 事件（01 §11）+ 警示
「这个 Skill 行为异常」+ 提供禁用选项。

设计要点（④ 对齐 A3 / A7）：

- **进程内声明式 enforcement**：``SkillSandbox.session(descriptor, approved,
  trace_id)`` 携带本次执行「已声明 + 已批准」的权限集；三类出口
  （文件 / 网络 / 命令）统一核对，非 OS 级隔离。
- **声明与批准双重要求**：越界的两种形态都拦截——① 未声明（Skill 行为
  超出自身声明）；② 已声明但未获用户批准（§10 逐项批准）。拦截一律返回
  ``ResultEnvelope.validation_failed``（不抛裸异常），流式出口例外。
- **事件最小载荷**（A7）：``BehaviorViolation`` 只含 ``skill_id`` 与越界
  类别，不含被访问的资源串（路径 / 主机 / 命令）。
- **禁用选项**：``disable(skill_id)`` 后该 Skill 的会话核对全部拒绝，
  状态落 ``config`` 分区（无 ``Store`` 时仅进程内）。
- **LLM 出口（A3）**：``SkillRunner`` 注入的 ``LlmClient`` 其 transport 在
  构造期已绑定真网关，runner 无法重建它，故取**行为性代理**
  ``session.guard_llm(client)``——在 ``invoke`` 边界按「端点 provider 的
  目标主机」核对 ``net_access``；主机无法解析时**fail-closed**（拒绝）。

布局（只经 ``Store`` 读写，不直连文件系统）：
- 越界留痕 → ``execution_log`` 分区 ``sandbox-violation/<id>.json``
- 禁用标记 → ``config`` 分区 ``sandbox-disabled/<skill_id>.json``
- 提供方主机映射 → ``config`` 分区 ``llm-provider-host/<provider>.json``
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Iterator, Mapping
from datetime import datetime
from typing import Any

from st_agent.contracts.capability_types import SkillDescriptor
from st_agent.contracts.registry_types import parse_permission
from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.contracts.time_events import PlatformEvent
from st_agent.l0.llm.errors import LlmNotFoundError
from st_agent.l0.llm.models import StreamEvent
from st_agent.l1.sandbox.errors import (
    SandboxValidationError,
    SandboxViolationError,
)
from st_agent.l1.sandbox.models import (
    DISABLED_PREFIX,
    VIOLATION_PREFIX,
    GuardVerdict,
    ViolationKind,
    ViolationRecord,
    new_violation_id,
)
from st_agent.l1.sandbox.scopes import host_in_scope, path_in_scope
from st_agent.l1.skills.ids import check_skill_id

__all__ = [
    "DISABLED_PREFIX",
    "VIOLATION_PREFIX",
    "WARNING_TEXT",
    "GuardedLlmClient",
    "SandboxSession",
    "SandboxedGateway",
    "SkillSandbox",
]

WARNING_TEXT = "这个 Skill 行为异常，已拦截本次操作；可在 Skill 库中禁用该 Skill"
"""越界警示文案（中性措辞；不含被访问资源串，A7）。"""


def _now() -> datetime:
    """用户本地时区当前时刻（01 §8）。"""
    return datetime.now().astimezone()


def _scope_of(decl: str) -> str:
    """取权限声明的裸作用域（剥掉 §10 记法的 ``<...>`` 包裹）。

    §10 权限串形如 ``local_read:<path-scope>``，``parse_permission`` 按该语法
    返回**含尖括号**的作用域；范围匹配只认括号内的值。
    """
    scope = parse_permission(decl)[1]
    if scope.startswith("<") and scope.endswith(">"):
        return scope[1:-1]
    return scope


class SkillSandbox:
    """执行沙箱门面（03 §1.5）。

    :param store: 可选 ``Store`` 句柄——给了则越界留痕与禁用标记落盘
        （越界记 ``execution_log``，禁用记 ``config``）；不给则仅进程内。
    :param endpoints: 可选端点注册表（``EndpointRegistry.get(endpoint_id)``）——
        供 LLM 出口把「端点 → 提供方」解析出来（T-L1-001.5 / A3）。
    :param provider_hosts: 可选 ``provider → host`` 映射（``ProviderHostRegistry``，
        按 D-005 暂归 L1 沙箱持有）；与 ``endpoints`` 二者缺一即无法核对
        LLM 出网范围，此时 LLM 出口 **fail-closed**（一律拒绝）。
    """

    def __init__(self, store=None, *, endpoints=None, provider_hosts=None) -> None:
        self._store = store
        self._endpoints = endpoints
        self._provider_hosts = provider_hosts
        self._violations: list[ViolationRecord] = []
        self._disabled: set[str] = set()
        if store is not None:
            for name in store.list_files("config"):
                if name.startswith(DISABLED_PREFIX) and name.endswith(".json"):
                    self._disabled.add(name[len(DISABLED_PREFIX):-len(".json")])

    # ───────────────────────── 会话创建 ─────────────────────────

    def session(
        self,
        descriptor: SkillDescriptor,
        approved_permissions: Iterable[str],
        *,
        trace_id: str,
    ) -> "SandboxSession":
        """为一次执行开一个受限会话（携带声明 + 已批准权限 + 关联 trace）。"""
        if not isinstance(trace_id, str) or not trace_id.strip():
            raise SandboxValidationError(
                "session 必须携带 trace_id（01 §11：BehaviorViolation 必带关联推理链）"
            )
        return SandboxSession(
            sandbox=self, descriptor=descriptor,
            approved=frozenset(approved_permissions), trace_id=trace_id.strip(),
        )

    # ───────────────────────── 禁用选项（03 §1.5） ─────────────────────────

    def disable(self, skill_id: str) -> None:
        """禁用某 Skill（幂等）：其后续会话核对一律拒绝，直至 ``enable``。"""
        check_skill_id(skill_id)
        self._disabled.add(skill_id)
        if self._store is not None:
            self._store.put(
                "config", self._disabled_path(skill_id),
                json.dumps({"skill_id": skill_id,
                            "disabled_at": _now().isoformat()}).encode("utf-8"),
            )

    def enable(self, skill_id: str) -> None:
        """解除禁用（幂等）。"""
        check_skill_id(skill_id)
        self._disabled.discard(skill_id)
        if self._store is not None and self._disabled_path(skill_id) in self._store.list_files("config"):
            self._store.delete("config", self._disabled_path(skill_id))

    def is_disabled(self, skill_id: str) -> bool:
        """是否已被用户禁用。"""
        return skill_id in self._disabled

    def disabled_skills(self) -> tuple[str, ...]:
        """当前全部已禁用 Skill（升序）。"""
        return tuple(sorted(self._disabled))

    # ───────────────────────── 越界留痕（GWT-S1/2/3） ─────────────────────────

    def violations(self) -> tuple[ViolationRecord, ...]:
        """全部越界留痕（有 ``Store`` 时读盘，否则读进程内；按时间升序）。"""
        if self._store is None:
            return tuple(sorted(self._violations, key=lambda r: r.occurred_at))
        out: list[ViolationRecord] = []
        for name in self._store.list_files("execution_log"):
            if name.startswith(VIOLATION_PREFIX) and name.endswith(".json"):
                raw = self._store.get("execution_log", name).decode("utf-8")
                out.append(ViolationRecord.model_validate_json(raw))
        return tuple(sorted(out, key=lambda r: r.occurred_at))

    # ───────────────────────── 内部工具 ─────────────────────────

    def _record(self, record: ViolationRecord) -> None:
        self._violations.append(record)
        if self._store is not None:
            self._store.put(
                "execution_log", f"{VIOLATION_PREFIX}{record.violation_id}.json",
                record.model_dump_json().encode("utf-8"),
            )

    @staticmethod
    def _event(record: ViolationRecord) -> PlatformEvent:
        """组装 ``BehaviorViolation`` 事件（A7：载荷只含 skill_id + 越界类别）。"""
        return PlatformEvent(
            event="BehaviorViolation",
            payload={"skill_id": record.skill_id, "violation": record.kind},
            trace_id=record.trace_id,
            occurred_at=record.occurred_at,
        )

    @staticmethod
    def _disabled_path(skill_id: str) -> str:
        check_skill_id(skill_id)
        return f"{DISABLED_PREFIX}{skill_id}.json"


class SandboxSession:
    """一次执行的受限会话（03 §1.5 三类出口的统一核对点）。"""

    def __init__(
        self,
        *,
        sandbox: SkillSandbox,
        descriptor: SkillDescriptor,
        approved: frozenset[str],
        trace_id: str,
    ) -> None:
        self._sandbox = sandbox
        self._descriptor = descriptor
        self._approved = approved
        self._trace_id = trace_id

    @property
    def skill_id(self) -> str:
        return self._descriptor.skill_id

    @property
    def trace_id(self) -> str:
        return self._trace_id

    # ───────────────────────── 文件出口（GWT-S1） ─────────────────────────

    def check_file(self, path: str) -> GuardVerdict:
        """核对一次文件访问是否在声明 ``local_read`` 范围且已获批准。"""
        if self._sandbox.is_disabled(self._descriptor.skill_id):
            return self._deny("local_read", "该 Skill 已被禁用，拒绝一切文件访问")
        return self._check("local_read", path, path_in_scope, "路径")

    def read_file(self, path: str, reader: Callable[[str], Any]) -> ResultEnvelope:
        """受限读取：放行才调用 ``reader``，越界则拦截且**不执行**原操作。"""
        verdict = self.check_file(path)
        if not verdict.allowed:
            return self._blocked_envelope(verdict)
        return _wrap(reader(path), "读取结果为空")

    # ───────────────────────── 网络出口（GWT-S2） ─────────────────────────

    def check_net(self, host: str) -> GuardVerdict:
        """核对一次出网目标是否在声明 ``net_access`` 范围且已获批准。"""
        if self._sandbox.is_disabled(self._descriptor.skill_id):
            return self._deny("net_access", "该 Skill 已被禁用，拒绝一切出网")
        return self._check("net_access", host, host_in_scope, "目标主机")

    def guard_gateway(self, gateway) -> "SandboxedGateway":
        """把 ``EgressGateway`` 包成受限出口（唯一出网路径经此核对）。"""
        return SandboxedGateway(gateway, self)

    # ───────────────────────── LLM 出口（GWT-W4 / A3） ────────────────────

    def guard_llm(self, client) -> "GuardedLlmClient":
        """把 ``LlmClient`` 包成受限代理（``invoke`` 边界核对目标主机）。

        A3：因注入的 ``LlmClient`` 其 transport 在构造期已绑定真网关，
        runner 无法重建，故取**行为性代理**——越界时只产单条 ``error``
        事件，**不调用底层 client**（不触碰其 transport）。
        """
        return GuardedLlmClient(client, self)

    def resolve_llm_host(self, endpoint_id: str) -> str | None:
        """解析端点 provider 的目标主机（未配置 / 未登记 → ``None``）。"""
        endpoints = self._sandbox._endpoints
        hosts = self._sandbox._provider_hosts
        if endpoints is None or hosts is None:
            return None
        try:
            endpoint = endpoints.get(endpoint_id)
        except (KeyError, LlmNotFoundError):
            return None
        return hosts.resolve(endpoint.provider)

    def guard_llm_host(self, endpoint_id: str) -> GuardVerdict:
        """核对一次 LLM 调用的目标主机（无法解析 → fail-closed 拒绝）。

        主机无法解析有两种情形：沙箱未配置端点 / 提供方映射（装配缺口），
        或该提供方未登记主机——两者都无法证明出网落在声明范围内，故拒绝
        （沙箱是 09-生态 导入第三方 Skill 的恶意行为拦截基础，取保守侧）。
        """
        host = self.resolve_llm_host(endpoint_id)
        if host is None:
            return self._deny(
                "net_access",
                f"端点 {endpoint_id!r} 的提供方目标主机无法解析，拒绝 LLM 出网"
                "（沙箱未配置端点/提供方映射，或该提供方未登记主机）",
            )
        return self.check_net(host)

    # ───────────────────────── 命令出口（GWT-S3） ─────────────────────────

    def check_command(self) -> GuardVerdict:
        """核对命令执行是否已声明并获批准 ``exec_command``。"""
        if self._sandbox.is_disabled(self._descriptor.skill_id):
            return self._deny("exec_command", "该 Skill 已被禁用，拒绝命令执行")
        declared = [d for d in self._descriptor.permissions
                    if parse_permission(d)[0] == "exec_command"]
        if not declared:
            return self._deny("exec_command", "该 Skill 未声明 exec_command 权限")
        if not any(d in self._approved for d in declared):
            return self._deny("exec_command", "该 Skill 的 exec_command 权限未获用户批准")
        return GuardVerdict(allowed=True)

    def run_command(self, command: str, runner: Callable[[str], Any]) -> ResultEnvelope:
        """受限命令执行：放行才调用 ``runner``，越界则拦截且**不执行**原操作。"""
        verdict = self.check_command()
        if not verdict.allowed:
            return self._blocked_envelope(verdict)
        return _wrap(runner(command), "命令执行无输出")

    # ───────────────────────── 内部核对 ─────────────────────────

    def _check(
        self,
        action: ViolationKind,
        target: str,
        matcher: Callable[[str, str], bool],
        target_label: str,
    ) -> GuardVerdict:
        declared: list[str] = []
        for decl in self._descriptor.permissions:
            action_of, _scope = parse_permission(decl)
            if action_of == action and matcher(target, _scope_of(decl)):
                declared.append(decl)
        if not declared:
            return self._deny(
                action,
                f"{target_label} {target!r} 超出本 Skill 声明的 {action} 范围"
                f"（该 Skill 未声明覆盖它的权限）",
            )
        if not any(decl in self._approved for decl in declared):
            return self._deny(
                action,
                f"{target_label} {target!r} 落在声明内，但该 {action} 权限未获用户批准",
            )
        return GuardVerdict(allowed=True)

    def _deny(self, kind: ViolationKind, reason: str) -> GuardVerdict:
        record = ViolationRecord(
            violation_id=new_violation_id(),
            skill_id=self._descriptor.skill_id,
            kind=kind, warning=WARNING_TEXT,
            occurred_at=_now(), trace_id=self._trace_id,
        )
        self._sandbox._record(record)
        return GuardVerdict(
            allowed=False, kind=kind, reason=reason, warning=WARNING_TEXT,
            event=SkillSandbox._event(record), violation_id=record.violation_id,
        )

    @staticmethod
    def _blocked_envelope(verdict: GuardVerdict) -> ResultEnvelope:
        return ResultEnvelope.validation_failed(
            f"{verdict.reason}｜{verdict.warning}（越界留痕 {verdict.violation_id}）"
        )


class SandboxedGateway:
    """受限出网出口（包装 ``EgressGateway``；越界不触碰底层 sender）。

    只暴露 ``execute`` / ``stream`` / ``online``（读）；不转发 ``_sender``
    等内部属性，避免执行器绕过核对。
    """

    def __init__(self, gateway, session: SandboxSession) -> None:
        self._gateway = gateway
        self._session = session

    @property
    def online(self) -> bool:
        return bool(getattr(self._gateway, "online", True))

    def execute(self, kind: str, target_host: str, **kwargs) -> ResultEnvelope:
        """越界 → 直接返回 ``validation_failed``（不进网关，不触碰 sender）。"""
        verdict = self._session.check_net(target_host)
        if not verdict.allowed:
            return SandboxSession._blocked_envelope(verdict)
        return self._gateway.execute(kind, target_host, **kwargs)

    def stream(self, kind: str, target_host: str, **kwargs) -> Iterator[str]:
        """越界 → 抛 ``SandboxViolationError``（流式契约只认「产块/抛异常」）。"""
        verdict = self._session.check_net(target_host)
        if not verdict.allowed:
            raise SandboxViolationError(
                f"{verdict.reason}｜{verdict.warning}"
                f"（越界留痕 {verdict.violation_id}）"
            )
        return self._gateway.stream(kind, target_host, **kwargs)

    def llm_transport(self, provider_hosts: Mapping[str, str] | None = None):
        """受限 LLM 传输（A4 结构性 API；签名与 L0 ``EgressGateway.llm_transport`` 对齐）。

        返回 ``Transport`` callable：``(endpoint, prompt, key, timeout_ms) -> Iterable[str]``。
        按端点 ``provider`` 查 ``provider_hosts`` 得目标主机，先核对会话声明的
        ``net_access`` 范围再经受限 ``stream`` 发出——越界 / 主机无法解析一律抛
        ``SandboxViolationError`` 且**不触碰底层 sender**。``key`` 只透传（网关不记录）。

        供未来由组合根构建 ``LlmClient`` 的调用方使用；本任务 runner 路径走
        ``SandboxSession.guard_llm``（A3），**不依赖**此 API。
        """
        hosts = dict(provider_hosts or {})

        def _transport(endpoint, prompt: str, key: str | None, timeout_ms: int):
            host = hosts.get(endpoint.provider)
            if host is None:
                raise SandboxViolationError(
                    f"提供方 {endpoint.provider!r} 未登记目标主机，无法核对出网范围"
                    "（沙箱拒绝发起未经核对的出网）"
                )
            verdict = self._session.check_net(host)
            if not verdict.allowed:
                raise SandboxViolationError(
                    f"{verdict.reason}｜{verdict.warning}"
                    f"（越界留痕 {verdict.violation_id}）"
                )
            _ = key  # 只透传给 sender（TLS/鉴权头由 sender 组装），网关不记录
            yield from self._gateway.stream(
                "llm_call", host,
                initiator=f"llm-endpoint:{endpoint.endpoint_id}",
                purpose=f"LLM 调用（端点 {endpoint.endpoint_id}，提供方 {endpoint.provider}）",
                bytes_out=len(prompt.encode("utf-8")),
                timeout_ms=timeout_ms,
            )

        return _transport


class GuardedLlmClient:
    """受限 LLM 调用代理（T-L1-001.5；A3 行为性代理）。

    ``invoke`` 边界按「端点 provider 的目标主机」核对 ``net_access``：越界 →
    产单条 ``error`` 事件（``validation_failed``）并**不调用底层 client**；
    主机无法解析 → fail-closed 同款拒绝。只读查询（``query_usage`` 等）原样转发。
    """

    def __init__(self, client, session: SandboxSession) -> None:
        self._client = client
        self._session = session

    def invoke(self, endpoint_id: str, prompt: str, **kwargs) -> Iterator[StreamEvent]:
        """受限发起一次 LLM 调用（越界 → 单条 error 事件，不触碰底层 transport）。"""
        verdict = self._session.guard_llm_host(endpoint_id)
        if not verdict.allowed:
            yield StreamEvent(
                kind="error",
                error_envelope=SandboxSession._blocked_envelope(verdict),
            )
            return
        yield from self._client.invoke(endpoint_id, prompt, **kwargs)

    def query_usage(self, *args, **kwargs):
        """用量查询（只读，不外出网）——原样转发。"""
        return self._client.query_usage(*args, **kwargs)


def _wrap(value: Any, empty_reason: str) -> ResultEnvelope:
    """把出口调用的返回值包成信封（``None`` → ``empty``，否则 ``ok``）。"""
    if value is None:
        return ResultEnvelope.empty(empty_reason)
    return ResultEnvelope.ok(value)
