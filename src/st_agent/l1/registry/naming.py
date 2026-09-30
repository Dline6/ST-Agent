"""01 §7 配置条目的命名与寻址（点分语义 → 落盘路径确定性派生）。

口径（决策 D-067）：

- ``config_id`` 取**点分语义** ``<族>.<名>``；作用域族为
  ``skill.<base>.<param>`` / ``workflow.<base>.<param>``，「族」＝首段。
- **落盘路径由 ``config_id`` 确定性派生**——``. → /`` 再缀 ``.json``，
  故「``config_id`` 主干即文件名」这一既有不变量对全部族成立，
  不另设路径规则。
- 为兼容**存量族**（``scheduler-policy/offline-catch-up`` 这类**路径前缀式**
  ``config_id``；D-067 决议「不改名」），校验同时接受 ``.`` 与 ``/`` 作段分隔符：
  两者派生结果一致（``scheduler-policy/offline-catch-up.json`` 正是其既有落盘路径）。

**段分隔符约束**：作用域族的 ``base`` 与 ``param`` 是**单段名**，不得含 ``.`` 或
``/``——``ParameterSpec.name`` 与 ``SKILL_BASE_PATTERN`` 在契约上未禁 ``.``，
含点在点分形态下会歧义，故 :func:`param_config_id` **即拒**（不静默误解析）。
"""

from __future__ import annotations

import re
import secrets

from st_agent.l1.registry.errors import RegistryValidationError

__all__ = [
    "CHANGE_PARTITION",
    "CONFIG_PARTITION",
    "MAX_CONFIG_ID_LEN",
    "SCOPE_PREFIXES",
    "WITHIN_SCOPE_FAMILIES",
    "check_config_id",
    "config_id_for_path",
    "config_path",
    "family_of",
    "new_change_id",
    "param_config_id",
    "parse_param_config_id",
    "target_parts",
]

CONFIG_PARTITION = "config"
"""登记条目所在的 ``Store`` 分区。"""

CHANGE_PARTITION = "execution_log"
"""变更留痕（``ChangeRecord``）所在的 ``Store`` 分区。"""

MAX_CONFIG_ID_LEN = 128
"""``config_id`` 长度上限（与 ``ChangeRecord.config_id`` 的契约上限同值）。"""

WITHIN_SCOPE_FAMILIES: tuple[str, ...] = ("skill", "workflow")
"""作用域族的族名——其 ``config_id`` 形态为 ``<族>.<base>.<param>``。"""

SCOPE_PREFIXES: tuple[tuple[str, str], ...] = (("sk_", "skill"), ("wf_", "workflow"))
"""目标 id 前缀 → 作用域族（``skill_id`` / ``flow_id`` 的形态区分）。"""

_SEPARATORS = "./"

_ILLEGAL_IN_ID = re.compile(r"[\s\\]")
"""``config_id`` 内不得出现的字符：空白与反斜杠（后者会破坏相对路径语义）。"""


def check_config_id(config_id: str) -> str:
    """校验 ``config_id`` 形态（非法 → :class:`RegistryValidationError`）。

    规则：非空字符串、长度 ≤ :data:`MAX_CONFIG_ID_LEN`、无空白 / 反斜杠 /
    空段 / ``.``、``..`` 段，且 ``.`` 与 ``/`` **不混用**（混用即歧义，不猜）。
    """
    if not isinstance(config_id, str) or not config_id:
        raise RegistryValidationError(f"config_id 须为非空字符串，收到 {config_id!r}")
    if len(config_id) > MAX_CONFIG_ID_LEN:
        raise RegistryValidationError(
            f"config_id 超长（≤{MAX_CONFIG_ID_LEN}）：{config_id!r}"
        )
    hit = _ILLEGAL_IN_ID.search(config_id)
    if hit:
        raise RegistryValidationError(
            f"config_id 不得含空白或反斜杠：{config_id!r}（命中 {hit.group()!r}）"
        )
    dotted = "." in config_id
    slashed = "/" in config_id
    if dotted and slashed:
        raise RegistryValidationError(
            f"config_id 不得混用 . 与 / 作段分隔符：{config_id!r}"
        )
    sep = "." if dotted else "/"
    segments = config_id.split(sep) if (dotted or slashed) else [config_id]
    for seg in segments:
        if not seg:
            raise RegistryValidationError(f"config_id 存在空段：{config_id!r}")
        if seg in (".", ".."):
            raise RegistryValidationError(f"config_id 不得含 . / .. 段：{config_id!r}")
    return config_id


