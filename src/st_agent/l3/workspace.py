"""工作区（钉住的持久组件）的读写面（[05 §6](../../../docs/技术架构-v2/05-L3-对话主入口.md)；[01 §5](../../../docs/技术架构-v2/01-平台共享契约.md) / [§12](../../../docs/技术架构-v2/01-平台共享契约.md)）。

生成式 UI 的产物可「钉」到工作区，走出对话流成为**持久**组件（[05 §6](../../../docs/技术架构-v2/05-L3-对话主入口.md)
「生成物可『钉』到工作区 → 变为持久组件」）。本模块是它在 L3 侧的唯一落点。

三件事：

- **落盘**——L0 ``config`` 分区的 ``workspace/pins/<description_id>.json``，一行一文件
  （同 [`l2/memory/config_store`](../l2/memory/config_store.py) 的取向）。``description_id``
  正是 [01 §1](../../../docs/技术架构-v2/01-平台共享契约.md) 给它的用途：「『钉』到工作区后的
  **寻址单位**」——故它同时是文件名与刷新 / 取消钉住的入参。
- **记录数据绑定与 ``as_of``**——[05 §6](../../../docs/技术架构-v2/05-L3-对话主入口.md) 要求钉住物
  「记录数据绑定与 ``as_of``，数据更新时可刷新」。故一条钉住 ＝ 一份 [01 §12](../../../docs/技术架构-v2/01-平台共享契约.md)
  描述（``slots`` 是已解析的绑定值、``as_of`` 是快照时刻）＋ 可选的**来源面键** ``source_key``。
- **重解析**——:meth:`WorkspaceFacade.refresh` 按 ``source_key`` 取**解析器**重解析，
  以**同一个 `description_id`** 换掉 ``slots`` / ``as_of``。**解析器表由组合根注入**
  （真取数口住组合根，同 ``MemoryPositionsFacade`` 的形态）；钉住项**没有** ``source_key``、
  或该键在本进程**未接取数口**，都回 ``unavailable`` ＋ 点名——**不伪造一份「刷新过」的旧数据**
  （[01 §5](../../docs/技术架构-v2/01-平台共享契约.md) 六态不混用）。

**为何来源面键是枚举而不是 URL**：描述一旦能表达「调哪个端点」，它就从**数据结构**退化为
**控制通道**（[01 §12](../../docs/技术架构-v2/01-平台共享契约.md) 的「动作不进描述」）。故
``source_key`` 只取 :data:`PIN_SOURCE_KEYS` 里的键，由**组合根**把它解析成取数口；表现层
（`ui/**`）既不下发 URL，也不认识键背后的任何东西。

**为何只有 ``context_card`` 在表里**：重解析必须落在一条**不产生副作用**的只读面上。
:data:`SOURCE_BY_COMPONENT_TYPE` 只登记真有这种口的型——本批即 ``context_card``
（取数口 ``DialogFacade.context_card``：只读记忆、不跑 Skill、不写盘）。其余型钉得住、
但刷新走 ``unavailable`` ＋ 点名，属**如实降级**而非缺陷。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from st_agent.contracts.errors import ContractViolation
from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.contracts.ui_description import UiDescription
from st_agent.l0.storage.store import Store
from st_agent.l3.errors import WorkspacePinNotFoundError, WorkspaceValidationError

__all__ = [
    "PIN_PARTITION",
    "PIN_SOURCE_KEYS",
    "SOURCE_BY_COMPONENT_TYPE",
    "WorkspaceFacade",
    "WorkspacePin",
    "WorkspacePinStore",
]

PIN_PARTITION = "config"
"""钉住物的落盘分区——取 L0 **既有**分区。

新增分区须同批改 [`l0/storage/partition.py`](../l0/storage/partition.py) 的
:data:`PARTITIONS` 与 `tests/l0/test_storage.py` 的分区断言，属契约面扩张；而钉住物是
**小的键值记录**（与 `l2/memory/config_store` 同规模），不值得——故复用 ``config``。
"""

PIN_PREFIX = "workspace/pins/"

SOURCE_BY_COMPONENT_TYPE: dict[str, str] = {"context_card": "chat.context"}
"""组件型 → **可重解析来源**的面键（钉住时未显式指定 ``source`` 时的缺省）。

