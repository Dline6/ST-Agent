"""导出流程（[09 §2](../../../docs/技术架构-v2/09-生态与分享.md)）与出处链追加（[09 §5](../../../docs/技术架构-v2/09-生态与分享.md)）。

本类只做**封装**：取材交给四个既有门面（L1 的 SkillRegistry / WorkflowStore、L4 的
LensRoster、L2 的 MemoryShare），容器交给 [`st_agent.eco.container`](container.py)。
本层因此不含任何存储逻辑，也不新造第二个真相源。

四类的取材与确认门：

| 分享物 | 取材面 | 确认门 |
| --- | --- | --- |
| `.stskill` | `SkillRegistry.get` | 无（09 §2：任何自建物可导出） |
| `.stflow` | `WorkflowStore.get` | 无 |
| `.stlens` | `LensRoster.get` | 无 |
| `.stmem` | `MemoryShare.plan` / `.export` | **强制三步**：过滤 → 清单 → 用户确认 |

**导出前一律过 [01 §6](../../docs/技术架构-v2/01-平台共享契约.md) 中性化校验**（09 §2）：
命名与描述命中拟人化即 :class:`~st_agent.eco.errors.ShareExportError`、**不生成文件**。
`.stmem` 的节点原文不在其列——记忆本体作数据展示不过输出校验（[D-053](../../../项目管理/决策日志.md)）。

**出处链**（09 §5）：`origin_chain` 记录**此前经手过的分享者序列**（不含本次分享者，
本次在 `provenance.sharer`）。`.stskill` 的本体自带 `provenance`（导入物），故自动沿用；
另三类的本体模型没有溯源字段，由调用方以 `previous=` 给出上一环。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Any

from st_agent.contracts.capability_types import Provenance
from st_agent.contracts.neutrality import NeutralityGuard
from st_agent.eco.card import ShareCard
from st_agent.eco.container import ShareContainer
from st_agent.eco.errors import ShareExportError
from st_agent.l2.memory.sharing import FragmentPlan

__all__ = ["ShareExporter", "appended_chain"]


def appended_chain(previous: Provenance | None) -> tuple[str, ...]:
    """出处链追加一环（09 §5「多次转手的出处链」，历史不覆盖）。

    口径：`origin_chain` ＝此前经手的分享者序列。再导出一次导入物 ⇒ 把上一环的
    分享者接到链首：``(上次分享者, *上次出处链)``。无上一环（自己创作、从未转手）
    ⇒ 空链。
    """
    if previous is None:
        return ()
    if not previous.sharer:
        return tuple(previous.origin_chain)
    return (previous.sharer, *previous.origin_chain)


class ShareExporter:
    """四类分享物的导出面（[09 §2](../../docs/技术架构-v2/09-生态与分享.md)）。

    :param skills: `SkillRegistry`（鸭子类型，只需 ``get``）
    :param workflows: `WorkflowStore`（鸭子类型，只需 ``get``）
    :param lenses: `LensRoster`（鸭子类型，只需 ``get``）
    :param memory: `MemoryShare`（鸭子类型，只需 ``plan`` / ``export``）
    :param neutrality: 中性化校验器（缺省自建 [01 §6](../../docs/技术架构-v2/01-平台共享契约.md) 守卫）
    :param now: 取时函数（用例注入固定时钟；缺省本机当前时刻）

    未注入某个取材面 ⇒ 该类导出**显式** `ShareExportError`（不静默降级、不产出空容器）。
    """

    def __init__(
        self,
        *,
        skills: Any = None,
        workflows: Any = None,
        lenses: Any = None,
        memory: Any = None,
        neutrality: NeutralityGuard | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._skills = skills
        self._workflows = workflows
        self._lenses = lenses
        self._memory = memory
        self._guard = NeutralityGuard() if neutrality is None else neutrality
        self._now = _now if now is None else now

    # ───────────────────────── 三类无确认门的导出 ─────────────────────────

    def export_skill(
        self,
        skill_id: str,
        *,
        author: str,
        sharer: str | None = None,
        previous: Provenance | None = None,
    ) -> ShareContainer:
        """导出单个 Skill 版本（`.stskill`）。

        出处链缺省取自**本体自带**的 `provenance`——导入物（`source=imported`，09 §5）
        的分享者与出处链因此在再导出时自动接上；`previous` 显式给出即覆盖之。
        """
        registry = self._need(self._skills, "SkillRegistry", "Skill")
        descriptor = registry.get(skill_id)
        self._check_neutral(descriptor.name, descriptor.description, label=f"Skill {skill_id}")
        inbound = descriptor.provenance if previous is None else previous
        return self._pack(
            descriptor,
            author=author,
            sharer=sharer,
            dependencies=tuple(descriptor.dependencies),
            previous=inbound,
        )

    def export_flow(
        self,
        flow_id: str,
        *,
        author: str,
        sharer: str | None = None,
        previous: Provenance | None = None,
    ) -> ShareContainer:
        """导出工作流的某个版本（`.stflow`）。

        依赖声明＝该版本内全部节点引用的 `skill_id`（去重升序）——即受赠方需要另备的
        能力清单（[09 §3](../../docs/技术架构-v2/09-生态与分享.md) 的依赖解析对着它判）。
        `WorkflowDAG` 无溯源字段，上一环须由 `previous` 给出。
        """
        store = self._need(self._workflows, "WorkflowStore", "工作流")
        dag = store.get(flow_id)
        self._check_neutral(dag.name, dag.description, label=f"工作流 {flow_id}")
        return self._pack(
            dag,
            author=author,
            sharer=sharer,
            dependencies=tuple(sorted({n.skill_id for n in dag.nodes})),
            previous=previous,
        )

    def export_lens(
        self,
        lens_id: str,
        *,
        author: str,
        sharer: str | None = None,
        previous: Provenance | None = None,
    ) -> ShareContainer:
        """导出自定义视角（`.stlens`）。

        依赖声明＝`skill_bundle`（该视角运行所需的一组 Skill）。`Lens` 无溯源字段，
        上一环须由 `previous` 给出。
        """
        roster = self._need(self._lenses, "LensRoster", "视角")
        lens = roster.get(lens_id)
        self._check_neutral(lens.name, lens.description, label=f"视角 {lens_id}")
        return self._pack(
            lens,
            author=author,
            sharer=sharer,
            dependencies=tuple(lens.skill_bundle),
            previous=previous,
        )

    # ───────────────────────── `.stmem`：强制三步 ─────────────────────────

    def plan_memory(self) -> FragmentPlan:
        """第 2 步：给用户看的「本次导出包含的公开信息」清单（落点在 L2）。

        与载荷**同一次读图**算出（[`MemoryShare.plan`](../../l2/memory/sharing.py)），
        故清单与随后导出的内容不会因中途改动而分叉。
        """
        share = self._need(self._memory, "MemoryShare", "记忆片段")
        return share.plan()

    def export_memory(
        self,
        *,
        author: str,
        confirmed_by: str,
        sharer: str | None = None,
        previous: Provenance | None = None,
    ) -> ShareContainer:
        """第 3 步：用户确认后生成 `.stmem`。

        :param confirmed_by: **只接受 `"user"`**——确认门由 L2 的
            [`MemoryShare.export`](../../l2/memory/sharing.py) 把关（[04 §8](../../docs/技术架构-v2/04-L2-记忆图谱.md)
            「用户确认后才生成」），未确认即由该门抛出、本层不吞。
        隐私过滤（第 1 步）与清单（第 2 步）同样在 L2——本层只负责「取材 → 装进容器」。
        """
        share = self._need(self._memory, "MemoryShare", "记忆片段")
        payload = share.export(confirmed_by=confirmed_by)
        return self._pack(
            payload, author=author, sharer=sharer, dependencies=(), previous=previous
        )

    # ───────────────────────── 卡片 ─────────────────────────

    @staticmethod
    def card_for(container: ShareContainer) -> ShareCard:
        """该容器的分享卡片（描述 + 校验和 + 下载指引；[09 §2](../../docs/技术架构-v2/09-生态与分享.md) 的「可选生成」）。"""
        return ShareCard.for_container(container)

    # ───────────────────────── 内部工具 ─────────────────────────

    def _pack(
        self,
        payload: object,
        *,
        author: str,
        sharer: str | None,
        dependencies: Sequence[str],
        previous: Provenance | None,
    ) -> ShareContainer:
        return ShareContainer.pack(
            payload,
            author=author,
            sharer=sharer,
            dependencies=dependencies,
            origin_chain=appended_chain(previous),
            created_at=self._now(),
        )

    def _check_neutral(self, name: str, description: str, *, label: str) -> None:
        """导出前的中性化拦截（09 §2；命中即不生成文件）。"""
        verdict = self._guard.check_name(name, description=description)
        if verdict.passed:
            return
        detail = "；".join(
            f"{f.kind} {f.matched!r} → {f.hint}" for f in verdict.findings
        )
        raise ShareExportError(
            f"{label} 的命名或描述未过 01 §6 中性化校验，导出已拦截：{detail}"
        )

    @staticmethod
    def _need(source: Any, label: str, what: str) -> Any:
        """取材面缺失即显式失败（不静默降级为「导出个空的」）。"""
        if source is None:
            raise ShareExportError(
                f"未注入 {label}，无法导出{what}——导出取材面须由装配方给出"
            )
        return source


def _now() -> datetime:
    """本机当前时刻（带本地时区，01 §8）。"""
    return datetime.now().astimezone()
