"""MCP tool → Skill 自动映射与版本化（T-L1-002.2；03 §5.2）。

职责与边界：

- **自动映射**：一台**已批准权限**的 MCP Server 的每个 tool 自动注册为 Skill
  （``source="mcp-mapped"``），元数据从 MCP schema 派生——``input_schema`` /
  ``output_schema`` 取 tool 的 MCP schema 原样（落在 01 §2 最小子集内），
  ``name`` 过 §6 中性化校验。
- **幂等与增量**：重复装载不重复注册、不报错；Server 新增 tool 时只注册新增者，
  既有映射不受影响。
- **版本化与重映射**：映射关系按 01 §9 版本化，按 tool 契约**指纹**判断是否需要
  重新映射——契约变了只标 ``pending-remap`` 并给出显式变化说明，**不静默覆盖**
  已注册的描述体；用户确认后经 ``apply_remap`` 落成新版本。
- **禁用联动**：Server 禁用（或记录已移除）→ 其派生 Skill 立即不在
  ``available_skills()`` 中，映射记录保留，故重新启用即恢复。
- **不做网络动作**：取 tool 列表复用 ``McpServerRegistry.test_connection``
  （内做握手 + ``tools/list``），本模块不持有传输。

布局（只经 ``Store`` 读写）：
- 映射记录 → ``config`` 分区 ``mcp-mapping/<server_id>/<tool>.json``
- Skill 描述体 → 复用 ``config`` 分区 ``skill-registry/<skill_id>.json``
  （T-L1-001.1 通道，本模块**不另立存储**）

fingerprint 口径：只含 schema、**不含** ``description``（GWT-3 的判据是「MCP
schema 指纹」）。总指纹由输入面与输出面两个分侧指纹合成，故变化说明能落到
具体哪一面变化。
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from st_agent.contracts.capability_types import Provenance, SkillDescriptor
from st_agent.contracts.registry_types import SemVer
from st_agent.contracts.schema_check import check_link
from st_agent.l1.mcp.errors import (
    McpConnectionError,
    McpError,
    McpMappingError,
    McpMappingNotFoundError,
    McpPermissionError,
    McpValidationError,
)
from st_agent.l1.mcp.ids import check_server_id
from st_agent.l1.mcp.models import ToolSpec
from st_agent.l1.mcp.registry import McpServerRegistry
from st_agent.l1.skills.errors import SkillValidationError
from st_agent.l1.skills.ids import base_of, parse_skill_id, skill_id_for
from st_agent.l1.skills.registry import SkillRegistry

__all__ = [
    "BASE_LIMIT",
    "MAPPING_PREFIX",
    "TOOL_NAME_PATTERN",
    "MappingStatus",
    "McpSkillMapper",
    "McpToolMapping",
    "check_tool_name",
    "tool_fingerprints",
]

MAPPING_PREFIX = "mcp-mapping/"
"""``config`` 分区内映射记录的目录前缀。"""

MappingStatus = Literal["active", "pending-remap", "vanished"]
"""映射状态：``active`` 与已注册描述体一致；``pending-remap`` 契约已变、待用户确认；
``vanished`` 该 tool 已不在 Server 的 tool 列表中（记录保留，tool 回归即自愈）。"""

TOOL_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
"""tool 名形状——同时是映射记录的**落盘路径段**（``check_tool_name``）。"""

BASE_LIMIT = 61
"""``skill_id`` 中 ``sk_`` 之后到 ``_v`` 之前的注册名段长度上限（T-L1-001.1 口径）。"""

_INITIAL_VERSION = SemVer(major=1, minor=0)


def check_tool_name(value: str) -> str:
    """校验 tool 名（非法 → ``McpValidationError``）。

    该名同时用作 ``mcp-mapping/<server_id>/<tool>.json`` 的路径段，故须为安全
    相对路径段：拒空白、路径分隔符、越界形态与 ``..``（后者由首字符须为字母
    数字排除）。
    """
    if not isinstance(value, str) or not TOOL_NAME_PATTERN.match(value):
        raise McpValidationError(
            f"非法 MCP tool 名 {value!r}（须以字母数字开头，仅含字母/数字/_/./-，"
            "≤128 字符——该名同时用作映射记录文件名）"
        )
    return value


# ───────────────────────── 指纹 ─────────────────────────


def _canonical(value: Any) -> str:
    """规范化 JSON 文本（键序稳定 → 同构 schema 得同一指纹）。"""
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str
    )


def _digest(value: Any) -> str:
    """规范化 JSON 的 sha256 十六进制指纹。"""
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def tool_fingerprints(spec: ToolSpec) -> tuple[str, str, str]:
    """算 tool 契约的 ``(总指纹, 输入面指纹, 输出面指纹)``。

    分侧指纹只用于把变化说明落到具体哪一面；总指纹是判定「是否需要重新映射」
    的单一依据。
    """
    input_fp = _digest(spec.input_schema)
    output_fp = _digest(spec.output_schema)
    combined = _digest({"input": spec.input_schema, "output": spec.output_schema})
    return combined, input_fp, output_fp


def _describe_change(baseline: "McpToolMapping", input_fp: str, output_fp: str) -> str:
    """把契约变化落到具体哪一面（GWT-3 的「变化说明」）。"""
    changed = [
        name
        for name, old, new in (
            ("input_schema", baseline.input_fingerprint, input_fp),
            ("output_schema", baseline.output_fingerprint, output_fp),
        )
        if old != new
    ]
    return (
        f"tool 契约已变化（{'、'.join(changed) or 'schema'}）——"
        "映射仍指向旧描述体，须确认后重新映射"
    )


def _now() -> datetime:
    """用户本地时区当前时刻（01 §8）。"""
    return datetime.now().astimezone()


# ───────────────────────── 映射记录 ─────────────────────────


class McpToolMapping(BaseModel):
    """一条 tool → Skill 的映射记录（03 §5.2；GWT-4）。

    三个指纹是**已注册描述体所源自的基线契约**；契约变化时基线不动，只在
    ``status`` 与 ``change_note`` 上体现，故「不静默覆盖」可复查。
    ``mapping_version`` 与 Skill 的注册版本同步（01 §9 主.次）。
    """

    model_config = ConfigDict(frozen=True)

    server_id: str
    tool: str
    skill_id: str
    fingerprint: str
    input_fingerprint: str
    output_fingerprint: str
    mapping_version: str
    status: MappingStatus
    change_note: str = ""
    updated_at: datetime

    @model_validator(mode="after")
    def _shape(self) -> "McpToolMapping":
        check_server_id(self.server_id)
        check_tool_name(self.tool)
        if self.updated_at.tzinfo is None:
            raise McpValidationError("updated_at 必须带时区语义（01 §8）")
        if self.status in ("pending-remap", "vanished") and not self.change_note.strip():
            raise McpValidationError(f"{self.status} 必须给出变化说明（GWT-3 显式提示）")
        if self.status == "active" and self.change_note.strip():
            raise McpValidationError("active 映射不得带变化说明")
        return self


# ───────────────────────── 映射门面 ─────────────────────────


class McpSkillMapper:
    """MCP tool → Skill 映射门面（03 §5.2）。

    :param store: ``Store`` 句柄（映射记录只写 ``config`` 分区）
    :param servers: ``McpServerRegistry``——Server 记录、权限批准态、tool 列表
    :param skills: ``SkillRegistry``——描述体的注册与版本发布（复用 T-L1-001.1 通道）
    """

    def __init__(self, store, *, servers: McpServerRegistry, skills: SkillRegistry) -> None:
        self._store = store
        self._servers = servers
        self._skills = skills

    # ───────────────────────── 装载 / 增量同步 ─────────────────────────

    def sync_tools(self, server_id: str) -> tuple[McpToolMapping, ...]:
        """装载 / 增量同步该 Server 的全部 tool（GWT-1 / GWT-2 / GWT-3）。

        - 权限未全部批准 → ``McpPermissionError``（A4：装载不自动批准）
        - 连接 / 握手失败 → ``McpConnectionError``（失败显式化）
        - 新 tool → 注册 Skill（``source="mcp-mapped"``）并落映射记录
        - 指纹未变 → 原样保留（幂等，不重复注册、不报错）
        - 指纹变了 → 只标 ``pending-remap``，**不触碰**已注册描述体
        - tool 从本次 ``tools/list`` 消失 → 标 ``vanished``（记录保留，回归即自愈）

        返回该 Server 同步后的全部映射记录（按 tool 升序）。
        """
        sid = check_server_id(server_id)
        record = self._servers.get_server(sid)
        self._require_approved(sid)
        specs = self._fetch_tools(sid)
        for spec in specs:
            check_tool_name(spec.name)
            self._sync_one(sid, spec, record.permissions)
        self._mark_vanished(sid, {spec.name for spec in specs})
        return self.mappings(sid)

    def _sync_one(
        self, server_id: str, spec: ToolSpec, permissions: tuple[str, ...]
    ) -> None:
        fingerprint, input_fp, output_fp = tool_fingerprints(spec)
        existing = self._load(server_id, spec.name)
        if existing is None:
            skill_id = self._register_skill(server_id, spec, permissions)
            self._save(
                McpToolMapping(
                    server_id=server_id, tool=spec.name, skill_id=skill_id,
                    fingerprint=fingerprint, input_fingerprint=input_fp,
                    output_fingerprint=output_fp,
                    mapping_version=f"{_INITIAL_VERSION.major}.{_INITIAL_VERSION.minor}",
                    status="active", updated_at=_now(),
                )
            )
            return
        if existing.fingerprint == fingerprint:
            if existing.status != "active":  # 契约回到基线 → 自愈
                self._save(existing.model_copy(update={
                    "status": "active", "change_note": "", "updated_at": _now(),
                }))
            return
        self._save(existing.model_copy(update={
            "status": "pending-remap",
            "change_note": _describe_change(existing, input_fp, output_fp),
            "updated_at": _now(),
        }))

    def _mark_vanished(self, server_id: str, present: set[str]) -> None:
        """本次 ``tools/list`` 未出现的既有 tool → 标 ``vanished``（不回收，回归即自愈）。"""
        for mapping in self.mappings(server_id):
            if mapping.tool in present or mapping.status == "vanished":
                continue
            self._save(mapping.model_copy(update={
                "status": "vanished",
                "change_note": (
                    f"tool {mapping.tool!r} 已不在 Server {server_id!r} 的 tool 列表中——"
                    "映射与描述体保留，tool 回归即自愈"
                ),
                "updated_at": _now(),
            }))

    def _register_skill(
        self, server_id: str, spec: ToolSpec, permissions: tuple[str, ...]
    ) -> str:
        """把一个 tool 注册为 Skill（幂等：已在册则直接返回其 id）。"""
        base = self._base_for(server_id, spec.name)
        skill_id = skill_id_for(base, _INITIAL_VERSION)
        if skill_id in {d.skill_id for d in self._skills.list_all()}:
            claim = self._claimant_of(skill_id)
            if claim is not None and (claim.server_id, claim.tool) != (server_id, spec.name):
                raise McpMappingError(
                    f"Skill 标识 {skill_id!r} 已被 tool {claim.tool!r}"
                    f"（Server {claim.server_id!r}）占用——Server / tool 名归一后撞车，"
                    "请改用可区分的标识"
                )
            return skill_id
        try:
            self._skills.register(
                base, version=f"{_INITIAL_VERSION.major}.{_INITIAL_VERSION.minor}",
                name=spec.name,
                description=self._describe_skill(server_id, spec),
                input_schema=spec.input_schema,
                output_schema=spec.output_schema,
                parameters=(), dependencies=(),
                source="mcp-mapped", provenance=Provenance(),
                permissions=permissions,
                offline_level="none", version_policy="follow-latest",
            )
        except SkillValidationError as exc:
            raise McpValidationError(
                f"MCP tool {spec.name!r}（Server {server_id!r}）无法映射为 Skill：{exc}"
            ) from exc
        return skill_id

    @staticmethod
    def _describe_skill(server_id: str, spec: ToolSpec) -> str:
        """描述体（SkillDescriptor.description 必填；MCP 未给描述时派生中性文案）。"""
        if spec.description.strip():
            return spec.description.strip()
        return f"MCP Server {server_id} 提供的 tool：{spec.name}"

    @staticmethod
    def _base_for(server_id: str, tool: str) -> str:
        """按 A1 拼 Skill base：``sk_mcp_<server>_<tool>``（小写安全段；超长显式拒）。"""
        raw = f"mcp_{_segment(server_id)}_{_segment(tool)}"
        if len(raw) > BASE_LIMIT:
            raise McpValidationError(
                f"MCP tool {tool!r}（Server {server_id!r}）派生出的 Skill 注册名超长"
                f"（{len(raw)} > {BASE_LIMIT}）：请改用更短的 Server 标识"
            )
        base = f"sk_{raw}"
        try:
            skill_id_for(base, _INITIAL_VERSION)
        except SkillValidationError as exc:
            raise McpValidationError(f"MCP tool {tool!r} 的 Skill 标识非法：{exc}") from exc
        return base

    # ───────────────────────── 重新映射 ─────────────────────────

    def apply_remap(self, server_id: str, tool: str) -> McpToolMapping:
        """确认重新映射：按 01 §9 落成新版本（GWT-3 下半；④ 定案 A 方案）。

        版本判定**只经** ``contracts.schema_check.check_link``（D-009 不得另造
        匹配规则）：旧 ``input_schema`` 与新 ``input_schema`` 双向任一不兼容
        （下游 ``required`` 未被上游 ``properties`` 声明）→ 主版本，否则次版本。
        主版本变更由 ``publish_version`` 自然产生 ``skill-update/`` 待检查标记，
        引用方须人工确认后才升级（§9）。
        """
        sid = check_server_id(server_id)
        check_tool_name(tool)
        existing = self._load(sid, tool)
        if existing is None:
            raise McpMappingNotFoundError(f"MCP Server {sid!r} 未装载过 tool {tool!r}")
        if existing.status != "pending-remap":
            raise McpMappingError(f"tool {tool!r}（Server {sid!r}）无待重新映射事项")
        self._require_approved(sid)
        spec = self._find_tool(sid, tool)
        fingerprint, input_fp, output_fp = tool_fingerprints(spec)
        if existing.fingerprint == fingerprint:
            raise McpMappingError(
                f"tool {tool!r} 的契约已回到基线，无变化可落：请直接 sync_tools"
            )
        base, old_ver = parse_skill_id(existing.skill_id)
        old_input = self._skills.get(existing.skill_id).input_schema
        incompatible = not (
            check_link(spec.input_schema, old_input).compatible
            and check_link(old_input, spec.input_schema).compatible
        )
        new_ver = (
            SemVer(major=old_ver.major + 1, minor=0)
            if incompatible
            else SemVer(major=old_ver.major, minor=old_ver.minor + 1)
        )
        self._skills.publish_version(
            base, version=new_ver,
            changelog=f"MCP tool {tool}（Server {sid}）契约变化，确认重新映射",
            impact=(
                "输入契约不兼容，引用方须复核调用参数后再升级"
                if incompatible
                else "兼容增强，引用方无需改动"
            ),
            input_schema=spec.input_schema,
            output_schema=spec.output_schema,
            description=self._describe_skill(sid, spec),
        )
        updated = existing.model_copy(update={
            "skill_id": skill_id_for(base, new_ver),
            "fingerprint": fingerprint, "input_fingerprint": input_fp,
            "output_fingerprint": output_fp,
            "mapping_version": f"{new_ver.major}.{new_ver.minor}",
            "status": "active", "change_note": "", "updated_at": _now(),
        })
        self._save(updated)
        return updated

    # ───────────────────────── 回收 ─────────────────────────

    def recycle_server(self, server_id: str) -> tuple[str, ...]:
        """回收一台 Server 的全部映射记录与派生 Skill（GWT-3；T-L1-007）。

        适用「Server 已被 ``McpServerRegistry.remove_server`` 显式移除」这类持久性
        动作：删 ``mcp-mapping/<server_id>/*.json`` 并反注册其派生 Skill（base 的
        **全部版本**），返回被反注册的 ``skill_id``（升序）。

        不调用 ``get_server``（注册记录此时已不存在）；非 ``mcp-mapped`` 来源的
        Skill 不受影响。
        """
        sid = check_server_id(server_id)
        mappings = self.mappings(sid)
        removed: list[str] = []
        for base in sorted({base_of(m.skill_id) for m in mappings}):
            if self._skills.get_latest(base) is not None:
                removed.extend(self._skills.unregister(base))
        for mapping in mappings:
            self._store.delete("config", self._path(mapping.server_id, mapping.tool))
        return tuple(removed)

    # ───────────────────────── 查询 ─────────────────────────

    def mappings(self, server_id: str) -> tuple[McpToolMapping, ...]:
        """该 Server 的全部映射记录（按 tool 升序；GWT-4）。"""
        sid = check_server_id(server_id)
        prefix = f"{MAPPING_PREFIX}{sid}/"
        out = [
            self._parse(self._store.get("config", name))
            for name in self._store.list_files("config")
            if name.startswith(prefix) and name.endswith(".json")
        ]
        return tuple(sorted(out, key=lambda m: m.tool))

    def pending_remap(self) -> tuple[McpToolMapping, ...]:
        """全部待重新映射的映射记录（含 Server 与变化说明；GWT-3）。

        只读映射记录，**不重探** Server（A7：面板刷新不得有副作用）。
        """
        out = [
            m
            for m in self._all_mappings()
            if m.status == "pending-remap"
        ]
        return tuple(sorted(out, key=lambda m: (m.server_id, m.tool)))

    def available_skills(self) -> tuple[SkillDescriptor, ...]:
        """当前可用的 Skill 列表（GWT-5）。

        = ``SkillRegistry.list_all()`` 减去**不可用 Server** 派生者。「不可用」＝
        Server 已禁用，或注册记录已被移除（映射记录此时仍保留，故只能在这一层
        判定）。非 MCP 来源的 Skill 不受影响。
        """
        usable = {r.server_id for r in self._servers.list_servers() if r.enabled}
        hidden = self._unavailable_skill_ids(usable)
        return tuple(d for d in self._skills.list_all() if d.skill_id not in hidden)

    def _unavailable_skill_ids(self, usable: set[str]) -> set[str]:
        """不可用 Skill 标识：不可用 Server 派生者 + 已标 ``vanished`` 者（全部版本）。"""
        return {
            m.skill_id
            for m in self._all_mappings()
            if m.server_id not in usable or m.status == "vanished"
        }

    # ───────────────────────── 内部工具 ─────────────────────────

    def _require_approved(self, server_id: str) -> None:
        """装载 / 重新映射前置的权限闸门（A4：装载不自动批准）。"""
        blocking = [
            f"{a.permission}（{'待批准' if a.decision == 'pending' else '已拒绝'}）"
            for a in self._servers.permissions.approvals(server_id)
            if a.decision != "approved"
        ]
        if blocking:
            raise McpPermissionError(
                f"MCP Server {server_id!r} 尚有未批准的权限，不得装载映射："
                + "、".join(blocking)
            )

    def _fetch_tools(self, server_id: str) -> tuple[ToolSpec, ...]:
        """取该 Server 的 tool 列表（复用连接测试路径：握手 + ``tools/list``）。"""
        result = self._servers.test_connection(server_id)
        if not result.ok:
            raise McpConnectionError(
                f"MCP Server {server_id!r} 连接失败，无法装载映射：{result.reason}"
            )
        return result.tools

    def _find_tool(self, server_id: str, tool: str) -> ToolSpec:
        for spec in self._fetch_tools(server_id):
            if spec.name == tool:
                return spec
        raise McpMappingError(f"MCP Server {server_id!r} 已不再提供 tool {tool!r}")

    def _all_mappings(self) -> tuple[McpToolMapping, ...]:
        """全部映射记录（跨 Server，按 ``(server_id, tool)`` 升序）。"""
        out = [
            self._parse(self._store.get("config", name))
            for name in self._store.list_files("config")
            if name.startswith(MAPPING_PREFIX) and name.endswith(".json")
        ]
        return tuple(sorted(out, key=lambda m: (m.server_id, m.tool)))

    def _claimant_of(self, skill_id: str) -> McpToolMapping | None:
        """已被某条映射记录占用的 ``skill_id``（防跨 Server 归一撞车）。"""
        for m in self._all_mappings():
            if m.skill_id == skill_id:
                return m
        return None

    def _load(self, server_id: str, tool: str) -> McpToolMapping | None:
        path = self._path(server_id, tool)
        if path not in self._store.list_files("config"):
            return None
        return self._parse(self._store.get("config", path))

    def _save(self, mapping: McpToolMapping) -> None:
        path = self._path(mapping.server_id, mapping.tool)
        self._store.put("config", path, mapping.model_dump_json().encode("utf-8"))

    @staticmethod
    def _path(server_id: str, tool: str) -> str:
        return f"{MAPPING_PREFIX}{check_server_id(server_id)}/{check_tool_name(tool)}.json"

    @staticmethod
    def _parse(raw: bytes) -> McpToolMapping:
        try:
            return McpToolMapping(**json.loads(raw.decode("utf-8")))
        except McpError:
            raise
        except (ValueError, UnicodeDecodeError, ValidationError) as exc:
            raise McpValidationError(f"MCP 映射记录损坏无法解析：{exc}") from exc


def _segment(value: str) -> str:
    """归一为一个 Skill 标识安全段（小写；非 ``[a-z0-9_.-]`` 一律转 ``_``）。"""
    return re.sub(r"[^a-z0-9_.-]", "_", value.lower())