def config_path(config_id: str) -> str:
    """``config_id`` → ``config`` 分区内的相对路径（``. → /`` + ``.json``）。"""
    return check_config_id(config_id).replace(".", "/") + ".json"


def config_id_for_path(path: str) -> str:
    """``config`` 分区内相对路径 → ``config_id``（:func:`config_path` 的逆；单段路径亦受理）。"""
    if not isinstance(path, str) or not path.endswith(".json"):
        raise RegistryValidationError(f"非法条目路径：{path!r}（须以 .json 结尾）")
    stem = path[: -len(".json")]
    if not stem:
        raise RegistryValidationError(f"非法条目路径：{path!r}（文件名为空）")
    return stem.replace("/", ".")


def family_of(config_id: str) -> str:
    """``config_id`` 的族名（首段）。"""
    normalized = check_config_id(config_id).replace(".", "/")
    return normalized.split("/", 1)[0]


def param_config_id(scope: str, base: str, param: str) -> str:
    """作用域族的 ``config_id``：``<scope>.<base>.<param>``。

    ``base`` / ``param`` 必须是**单段名**（不含 ``.`` / ``/``），否则点分形态歧义。
    """
    if scope not in WITHIN_SCOPE_FAMILIES:
        raise RegistryValidationError(
            f"未知作用域族 {scope!r}（合法：{'/'.join(WITHIN_SCOPE_FAMILIES)}）"
        )
    for label, value in (("base", base), ("param", param)):
        if not isinstance(value, str) or not value:
            raise RegistryValidationError(f"作用域族的 {label} 须为非空字符串：{value!r}")
        if any(sep in value for sep in _SEPARATORS):
            raise RegistryValidationError(
                f"作用域族的 {label} 不得含段分隔符（. 或 /）：{value!r}"
            )
    return check_config_id(f"{scope}.{base}.{param}")


def parse_param_config_id(config_id: str) -> tuple[str, str, str] | None:
    """``<scope>.<base>.<param>`` → ``(scope, base, param)``；非该形态 → ``None``。

    **恰好三段**（多段即歧义 → ``None``，不猜哪段是 base、哪段是 param）。
    """
    if not isinstance(config_id, str) or "/" in config_id:
        return None
    parts = config_id.split(".")
    if len(parts) != 3 or parts[0] not in WITHIN_SCOPE_FAMILIES:
        return None
    if not all(parts):
        return None
    return parts[0], parts[1], parts[2]


def target_parts(target: str) -> tuple[str, str] | None:
    """目标 id（``skill_id`` / ``flow_id``）→ ``(scope, base)``；形态不认得 → ``None``。

    调用方按 ``None`` **回落**（不臆测目标类型、不猜版本）。
    """
    if not isinstance(target, str):
        return None
    for prefix, scope in SCOPE_PREFIXES:
        if not target.startswith(prefix):
            continue
        try:
            if scope == "skill":
                from st_agent.l1.skills.ids import base_of as _base_of  # noqa: PLC0415
            else:
                from st_agent.l1.workflow.ids import base_of as _base_of  # noqa: PLC0415
            return scope, _base_of(target)
        except Exception:  # 形态非法（版本后缀缺失等）→ 不认，交调用方回落
            return None
    return None


def new_change_id() -> str:
    """生成一个 ``change_id``（01 §7：每次配置变更产生，作回滚单位）。"""
    return f"chg_{secrets.token_hex(10)}"
