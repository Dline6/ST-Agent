"""导入校验流水线（[09 §3](../../../docs/技术架构-v2/09-生态与分享.md)）与导入留痕（[09 §5](../../../docs/技术架构-v2/09-生态与分享.md)）。

五段一条线——**格式校验 → 依赖解析 → 权限审核 → 用户批准 → 安装**，任一环不成立
即明确报错、不安装：

| 段 | 落点 | 说明 |
| --- | --- | --- |
| 1 格式校验 | [`ShareContainer.from_bytes`](container.py) | **不重造**：三段齐备 → 主版本 → 校验和 → 载荷解析，逐类显式抛 |
| 2 依赖解析 | 本模块 `_dependencies` / `_present` | 对照 `manifest` 的依赖声明查**本地 Skill 库**；缺失只**报告**（含获取途径），**不自动安装**依赖 |
| 3 权限审核 | 本模块 `_declare_and_describe` | 仅 `.stskill` 带权限声明；经 [`SkillPermissionBook.declare`](../l1/skills/permissions.py) 登记（初态 ``pending``）并给出「想做什么」的逐条措辞 |
| 4 用户批准 | `install(..., confirmed_by="user")` | 显式确认门；`.stskill` 另需**声明全部已批准**（`01 §10` fail-closed） |
| 5 安装 | 四类各自的落点 | 见下表 |

| 分享物 | 安装落点 | 身份 |
| --- | --- | --- |
| `.stskill` | [`SkillRegistry.register`](../l1/skills/registry.py)（`source="imported"` + `provenance`） | 保留分享方 `skill_id` |
| `.stflow` | [`WorkflowStore.save`](../l1/workflow/store.py) | 保留 `flow_id` |
| `.stlens` | [`LensRoster.install_shared`](../l4/roster.py) | 保留 `lens_id`（`kind` 归 `custom`） |
| `.stmem` | [`FragmentImporter.import_fragment`](../l2/memory/importer.py) | 保留 `memory_node_id` |

**失败面分工**：文件坏了 → [`ShareFormatError`](errors.py)（第 1 段抛出，本模块不吞）；
文件是好的但这次导入不成立 → [`ShareImportError`](errors.py)。

**来源留痕（[09 §5](../../../docs/技术架构-v2/09-生态与分享.md)）**：`.stskill` 的来源住在描述体的
`provenance`（契约字段）、`.stmem` 的在 L2 既有的 `memory-import/` 记录——两者**不重复记账**；
`.stflow` / `.stlens` 的本体模型（[`WorkflowDAG`](../l1/workflow/models.py) / [`Lens`](../l4/lens.py)）
**没有**溯源字段，故在本模块落 ``execution_log/share-import/<import_id>.json``（与 L2 的
`memory-import/` 同族、同属 [02 §6 的 append-only 审计语义](../../../docs/技术架构-v2/02-L0-本地优先基座.md)）。

**一处不对称，如实记下**：依赖缺失时 `.stskill` 仍可安装（注册表容忍悬空依赖，运行期由
调度 / 沙箱处置），而 `.stflow` / `.stlens` 的写面**自身**会因悬空引用拒装（[`validate_dag`](../l1/workflow/validate.py)
与 [`install_shared`](../l4/roster.py) 的 `skill_bundle` 校验）——那是归属层的既有不变量，
本模块**不替它们放宽**，只把原因如实转成 :class:`ShareImportError`。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from st_agent.contracts.capability_types import Provenance
from st_agent.contracts.identifiers import digest_id
from st_agent.eco.container import ShareContainer
from st_agent.eco.errors import ShareImportError
from st_agent.l1.skills.errors import SkillError, SkillNotFoundError
from st_agent.l1.skills.ids import parse_skill_id
from st_agent.l1.workflow.errors import WorkflowError
from st_agent.l2.memory.errors import MemoryError
from st_agent.l2.memory.models import ImportOrigin
from st_agent.l4.errors import L4Error

__all__ = [
    "HOW_TO_GET",
    "IMPORT_CONFIRMATION",
    "IMPORT_RECORD_PREFIX",
    "LEDGER_KINDS",
    "DependencyGap",
    "ImportOutcome",
    "ImportPlan",
    "ShareImportRecord",
    "ShareImporter",
    "new_import_id",
]

IMPORT_CONFIRMATION = "user"
"""安装确认门的合法取值（[09 §3](../../../docs/技术架构-v2/09-生态与分享.md) 第 4 段「用户批准」）。"""

IMPORT_RECORD_PREFIX = "share-import/"
"""``execution_log`` 分区内导入留痕的目录前缀（仅 `.stflow` / `.stlens`，见模块说明）。"""

LEDGER_KINDS: tuple[str, ...] = ("flow", "lens")
"""需要本模块落导入留痕的种类（本体模型无 `provenance` 字段的那两类）。"""

HOW_TO_GET = "经官方 Pack 更新或外部渠道获取后重新导入（产品不代理下载、不自动安装）"
"""缺失依赖的获取途径（[09 §3](../../../docs/技术架构-v2/09-生态与分享.md) 第 2 段的中性措辞）。"""

_IDENTITY_LABELS: dict[str, str] = {
    "skill": "Skill", "flow": "工作流", "lens": "视角", "mem": "记忆公开片段",
}

#: 归属层写面可能抛出的错误基类（逐层显式列出，不用裸 ``except Exception``）
_LAYER_ERRORS = (SkillError, WorkflowError, MemoryError, L4Error)


def _now() -> datetime:
    """用户本地时区当前时刻（[01 §8](../../../docs/技术架构-v2/01-平台共享契约.md)）。"""
    return datetime.now().astimezone()


def new_import_id(origin: Provenance) -> str:
    """导入来源的**确定性摘要** ID（``simp_<20 位十六进制>``；同来源重放得同一 id）。

    业务键＝分享者 + 导入时刻 + 校验和，与 [L2 的 `new_import_id`](../l2/memory/importer.py) 同口径，
    但**不复用其实现**——两者落不同分区、面向不同本体，共用前缀会让「同一 id 两处指不同记录」。
    **不新增契约 ID 类**（沿用 [01 §1](../../../docs/技术架构-v2/01-平台共享契约.md) 的确定性摘要取向）。
    """
    return digest_id(
        "simp", origin.sharer, origin.imported_at, origin.checksum, *origin.origin_chain
    )


class DependencyGap(BaseModel):
    """一条缺失的依赖（[09 §3](../../../docs/技术架构-v2/09-生态与分享.md) 第 2 段）。"""

    model_config = ConfigDict(frozen=True)

    skill_id: str = Field(min_length=1)
    how_to_get: str = HOW_TO_GET


class ImportPlan(BaseModel):
    """一次导入的审核计划（第 2、3 段的产出；**此时尚未安装**）。

    `inspect` 是只读动作，其唯一写副作用是向权限账本**登记**声明（初态 ``pending``）。
    """

    model_config = ConfigDict(frozen=True)

    kind: str
    """四类之一（`skill` / `flow` / `lens` / `mem`）。"""
    container: ShareContainer
    """已通过格式校验的容器。"""
    provenance: Provenance
    """来源追溯（[09 §5](../../../docs/技术架构-v2/09-生态与分享.md) 的四字段；导入时刻由本机落章）。"""
    permissions: tuple[str, ...] = ()
    """该导入物声明的权限（仅 `.stskill` 非空）。"""
    permission_display: tuple[str, ...] = ()
    """权限申请的逐条措辞 + 当前批准态（仅 `.stskill` 非空）。"""
    missing: tuple[DependencyGap, ...] = ()
    """缺失的依赖（已报告、**未**自动安装）。"""

    def identity(self) -> str:
        """本体的中性标识（名称 + ID；`.stmem` 无名称，用固定说法）。"""
        payload = self.container.payload
        name = getattr(payload, "name", None)
        ident = next(
            (getattr(payload, a, None) for a in ("skill_id", "flow_id", "lens_id")
             if isinstance(getattr(payload, a, None), str)),
            None,
        )
        label = _IDENTITY_LABELS.get(self.kind, self.kind)
        if isinstance(name, str) and name and ident:
            return f"{label} {name}（{ident}）"
        if ident:
            return f"{label}（{ident}）"
        return label

    def what_it_wants(self) -> tuple[str, ...]:
        """「这个能力想做什么」的逐行展示面（[09 §3](../../../docs/技术架构-v2/09-生态与分享.md) 第 3 段）。

        三段齐备、**不合并成一句**：权限申请列表 · 依赖列表 · 来源追溯。
        """
        lines = [self.identity()]
        if self.permission_display:
            lines.extend(f"权限申请：{item}" for item in self.permission_display)
        else:
            lines.append("权限申请：无声明（不经本机的文件 / 网络 / 命令出口）")
        if self.missing:
            lines.extend(f"缺失依赖：{g.skill_id} —— {g.how_to_get}" for g in self.missing)
        else:
            lines.append("依赖：本地齐备，无缺失")
        p = self.provenance
        chain = " → ".join(p.origin_chain) if p.origin_chain else "（无，未经转手）"
        lines.append(
            f"来源追溯：分享者 {p.sharer}｜导入时间 {p.imported_at}｜校验和 {p.checksum}"
            f"｜出处链 {chain}"
        )
        return tuple(lines)


class ShareImportRecord(BaseModel):
    """一次导入的留痕（落 ``execution_log`` 分区；只增不改）。

    仅用于本体模型**无 `provenance` 字段**的两类（`.stflow` / `.stlens`）——`.stskill`
    的来源在描述体、`.stmem` 的在 L2 的 `memory-import/`，**不重复记账**。
    """

    model_config = ConfigDict(frozen=True)

    import_id: str
    kind: str
    installed_id: str
    """入库对象的标识（`flow_id` / `lens_id`）。"""
    origin: Provenance
    recorded_at: datetime

    @field_validator("recorded_at")
    @classmethod
    def _recorded_at_tz(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ShareImportError("导入留痕的时刻须带时区（01 §8）")
        return v


class ImportOutcome(BaseModel):
    """一次导入的产出（[09 §3](../../../docs/技术架构-v2/09-生态与分享.md) 第 5 段的回执）。"""

    model_config = ConfigDict(frozen=True)

    kind: str
    installed_id: str
    provenance: Provenance
    missing: tuple[DependencyGap, ...] = ()
    note: str = ""
    """中性补充说明（如 `.stmem` 的入库 / 跳过条数）。"""


class ShareImporter:
    """导入校验流水线门面（[09 §3](../../../docs/技术架构-v2/09-生态与分享.md)）。

    :param store: `Store` 句柄（**仅** `.stflow` / `.stlens` 的导入留痕需要）
    :param skills: `SkillRegistry`（鸭子类型，只需 `get` / `register`）
    :param workflows: `WorkflowStore`（鸭子类型，只需 `save`）
    :param lenses: `LensRoster`（鸭子类型，只需 `install_shared`）
    :param memory: `FragmentImporter`（鸭子类型，只需 `import_fragment`）
    :param permissions: `SkillPermissionBook`（鸭子类型，只需 `declare` / `describe` /
        `approved_permissions` / `pending_permissions`）
    :param now: 取时函数（用例注入固定时钟；缺省本机当前时刻）

    未注入某类所需的门面 ⇒ 该类导入/审核**显式** `ShareImportError`（不静默降级）。
    """

    def __init__(
        self,
        *,
        store: Any = None,
        skills: Any = None,
        workflows: Any = None,
        lenses: Any = None,
        memory: Any = None,
        permissions: Any = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._skills = skills
        self._workflows = workflows
        self._lenses = lenses
        self._memory = memory
        self._permissions = permissions
        self._now = _now if now is None else now

    # ───────────────────────── 第 1–3 段：审核 ─────────────────────────

    def inspect(self, blob: bytes, *, received_from: str | None = None) -> ImportPlan:
        """读一份分享文件并算出导入计划（**只读**，不安装）。

        :param received_from: 容器 `manifest` 未记分享者时由用户补录（[09 §5](../../../docs/技术架构-v2/09-生态与分享.md)
            要求导入物记录完整来源）；两者皆无 → `ShareImportError`

        :raises ShareFormatError: 文件损坏 / 非本族 / 主版本不兼容 / 校验和不符（第 1 段）
        """
        container = ShareContainer.from_bytes(blob)
        kind = container.share_type
        payload = container.payload
        provenance = self._provenance(container, received_from)
        missing = tuple(
            DependencyGap(skill_id=sid)
            for sid in self._dependencies(container)
            if not self._present(sid)
        )
        permissions = tuple(getattr(payload, "permissions", ()) or ()) if kind == "skill" else ()
        return ImportPlan(
            kind=kind,
            container=container,
            provenance=provenance,
            permissions=permissions,
            permission_display=self._declare_and_describe(kind, payload, permissions),
            missing=missing,
        )

    # ───────────────────────── 第 4–5 段：安装 ─────────────────────────

    def install(self, plan: ImportPlan, *, confirmed_by: str) -> ImportOutcome:
        """用户确认后安装（[09 §3](../../../docs/技术架构-v2/09-生态与分享.md) 第 4、5 段）。

        `.stskill` 另有一道门：声明**全部已批准**才装（[01 §10](../../../docs/技术架构-v2/01-平台共享契约.md) fail-closed）。
        """
        if confirmed_by != IMPORT_CONFIRMATION:
            raise ShareImportError(
                f"安装须由用户确认：confirmed_by 只接受 {IMPORT_CONFIRMATION!r}，"
                f"得到 {confirmed_by!r}（09 §3 第 4 段）"
            )
        if plan.kind == "skill":
            return self._install_skill(plan)
        if plan.kind == "flow":
            return self._install_flow(plan)
        if plan.kind == "lens":
            return self._install_lens(plan)
        if plan.kind == "mem":
            return self._install_mem(plan)
        raise ShareImportError(f"未知分享物种类 {plan.kind!r}")

    # ───────────────────────── 留痕读面 ─────────────────────────

    def ledger_records(self) -> tuple[ShareImportRecord, ...]:
        """全部导入留痕（按 `import_id` 升序；`.stflow` / `.stlens` 的「导入历史」）。"""
        store = self._need(self._store, "Store", "读取导入留痕")
        out: list[ShareImportRecord] = []
        for name in store.list_files("execution_log"):
            if name.startswith(IMPORT_RECORD_PREFIX) and name.endswith(".json"):
                raw = store.get("execution_log", name)
                try:
                    out.append(ShareImportRecord.model_validate_json(raw))
                except (ValueError, UnicodeDecodeError, ValidationError) as exc:
                    raise ShareImportError(f"导入留痕 {name} 损坏无法解析：{exc}") from exc
        return tuple(sorted(out, key=lambda r: r.import_id))

    # ───────────────────────── 各段实现 ─────────────────────────

    def _provenance(self, container: ShareContainer, received_from: str | None) -> Provenance:
        """算出本次导入的来源追溯（[09 §5](../../../docs/技术架构-v2/09-生态与分享.md)）。"""
        source = container.manifest.provenance
        sharer = source.sharer or received_from
        if not sharer:
            raise ShareImportError(
                "容器 manifest 未记录分享者，也未给出 received_from——"
                "09 §5 要求每个导入物记录完整来源（分享者 / 导入时间 / 校验和）"
            )
        return Provenance(
            sharer=sharer,
            imported_at=self._now().isoformat(),
            checksum=container.checksum(),
            origin_chain=tuple(source.origin_chain),
        )

    @staticmethod
    def _dependencies(container: ShareContainer) -> tuple[str, ...]:
        """该导入物声明所需的其他 `skill_id`（[09 §3](../../../docs/技术架构-v2/09-生态与分享.md) 第 2 段的对照面）。

        取 **`manifest` 的依赖声明**（[09 §1](../../../docs/技术架构-v2/09-生态与分享.md) 的「所需 `skill_id` 清单」）
        而非本体内字段——四类同构，且导出侧已按类填好（`.stflow` 取节点引用、`.stlens`
        取 `skill_bundle`、`.stskill` 取描述体、`.stmem` 为空）。
        """
        return tuple(container.manifest.dependencies)

    def _present(self, skill_id: str) -> bool:
        """该 `skill_id` 本地是否已有（依赖解析判据）。"""
        registry = self._need(self._skills, "SkillRegistry", "依赖解析")
        try:
            registry.get(skill_id)
        except (SkillNotFoundError, KeyError):
            return False
        return True

    def _declare_and_describe(
        self, kind: str, payload: Any, permissions: Sequence[str]
    ) -> tuple[str, ...]:
        """第 3 段：登记声明（初态 `pending`）并给出「想做什么」的逐条措辞。

        只对 `.stskill` 有内容——`.stflow` / `.stlens` / `.stmem` 的本体模型不带权限字段，
        其内引用的 Skill 各有自己的声明与批准。
        """
        if kind != "skill" or not permissions:
            return ()
        book = self._need(self._permissions, "SkillPermissionBook", "权限审核")
        base, _ = parse_skill_id(payload.skill_id)
        book.declare(base, tuple(permissions))
        return tuple(book.describe(base))

    # ── 安装：四类 ──

    def _install_skill(self, plan: ImportPlan) -> ImportOutcome:
        registry = self._need(self._skills, "SkillRegistry", "安装 Skill")
        book = self._need(self._permissions, "SkillPermissionBook", "校验权限批准")
        descriptor = plan.container.payload
        base, version = parse_skill_id(descriptor.skill_id)
        declared = set(descriptor.permissions)
        approved = set(book.approved_permissions(base))
        if not declared <= approved:
            raise ShareImportError(
                f"Skill {descriptor.skill_id!r} 的权限未全部批准，不得安装："
                f"{sorted(declared - approved)}（待批准 "
                f"{sorted(book.pending_permissions(base))}）——01 §10 逐项批准，"
                "批准入口在能力安装审批面"
            )
        try:
            installed = registry.register(
                base,
                version=version,
                name=descriptor.name,
                description=descriptor.description,
                input_schema=dict(descriptor.input_schema),
                output_schema=dict(descriptor.output_schema),
                parameters=descriptor.parameters,
                dependencies=descriptor.dependencies,
                source="imported",
                provenance=plan.provenance,
                permissions=descriptor.permissions,
                offline_level=descriptor.offline_level,
                version_policy=descriptor.version_policy,
            )
        except _LAYER_ERRORS as exc:
            raise ShareImportError(f"安装 Skill {descriptor.skill_id!r} 失败：{exc}") from exc
        return ImportOutcome(
            kind="skill", installed_id=installed.skill_id,
            provenance=plan.provenance, missing=plan.missing,
            note=_gap_note(plan),
        )

    def _install_flow(self, plan: ImportPlan) -> ImportOutcome:
        store = self._need(self._workflows, "WorkflowStore", "安装工作流")
        dag = plan.container.payload
        try:
            store.save(dag)
        except _LAYER_ERRORS as exc:
            raise ShareImportError(
                f"安装工作流 {dag.flow_id!r} 失败：{exc}{_gap_hint(plan)}"
            ) from exc
        self._write_ledger("flow", dag.flow_id, plan.provenance)
        return ImportOutcome(
            kind="flow", installed_id=dag.flow_id,
            provenance=plan.provenance, missing=plan.missing, note=_gap_note(plan),
        )

    def _install_lens(self, plan: ImportPlan) -> ImportOutcome:
        roster = self._need(self._lenses, "LensRoster", "安装视角")
        lens = plan.container.payload
        try:
            installed = roster.install_shared(lens)
        except _LAYER_ERRORS as exc:
            raise ShareImportError(
                f"安装视角 {lens.lens_id!r} 失败：{exc}{_gap_hint(plan)}"
            ) from exc
        self._write_ledger("lens", installed.lens_id, plan.provenance)
        return ImportOutcome(
            kind="lens", installed_id=installed.lens_id,
            provenance=plan.provenance, missing=plan.missing, note=_gap_note(plan),
        )

    def _install_mem(self, plan: ImportPlan) -> ImportOutcome:
        importer = self._need(self._memory, "FragmentImporter", "安装记忆片段")
        origin = ImportOrigin(
            sharer=plan.provenance.sharer,
            imported_at=datetime.fromisoformat(plan.provenance.imported_at),
            checksum=plan.provenance.checksum,
            origin_chain=plan.provenance.origin_chain,
        )
        try:
            outcome = importer.import_fragment(
                plan.container.payload, origin=origin, confirmed_by=IMPORT_CONFIRMATION
            )
        except _LAYER_ERRORS as exc:
            raise ShareImportError(f"安装记忆片段失败：{exc}") from exc
        return ImportOutcome(
            kind="mem", installed_id=outcome.record.import_id, provenance=plan.provenance,
            note=f"入库 {len(outcome.record.created)} 条节点、跳过 {len(outcome.record.skipped)} 条"
                 f"（已存在即跳过，04 §8 幂等）",
        )

    # ── 留痕 ──

    def _write_ledger(self, kind: str, installed_id: str, origin: Provenance) -> None:
        """落一条导入留痕（仅 `LEDGER_KINDS`；见模块说明的「不重复记账」）。"""
        if kind not in LEDGER_KINDS:  # pragma: no cover - 调用点已限
            raise ShareImportError(f"{kind!r} 不走共享留痕（其本体或归属层已有来源记账）")
        store = self._need(self._store, "Store", f"为 {kind} 落导入留痕")
        record = ShareImportRecord(
            import_id=new_import_id(origin), kind=kind, installed_id=installed_id,
            origin=origin, recorded_at=self._now(),
        )
        store.put(
            "execution_log", f"{IMPORT_RECORD_PREFIX}{record.import_id}.json",
            record.model_dump_json().encode("utf-8"),
        )

    @staticmethod
    def _need(source: Any, label: str, what: str) -> Any:
        """门面缺失即显式失败（不静默降级为「什么都没发生」）。"""
        if source is None:
            raise ShareImportError(
                f"未注入 {label}，无法{what}——导入面的取材门面须由装配方给出"
            )
        return source


def _gap_note(plan: ImportPlan) -> str:
    """把缺失依赖写进回执（第 2 段的可见留痕；不自动安装）。"""
    if not plan.missing:
        return ""
    return f"缺失依赖 {len(plan.missing)} 项（未自动安装）：" + "、".join(
        g.skill_id for g in plan.missing
    )


def _gap_hint(plan: ImportPlan) -> str:
    """归属层写面拒绝时的补充提示（悬空引用多半是缺失依赖导致的）。"""
    if not plan.missing:
        return ""
    return "（本机缺失依赖：" + "、".join(g.skill_id for g in plan.missing) + "）"
