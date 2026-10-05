"""阵容门面 LensRoster（[06 §1 常设阵容·用户可增删](../../../docs/技术架构-v2/06-L4-多视角推理.md)）。

Lens 定义（[`lens.Lens`](lens.py)）的**读写门面**——持久化只经 [`Store`](../l0/storage/store.py) 的
`config` 分区、目录前缀 `lens-roster/`（仿 [`skill-registry/`](../l1/skills/registry.py)），逐 lens_id 一文件。

职责边界（任务范围）：
- 播种 7 内置视角（`seed_builtin`，幂等）
- 用户**增**自定义视角（`add_custom`：过 [01 §6](../../../docs/技术架构-v2/01-平台共享契约.md) 命名 + 描述中性化，校验 `skill_bundle` 经 `SkillRegistry` 解析存在）
- 用户**删**自定义视角（`remove`）；**内置只能停用不可删**（任务 A2：`remove` 对内置抛 `BuiltinLensError`，`set_enabled` 可停用）
- 阵容读面（`list_all` / `list_enabled` / `get` / `has_custom`）

**不含**执行编排（`T-L4-002`）、交叉对照（`T-L4-003`）、可视化（`T-L4-004`）。
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from typing import Any

from pydantic import ValidationError

from st_agent.contracts.errors import ContractViolation
from st_agent.contracts.identifiers import LensId
from st_agent.contracts.neutrality import NeutralityGuard, NeutralityVerdict
from st_agent.l4.builtin import BUILTIN_LENSES
from st_agent.l4.errors import BuiltinLensError, LensNotFoundError, LensValidationError
from st_agent.l4.lens import JudgingCriteria, Lens

__all__ = ["LENS_PREFIX", "LensRoster"]

LENS_PREFIX = "lens-roster/"
"""`config` 分区内视角定义体的目录前缀。"""

_NAME_OK = NeutralityGuard()


class LensRoster:
    """常设视角阵容门面（持久化经 Store 的 config 分区）。"""

    def __init__(
        self,
        store,
        *,
        skills: Any = None,
        name_check: Callable[..., NeutralityVerdict] | None = None,
    ) -> None:
        self._store = store
        # skills: 可选注入 SkillRegistry（或鸭子类型 get(skill_id)），用于校验 skill_bundle 存在性。
        # 不注入时 skill_bundle 仅做形态校验（Lens 构造期已做），存在性校验延后——单测可脱离 L1 装配。
        self._skills = skills
        # name_check: 中性化校验注入点（依赖倒置，同 SkillRegistry.name_neutrality_check）。
        self._name_check = name_check or _NAME_OK.check_name

    # ───────────────────────── 写入：播种 / 新增 / 删除 / 停用 ─────────

    def seed_builtin(self) -> int:
        """幂等播种 7 内置视角：已存在即跳过，返回本次新增数。"""
        added = 0
        existing = set(self._store.list_files("config"))
        for lens in BUILTIN_LENSES:
            if self._path(lens.lens_id) in existing:
                continue
            self._store.put("config", self._path(lens.lens_id), lens.model_dump_json().encode("utf-8"))
            added += 1
        return added

    def add_custom(
        self,
        *,
        name: str,
        description: str,
        skill_bundle: tuple[str, ...],
        judging_criteria: JudgingCriteria | dict[str, Any],
        confidence_policy: dict[str, Any] | None = None,
    ) -> Lens:
        """新建自定义视角并落盘。命名/描述须过 01 §6，skill_bundle 存在性经 SkillRegistry 解析（若注入）。

        `lens_id` 由本机生成（[01 §1](../../../docs/技术架构-v2/01-平台共享契约.md)：lens 由 L4 产生）。
        """
        verdict = self._name_check(name, description=description)
        if not verdict.passed:
            raise LensValidationError(
                f"视角命名/描述未过中性化校验: {name!r}（01 §6；命中 {[f.kind for f in verdict.findings]}）"
            )
        if self._skills is not None:
            for sid in skill_bundle:
                self._resolve_skill(sid)
        criteria = (
            judging_criteria
            if isinstance(judging_criteria, JudgingCriteria)
            else _build_judging(judging_criteria)
        )
        lens = _checked_lens(
            lens_id=LensId.generate().value, name=name, description=description,
            skill_bundle=tuple(skill_bundle), judging_criteria=criteria,
            confidence_policy=confidence_policy or {}, kind="custom", enabled=True,
        )
        if self._path(lens.lens_id) in set(self._store.list_files("config")):
            raise LensValidationError(f"视角 {lens.lens_id!r} 已存在，不得静默覆盖")
        self._store.put("config", self._path(lens.lens_id), lens.model_dump_json().encode("utf-8"))
        return lens

    def install_shared(self, lens: Lens | Mapping[str, Any]) -> Lens:
        """安装一份**导入**的视角定义（[09 §3 导入校验流水线的安装段](../../../docs/技术架构-v2/09-生态与分享.md)）。

        与 :meth:`add_custom` 的差别只有一处：**保留分享方的 ``lens_id``**——导入物的
        身份不该被本机改写（改了，来源追溯与二次分享的往返就断了）。两处口径与导入侧一致：

        - ``kind`` 强制归 ``custom``：导入物不是官方预置，而 [06 §1](../../../docs/技术架构-v2/06-L4-多视角推理.md)
          的 ``builtin`` 隐含「只能停用不可删」——导入物应当可删（口径同 `.stmem` 导入把
          ``source`` 改写为 ``inferred``）
        - 命名 / 描述仍过 [01 §6](../../../docs/技术架构-v2/01-平台共享契约.md) 中性化；``skill_bundle``
          存在性经 ``SkillRegistry`` 解析（若注入）

        同 ``lens_id`` 已存在 → ``LensValidationError``（不静默覆盖本机既有视角）。
        """
        fields = lens.model_dump(mode="python") if isinstance(lens, Lens) else dict(lens)
        fields["kind"] = "custom"
        candidate = _checked_lens(**fields)
        verdict = self._name_check(candidate.name, description=candidate.description)
        if not verdict.passed:
            raise LensValidationError(
                f"视角命名/描述未过中性化校验: {candidate.name!r}"
                f"（01 §6；命中 {[f.kind for f in verdict.findings]}）"
            )
        if self._skills is not None:
            for sid in candidate.skill_bundle:
                self._resolve_skill(sid)
        if self._path(candidate.lens_id) in set(self._store.list_files("config")):
            raise LensValidationError(
                f"视角 {candidate.lens_id!r} 已存在，不得静默覆盖（导入物不覆盖本机既有视角）"
            )
        self._store.put(
            "config", self._path(candidate.lens_id),
            candidate.model_dump_json().encode("utf-8"),
        )
        return candidate

    def remove(self, lens_id: str) -> None:
        """删除视角——仅自定义可删，内置拒绝（任务 A2：内置只能停用不可删）。"""
        lens = self.get(lens_id)
        if lens.kind == "builtin":
            raise BuiltinLensError(
                f"内置视角 {lens_id!r}（{lens.name}）不可删除，只能停用（set_enabled）"
            )
        self._store.delete("config", self._path(lens_id))

    def set_enabled(self, lens_id: str, enabled: bool) -> Lens:
        """启用 / 停用视角（内置与自定义皆可停用）。停用不落删除。"""
        lens = self.get(lens_id)
        updated = lens.model_copy(update={"enabled": bool(enabled)})
        self._store.put("config", self._path(lens_id), updated.model_dump_json().encode("utf-8"))
        return updated

    # ───────────────────────── 读取：全量 / 启用 / 单个 / 有无自定义 ─────────

    def list_all(self) -> tuple[Lens, ...]:
        """全部视角（含停用），按 (kind, name) 排序（内置在前稳定）。"""
        out = [self._load(name) for name in self._roster_files()]
        return tuple(sorted(out, key=lambda l: (l.kind != "builtin", l.name)))

    def list_enabled(self) -> tuple[Lens, ...]:
        """启用中的视角（编排层取数入口，`T-L4-002` 用）。"""
        return tuple(l for l in self.list_all() if l.enabled)

    def get(self, lens_id: str) -> Lens:
        """按 lens_id 取视角（不存在 → LensNotFoundError）。"""
        path = self._path(lens_id)
        try:
            raw = self._store.get("config", path)
        except KeyError as exc:
            raise LensNotFoundError(f"视角 {lens_id!r} 不存在") from exc
        return self._parse(lens_id, raw)

    def has_custom(self) -> bool:
        """是否存在任何自定义视角（空状态判定，06 §6「创建你自己的视角」入口的数据源）。"""
        return any(self._parse(name, self._store.get("config", name)).kind == "custom"
                   for name in self._roster_files())

    # ───────────────────────── 内部工具 ─────────

    def _roster_files(self) -> tuple[str, ...]:
        return tuple(n for n in self._store.list_files("config")
                     if n.startswith(LENS_PREFIX) and n.endswith(".json"))

    @staticmethod
    def _path(lens_id: str) -> str:
        LensId.of(lens_id)  # 形态校验（非法 lens_id 即拒，复用 §1 单一真相源）
        return f"{LENS_PREFIX}{lens_id}.json"

    def _load(self, fname: str) -> Lens:
        lens_id = fname[len(LENS_PREFIX):-len(".json")]
        return self._parse(lens_id, self._store.get("config", fname))

    @staticmethod
    def _parse(lens_id: str, raw: bytes) -> Lens:
        try:
            return _checked_lens(**json.loads(raw.decode("utf-8")))
        except (ValueError, UnicodeDecodeError) as exc:
            raise LensValidationError(f"视角 {lens_id!r} 记录损坏：{exc}") from exc

    def _resolve_skill(self, skill_id: str) -> None:
        """经 SkillRegistry.get 确认 skill_bundle 项存在（不存在 → 校验错）。"""
        try:
            self._skills.get(skill_id)
        except Exception as exc:
            raise LensValidationError(f"skill_bundle 含未注册 skill_id {skill_id!r}") from exc


def _checked_lens(**fields) -> Lens:
    """构造 Lens 并把 pydantic 的 ValidationError 还原成层内 LensValidationError（同 l1._checked_descriptor）。"""
    try:
        return Lens(**fields)
    except ValidationError as exc:
        raise LensValidationError(f"视角定义非法：{exc}") from exc


def _build_judging(raw: dict[str, Any]) -> JudgingCriteria:
    try:
        return JudgingCriteria(**raw)
    except ValidationError as exc:
        raise LensValidationError(f"judging_criteria 非法：{exc}") from exc
