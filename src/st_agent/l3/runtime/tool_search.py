"""工具检索与延迟加载（T-AGT-017.1；[05 §10](../../../../docs/技术架构-v2/05-L3-对话主入口.md)）。

工具目录的**模型可见形态**超过阈值时，不再整份下发——只给「摘要索引 + 检索入口」，
模型按需展开匹配条目的完整描述与参数。四条口径：

- **阈值按规模、不按条数**——目录的工具段字符数**超过本次工作上下文预算**即进入惰性态
  （取值属实现口径，可经 ``run_agent_loop(tool_lazy_chars=…)`` 覆盖）。未超阈值时
  装配输出**逐字节同前**：本模块不向目录加任何条目。
- **检索入口不是能力登记**——``sk_tool_search`` 是循环的**上下文控制**，只在惰性态
  进入可见面，**不注册进 L1 能力目录**（否则未超阈值时目录会多一条，破坏「逐字节不变」）。
  它不产 ``skill_run`` / Trace 步，检索记录进 ``agent_run`` 留痕（[01 §4] 的决策口径）。
- **词法匹配、确定性**——查询词元全部在 ``name`` + ``description`` 中不区分大小写命中，
  ``name`` 命中优先；排序固定，故同一输入两次检索结果一致（GWT-6 的确定性不破）。
- **不引入向量检索**——[D-137] D-5 只解冻「工具检索 / 延迟加载」，RAG / 向量召回仍冻结。
"""

from __future__ import annotations

from collections.abc import Iterable

from pydantic import BaseModel, ConfigDict, Field

from st_agent.l0.llm.models import ToolSpec
from st_agent.l1.skills.tool_catalog import ToolEntry

__all__ = [
    "DEFAULT_TOOL_SEARCH_LIMIT",
    "MAX_TOOL_SEARCH_LIMIT",
    "SUMMARY_CHARS",
    "TOOL_SEARCH_TOOL",
    "ToolSearchRecord",
    "normalize_limit",
    "normalize_query",
    "search_catalog",
    "tool_search_description",
    "tool_search_parameters",
    "tool_search_spec",
]

TOOL_SEARCH_TOOL = "sk_tool_search"
"""检索入口的工具名（循环按名字识别，不走 L1 执行面）。"""

DEFAULT_TOOL_SEARCH_LIMIT = 5
"""单次检索最多展开的条目数（缺省；防一次检索把整个目录又塞回窗口）。"""

MAX_TOOL_SEARCH_LIMIT = 10
"""单次检索展开条目的上限（模型可调，超过即拒——不静默截断）。"""

SUMMARY_CHARS = 60
"""摘要索引里每条工具保留的描述字符数（够判断「要不要点开」，不占窗口）。"""

_TOOL_SEARCH_DESCRIPTION = (
    "按关键词在工具目录中检索并展开匹配条目的完整描述与参数"
    "（目录超过阈值、工具未被展开时使用）"
)

_TOOL_SEARCH_PARAMETERS: dict[str, object] = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": "检索词（按空白分词，全部词元须在工具名或描述中命中）",
        },
        "limit": {
            "type": "integer",
            "description": f"最多展开的条目数（1–{MAX_TOOL_SEARCH_LIMIT}，缺省 {DEFAULT_TOOL_SEARCH_LIMIT}）",
            "minimum": 1,
            "maximum": MAX_TOOL_SEARCH_LIMIT,
        },
    },
    "required": ["query"],
}


class ToolSearchRecord(BaseModel):
    """一次工具检索的留痕（**决策**，进 ``agent_run``；不产 Trace 步 / ``skill_run``）。"""

    model_config = ConfigDict(frozen=True)

    query: str = ""
    """本次检索词（模型给出、未经改写）。"""
    matched: tuple[str, ...] = ()
    """命中的工具名（按检索排序，最多 ``MAX_TOOL_SEARCH_LIMIT`` 条）。"""
    note: str = ""
    """结果的显式说明（命中数 / 未命中 / 参数非法原因）。"""


def tool_search_description() -> str:
    """检索入口的描述（模型可见形态；与摘要段共用同一份文字）。"""
    return _TOOL_SEARCH_DESCRIPTION


def tool_search_parameters() -> dict[str, object]:
    """检索入口的参数 schema（object 根；``ToolSpec.parameters`` 的线上形态）。"""
    return {key: value for key, value in _TOOL_SEARCH_PARAMETERS.items()}


def tool_search_spec() -> ToolSpec:
    """检索入口的**模型可见形态**（只在惰性态进入 ``tools`` 通道）。"""
    return ToolSpec(
        name=TOOL_SEARCH_TOOL,
        description=_TOOL_SEARCH_DESCRIPTION,
        parameters=tool_search_parameters(),
    )


def normalize_query(raw: object) -> tuple[str, str]:
    """归一化检索词（``(query, "")`` 或 ``("", 原因)``；空词不猜、不拿空查询检索）。"""
    if raw is None:
        return "", f"{TOOL_SEARCH_TOOL} 缺少 query（检索词不得为空）"
    if not isinstance(raw, str):
        return "", f"{TOOL_SEARCH_TOOL} 的 query 须为字符串，得到 {type(raw).__name__}"
    query = " ".join(raw.split())
    if not query:
        return "", f"{TOOL_SEARCH_TOOL} 的 query 不得为空"
    return query, ""


def normalize_limit(raw: object) -> tuple[int, str]:
    """归一化展开条数（缺省取 :data:`DEFAULT_TOOL_SEARCH_LIMIT`；越界即拒）。"""
    if raw is None:
        return DEFAULT_TOOL_SEARCH_LIMIT, ""
    if isinstance(raw, bool) or not isinstance(raw, int):
        return 0, f"{TOOL_SEARCH_TOOL} 的 limit 须为整数，得到 {type(raw).__name__}"
    if raw < 1 or raw > MAX_TOOL_SEARCH_LIMIT:
        return 0, (
            f"{TOOL_SEARCH_TOOL} 的 limit {raw} 越界"
            f"（取值域 1–{MAX_TOOL_SEARCH_LIMIT}）"
        )
    return raw, ""


def search_catalog(
    entries: Iterable[ToolEntry], query: str, *, limit: int = DEFAULT_TOOL_SEARCH_LIMIT
) -> tuple[ToolEntry, ...]:
    """按检索词挑条目（确定性：同一目录与查询恒得同一序列）。

    匹配口径：查询按空白分词，**全部词元**须在 ``name`` 或 ``description`` 中
    不区分大小写命中；排序为「``name`` 命中数降序 → 描述命中数降序 → 名字升序」。
    """
    tokens = [token for token in query.lower().split() if token]
    if not tokens:
        return ()
    scored: list[tuple[int, int, str, ToolEntry]] = []
    for entry in entries:
        name = entry.name.lower()
        description = entry.description.lower()
        if not all(token in name or token in description for token in tokens):
            continue
        name_hits = sum(1 for token in tokens if token in name)
        description_hits = sum(1 for token in tokens if token in description)
        scored.append((-name_hits, -description_hits, entry.name, entry))
    scored.sort(key=lambda item: (item[0], item[1], item[2]))
    return tuple(item[3] for item in scored[:max(0, limit)])
