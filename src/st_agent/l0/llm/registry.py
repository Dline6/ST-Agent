"""LLM 端点注册表（02 §4；配置本体存 ``config`` 分区）。

布局（只经 ``Store`` 读写）：
- 端点配置 → ``config`` 分区 ``llm-endpoint/<endpoint_id>.json``
  （整文件加密落盘；只存凭据**引用**，不含任何密钥明文，GWT-1/4）

``priority`` 数字越小越优先；``list_endpoints`` 按 ``(priority, endpoint_id)``
排序。更新走 ``update_priority``（只调优先级/用途）或 ``replace``（整体
替换，保留创建时间）；删除走 ``remove``。协商见 ``negotiate``（GWT-2）。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from pydantic import ValidationError

from st_agent.l0.llm.errors import LlmExistsError, LlmNotFoundError, LlmValidationError
from st_agent.l0.llm.models import (
    CapabilityRequirement,
    LlmEndpoint,
    check_endpoint_id,
)

__all__ = ["ENDPOINT_PREFIX", "EndpointRegistry"]

ENDPOINT_PREFIX = "llm-endpoint/"
"""``config`` 分区内端点配置文件的目录前缀。"""


def _now() -> datetime:
    """用户本地时区当前时刻（01 §8：时间统一用用户本地时区存储与展示）。"""
    return datetime.now().astimezone()


def _checked(**fields) -> LlmEndpoint:
    """构造端点模型，把 ``ValidationError`` 统一包成 ``LlmValidationError``。"""
    try:
        return LlmEndpoint(**fields)
    except ValidationError as exc:
        raise LlmValidationError(f"端点配置非法：{exc}") from exc


class EndpointRegistry:
    """LLM 端点注册表（02 §4 门面；配置只读经 ``Store`` 的 ``config`` 分区）。"""

    def __init__(self, store) -> None:
        self._store = store

    # ───────────────────────── 写入：注册 / 替换 / 调优先级 / 删除 ─────────

    def register(
        self,
        endpoint_id: str,
        kind: str,
        provider: str,
        capability: dict,
        priority: int = 100,
        purpose: str = "",
        credential_id: str | None = None,
        offline_level: str = "none",
        default_timeout_ms: int = 60_000,
        label: str = "",
    ) -> LlmEndpoint:
        """注册端点（已存在 → ``LlmExistsError``，更新走 ``replace``）。"""
        check_endpoint_id(endpoint_id)
        path = self._endpoint_path(endpoint_id)
        if path in self._store.list_files("config"):
            raise LlmExistsError(
                f"端点 {endpoint_id!r} 已存在；更新请用 replace，不得静默覆盖"
            )
        now = _now()
        try:
            from st_agent.l0.llm.models import EndpointCapability
            cap = capability if isinstance(capability, EndpointCapability) else EndpointCapability(**capability)
        except (ValidationError, TypeError) as exc:
            raise LlmValidationError(f"能力档位非法：{exc}") from exc
        endpoint = _checked(
            endpoint_id=endpoint_id,
            kind=kind,
            provider=provider,
            label=label,
            capability=cap,
            priority=priority,
            purpose=purpose,
            credential_id=credential_id,
            offline_level=offline_level,
            default_timeout_ms=default_timeout_ms,
            created_at=now,
            updated_at=now,
        )
        self._store.put("config", path, endpoint.model_dump_json().encode("utf-8"))
        return endpoint

    def replace(self, endpoint_id: str, **fields) -> LlmEndpoint:
        """整体替换端点配置（保留 ``created_at``；凭据引用可换，明文永不经此）。"""
        old = self._load(endpoint_id)
        merged = old.model_dump()
        merged.update(fields)
        merged["endpoint_id"] = old.endpoint_id
        merged["created_at"] = old.created_at
        merged["updated_at"] = _now()
        endpoint = _checked(**merged)
        self._store.put("config", self._endpoint_path(endpoint_id), endpoint.model_dump_json().encode("utf-8"))
        return endpoint

    def update_priority(
        self, endpoint_id: str, priority: int | None = None, purpose: str | None = None
    ) -> LlmEndpoint:
        """只调优先级/用途（GWT-1 切换优先级的主入口）。"""
        fields: dict = {}
        if priority is not None:
            fields["priority"] = priority
        if purpose is not None:
            fields["purpose"] = purpose
        if not fields:
            return self._load(endpoint_id)
        return self.replace(endpoint_id, **fields)

    def remove(self, endpoint_id: str) -> LlmEndpoint:
        """删除端点（返回删除前的配置快照作回执）。"""
        endpoint = self._load(endpoint_id)
        self._store.delete("config", self._endpoint_path(endpoint_id))
        return endpoint

    # ───────────────────────── 读取：单个 / 列表 ─────────────────────────

    def get(self, endpoint_id: str) -> LlmEndpoint:
        """读取单个端点配置（GWT-1 列表与切换的基础）。"""
        return self._load(endpoint_id)

    def list_endpoints(self) -> tuple[LlmEndpoint, ...]:
        """列出全部端点（按 ``(priority, endpoint_id)`` 排序，GWT-1）。"""
        endpoints = [
            self._load(name[len(ENDPOINT_PREFIX):-len(".json")])
            for name in self._store.list_files("config")
            if name.startswith(ENDPOINT_PREFIX) and name.endswith(".json")
        ]
        return tuple(sorted(endpoints, key=lambda e: (e.priority, e.endpoint_id)))

    # ───────────────────────── 协商（GWT-2） ─────────────────────────

    def negotiate(
        self, endpoint_id: str, requirement: CapabilityRequirement | dict
    ) -> tuple[bool, str]:
        """能力协商：调用需求能否被该端点满足。

        返回 ``(满足与否, 原因)``：满足 → ``(True, "")``；上下文不足或缺结构化
        输出 → ``(False, 人可读降级原因)``。调用方据此降级，不得静默调用高档。
        """
        endpoint = self._load(endpoint_id)
        if isinstance(requirement, dict):
            try:
                requirement = CapabilityRequirement(**requirement)
            except ValidationError as exc:
                raise LlmValidationError(f"能力需求非法：{exc}") from exc
        cap = endpoint.capability
        if requirement.min_context_tokens > cap.max_context_tokens:
            return (False, (
                f"端点 {endpoint_id!r} 上下文不足（需求 {requirement.min_context_tokens} "
                f"> 供给 {cap.max_context_tokens}）；请降级需求或切换端点"
            ))
        if requirement.needs_structured_output and not cap.supports_structured_output:
            return (False, (
                f"端点 {endpoint_id!r} 不支持结构化输出；请关闭该需求或切换端点"
            ))
        return (True, "")

    def pick(
        self, requirement: CapabilityRequirement | dict, purpose: str = ""
    ) -> LlmEndpoint | None:
        """按优先级挑第一个满足需求的端点（``purpose`` 非空时先按用途过滤）。

        无满足者 → ``None``（调用方走 ``ResultEnvelope.unavailable`` 显式降级）。
        """
        if isinstance(requirement, dict):
            try:
                requirement = CapabilityRequirement(**requirement)
            except ValidationError as exc:
                raise LlmValidationError(f"能力需求非法：{exc}") from exc
        for endpoint in self.list_endpoints():
            if purpose and endpoint.purpose != purpose:
                continue
            ok, _ = self.negotiate(endpoint.endpoint_id, requirement)
            if ok:
                return endpoint
        return None

    # ───────────────────────── 内部工具 ─────────────────────────

    @staticmethod
    def _endpoint_path(endpoint_id: str) -> str:
        check_endpoint_id(endpoint_id)
        return f"{ENDPOINT_PREFIX}{endpoint_id}.json"

    def _load(self, endpoint_id: str) -> LlmEndpoint:
        """读回端点配置（不存在 → ``LlmNotFoundError``）。"""
        check_endpoint_id(endpoint_id)
        try:
            raw = self._store.get("config", self._endpoint_path(endpoint_id))
        except KeyError as exc:
            raise LlmNotFoundError(f"端点 {endpoint_id!r} 不存在") from exc
        try:
            return _checked(**json.loads(raw.decode("utf-8")))
        except (ValueError, UnicodeDecodeError) as exc:
            raise LlmNotFoundError(f"端点 {endpoint_id!r} 记录损坏无法解析") from exc