只有真有纯只读取数口的型在表里，理由见模块 docstring 末。前端**不**复制这张表——它由
服务端在落盘时定型，故两侧没有漂移面。
"""

PIN_SOURCE_KEYS: tuple[str, ...] = tuple(sorted(set(SOURCE_BY_COMPONENT_TYPE.values())))
"""来源面键的枚举（键**是**枚举值、不是 URL；[01 §12](../../../docs/技术架构-v2/01-平台共享契约.md)）。"""

NO_PINNED_REASON = "工作区还没有钉住的组件。在对话里对生成物点「钉到工作区」即可留存到这里。"

NO_SOURCE_REASON = (
    "该钉住项未记录可重解析的来源：它的组件型没有只读取数口，"
    "故刷新没有可重取的数据。"
)

UNWIRED_SOURCE_REASON = (
    "重解析来源未接取数口：本进程装配未提供该来源的读取面，刷新暂不可用（装配归组合根）。"
)


def _now() -> datetime:
    """用户本地时区当前时刻（[01 §8](../../../docs/技术架构-v2/01-平台共享契约.md)）。"""
    return datetime.now().astimezone()


class WorkspacePin(BaseModel):
    """一条钉住物：一份 [01 §12](../../docs/技术架构-v2/01-平台共享契约.md) 描述 ＋ 来源面键 ＋ 钉住时刻。"""

    model_config = ConfigDict(frozen=True)

    description: UiDescription
    source_key: str | None = None
    """可重解析的来源面键；``None`` ＝ 该型没有只读取数口（刷新会如实回 ``unavailable``）。"""

    pinned_at: datetime

    @field_validator("pinned_at")
    @classmethod
    def _tz_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ContractViolation("pinned_at 必须带时区语义（01 §8）")
        return value


class WorkspacePinStore:
    """钉住物的读写面（落 ``config`` 分区）。

    不缓存内存副本——每次调用从盘上读、改完即写，避免「盘已改、内存副本仍旧」这类
    双事实源（同 [`SessionStore`](chat/session.py) 的取向）。
    """

    def __init__(self, store: Store) -> None:
        self._store = store

    @staticmethod
    def _key(description_id: str) -> str:
        return f"{PIN_PREFIX}{description_id}.json"

    def pins(self) -> tuple[WorkspacePin, ...]:
        """全部钉住物——**新钉的在前**（`pinned_at` 降序，同刻按 `description_id`）。"""
        found = [
            pin
            for name in self._store.list_files(PIN_PARTITION)
            if name.startswith(PIN_PREFIX) and name.endswith(".json")
            if (pin := self._read(name)) is not None
        ]
        return tuple(sorted(found, key=lambda pin: (pin.pinned_at, pin.description.description_id),
                            reverse=True))

    def get(self, description_id: str) -> WorkspacePin:
        """按 `description_id` 读回一条钉住物（不存在 → :class:`WorkspacePinNotFoundError`）。"""
        try:
            raw = self._store.get(PIN_PARTITION, self._key(description_id))
        except KeyError as exc:  # Store 对缺文件抛 KeyError 族；统一为 L3 错误
            raise WorkspacePinNotFoundError(
                f"工作区没有该钉住项：{description_id}（它可能已被取消钉住）"
            ) from exc
        try:
            return WorkspacePin.model_validate_json(raw)
        except ValidationError as exc:
            raise WorkspaceValidationError(
                f"钉住记录损坏无法解析：{description_id}（{exc.errors()[0].get('msg', exc)}）"
            ) from exc

    def save(self, pin: WorkspacePin) -> WorkspacePin:
        """整份写回一条钉住物（同 `description_id` 即覆盖）。"""
        self._store.put(
            PIN_PARTITION,
            self._key(pin.description.description_id),
            pin.model_dump_json().encode("utf-8"),
        )
        return pin

    def unpin(self, description_id: str) -> None:
        """删除一条钉住物（不存在 → :class:`WorkspacePinNotFoundError`）。"""
        if self._key(description_id) not in self._store.list_files(PIN_PARTITION):
            raise WorkspacePinNotFoundError(
                f"工作区没有该钉住项：{description_id}（它可能已被取消钉住）"
            )
        self._store.delete(PIN_PARTITION, self._key(description_id))

    def _read(self, name: str) -> WorkspacePin | None:
        """按分区内文件名读一条（损坏的记录跳过而不是让整面 500——逐条并列，不连坐）。"""
        try:
            return WorkspacePin.model_validate_json(self._store.get(PIN_PARTITION, name))
        except Exception:  # noqa: BLE001 —— 单条坏记录不拖垮整面；缺哪条由「读不出」体现
            return None


class WorkspaceFacade:
    """工作区面（组合根注入的鸭子端口）：把 :class:`WorkspacePinStore` 的行操作包成信封。

    ``resolvers`` 是**来源面键 → 取数口**的表，由组合根按 :data:`PIN_SOURCE_KEYS` 里
    **本进程真有**的键填（缺键 ＝ 该来源未接，刷新如实回 ``unavailable``）。
    """

    def __init__(
        self,
        store: WorkspacePinStore,
        resolvers: Mapping[str, Callable[[], ResultEnvelope]] | None = None,
    ) -> None:
        self._store = store
        self._resolvers = dict(resolvers or {})

    # ───────────────────────── 读面 ─────────────────────────

    def pins(self) -> ResultEnvelope:
        """钉住物的**描述序列**（[01 §5](../../docs/技术架构-v2/01-平台共享契约.md)：工作区是承载页，
        钉住物按其**自身型**渲染、看板即该序列）。

        空工作区回 ``empty`` ＋ 原因（[01 §5](../../docs/技术架构-v2/01-平台共享契约.md)：
        「不得返回裸空」），不是 ``ok`` 空数组。
        """
        found = self._store.pins()
        if not found:
            return ResultEnvelope.empty(NO_PINNED_REASON, as_of=_now())
        return ResultEnvelope.ok(
            [pin.description for pin in found],
            as_of=found[0].description.as_of,
        )

    # ───────────────────────── 写面 ─────────────────────────

    def pin(self, body: Mapping[str, Any]) -> ResultEnvelope:
        """钉住一份描述（同 `description_id` 重复钉住 ＝ 覆盖，即「刷新」的落盘那一半）。"""
        description = _description_from(body.get("description"))
        source_key = _source_key_for(description, body.get("source"))
        pin = self._store.save(
            WorkspacePin(description=description, source_key=source_key, pinned_at=_now())
        )
        return ResultEnvelope.ok(pin.description, as_of=pin.description.as_of)

    def unpin(self, body: Mapping[str, Any]) -> ResultEnvelope:
        """取消钉住（按 `description_id`）。"""
        description_id = _required_id(body)
        self._store.unpin(description_id)
        return ResultEnvelope.ok({"description_id": description_id}, as_of=_now())

    def refresh(self, body: Mapping[str, Any]) -> ResultEnvelope:
        """按记录下来的来源面键**重解析**，以同一个 `description_id` 换新绑定与 ``as_of``。

        三种如实降级，**都不伪造数据**：没有来源面键 / 该键未接取数口 / 来源自己非 ``ok``。
        来源给出的型与钉住项不符属**装配错误**，回 ``dependency_failed`` ＋ 点名两边的型。
        """
        description_id = _required_id(body)
        pin = self._store.get(description_id)
        if pin.source_key is None:
            return ResultEnvelope.unavailable(NO_SOURCE_REASON, last_updated_at=_now())
        resolver = self._resolvers.get(pin.source_key)
        if resolver is None:
            return ResultEnvelope.unavailable(
                f"{UNWIRED_SOURCE_REASON}（来源面键：{pin.source_key}）", last_updated_at=_now()
            )

        envelope = resolver()
        if not isinstance(envelope, ResultEnvelope):
            return ResultEnvelope.dependency_failed(
                f"重解析来源未回信封（{type(envelope).__name__}）——装配有误"
            )
        if envelope.status != "ok":
            return ResultEnvelope.unavailable(
                f"重解析来源未给出数据（{envelope.status}）：{envelope.reason or '来源未说明原因'}",
                last_updated_at=_now(),
            )
        fresh = envelope.data
        if not isinstance(fresh, UiDescription):
            return ResultEnvelope.dependency_failed(
                "重解析来源给出的载荷不是一份 UI 描述——装配有误"
            )
        if fresh.component_type != pin.description.component_type:
            return ResultEnvelope.dependency_failed(
                "重解析来源给出的组件型与钉住项不符："
                f"钉住为 {pin.description.component_type!r}，来源给出 {fresh.component_type!r}"
                "——装配有误"
            )

        updated = fresh.model_copy(update={"description_id": pin.description.description_id})
        self._store.save(pin.model_copy(update={"description": updated}))
        return ResultEnvelope.ok(updated, as_of=updated.as_of)


def _required_id(body: Mapping[str, Any]) -> str:
    """取请求里的 `description_id`（缺 / 空 → :class:`WorkspaceValidationError`）。"""
    value = body.get("description_id") if isinstance(body, Mapping) else None
    if not isinstance(value, str) or not value:
        raise WorkspaceValidationError("请求缺 `description_id`（钉住后的寻址单位，01 §1）")
    return value


def _description_from(value: Any) -> UiDescription:
    """把请求里的描述构造成 :class:`UiDescription`（不合 [01 §12] 即拒，不留半份）。"""
    if isinstance(value, UiDescription):
        return value
    if not isinstance(value, dict):
        raise WorkspaceValidationError("请求缺 `description`（一份 01 §12 的 UI 描述）")
    try:
        return UiDescription.model_validate(value)
    except ValidationError as exc:
        raise WorkspaceValidationError(
            f"请求里的描述不合 01 §12：{exc.errors()[0].get('msg', exc)}"
        ) from exc


def _source_key_for(description: UiDescription, override: Any) -> str | None:
    """定一条钉住物的来源面键：请求显式给的优先，否则按组件型取缺省。"""
    if override is None:
        return SOURCE_BY_COMPONENT_TYPE.get(description.component_type)
    if not isinstance(override, str) or override not in PIN_SOURCE_KEYS:
        raise WorkspaceValidationError(
            f"不认识的重解析来源面键：{override!r}（只认 {PIN_SOURCE_KEYS}；"
            "键是枚举值不是 URL，01 §12）"
        )
    return override
