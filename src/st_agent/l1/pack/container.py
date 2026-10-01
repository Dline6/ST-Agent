"""官方资源包容器与统一装载入口（01 §13）。

容器聚合类型化资源条目（:class:`ResourceEntry`），按 ``kind`` 分派给各消费方的
**loader**；装载入口 :func:`load_official_pack` 即 [01 §13](../../../docs/技术架构-v2/01-平台共享契约.md)
的「按 kind 分发」落地。

三条语义（01 §13）：

- **「kind → loader」分派**——未注册 loader 的 kind **显式拒**（:class:`PackLoaderMissingError`），
  不静默忽略、不猜测
- **幂等**——同 ``(kind, version)`` 重复装载**不重复调用 loader、不静默覆盖**
- **回退在消费方**——容器不内置缺省；各消费方保留自有缺省（Pack 缺席时行为与内置缺省一致）
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping

from st_agent.l1.pack.errors import PackLoaderMissingError
from st_agent.l1.pack.resources import OFFICIAL_RESOURCES, ResourceEntry

__all__ = [
    "Loader",
    "OfficialPack",
    "load_official_pack",
]

Loader = Callable[[ResourceEntry], object]
"""某 kind 的 loader：资源条目 → 该 kind 的原生对象（形态由该 kind 的归属层定）。"""


class OfficialPack:
    """官方资源容器（[01 §13](../../../docs/技术架构-v2/01-平台共享契约.md)）。

    :param entries: 初始条目；``None`` 即用 L1 自有的 :data:`OFFICIAL_RESOURCES`。
        其余 kind 的条目由归属层经 :meth:`add` 贡献（装配期）。
    """

    def __init__(self, entries: Iterable[ResourceEntry] | None = None) -> None:
        self._entries: dict[tuple[str, object], ResourceEntry] = {}
        self._loaded: set[tuple[str, object]] = set()
        for entry in (OFFICIAL_RESOURCES if entries is None else entries):
            self.add(entry)

    # ───────────────────────── 条目 ─────────────────────────

    def add(self, entry: ResourceEntry) -> ResourceEntry:
        """登记一条条目；同 ``(kind, version)`` 已存在即**幂等跳过**（不覆盖）。"""
        if entry.key in self._entries:
            return self._entries[entry.key]
        self._entries[entry.key] = entry
        return entry

    def entries(self, *, kind: str | None = None) -> tuple[ResourceEntry, ...]:
        """列条目（可按 kind 过滤；按登记顺序）。"""
        items = tuple(self._entries.values())
        if kind is None:
            return items
        return tuple(e for e in items if e.kind == kind)

    def kinds(self) -> tuple[str, ...]:
        """容器内出现过的 kind（按首次登记顺序去重）。"""
        seen: dict[str, None] = {}
        for entry in self._entries.values():
            seen.setdefault(entry.kind, None)
        return tuple(seen)

    # ───────────────────────── 装载 ─────────────────────────

    def load(self, loaders: Mapping[str, Loader]) -> tuple[object, ...]:
        """按 kind 分发条目 → 本次**新装载**的 loader 结果（幂等：已装载的不再调用）。

        任一条目的 kind 无 loader → :class:`PackLoaderMissingError`（显式拒）。
        """
        results: list[object] = []
        for entry in self._entries.values():
            if entry.key in self._loaded:
                continue                                   # 幂等：同 kind 同 version 不再装载
            loader = loaders.get(entry.kind)
            if loader is None:
                raise PackLoaderMissingError(entry.kind)
            results.append(loader(entry))
            self._loaded.add(entry.key)
        return tuple(results)


def load_official_pack(
    pack: OfficialPack, loaders: Mapping[str, Loader]
) -> tuple[object, ...]:
    """统一装载入口（[01 §13](../../../docs/技术架构-v2/01-平台共享契约.md)）：委托 :meth:`OfficialPack.load`。"""
    return pack.load(loaders)
