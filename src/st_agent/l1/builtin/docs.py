"""``docs`` 内置原语（T-AGT-013.3；[02 §2.1](../../../docs/技术架构-v2/02-L0-本地优先基座.md)、
[D-031](../../../项目管理/决策日志.md)、[D-145](../../../项目管理/决策日志.md)）。

把 L0 既有的文档取数三面（`list_docs` / `read_doc` / `search_docs`）与一致性核对投影成
**一条只读原语**（`operation` 枚举），不新增读取方式：

- **`list`**：元数据（不含正文）；可按域 / 代码过滤。
- **`read`**：按标识读正文；「表在而文件缺」透传 L0 的 ``unavailable``（判损坏），
  **不返回空正文冒充**。
- **`search`**：按关键词检索元数据文本列（表即索引；全文/语义检索属 RAG 侧）。
- **`audit`**：核对两段一致性——缺文件列 ``corrupt``、孤儿文件列 ``orphan`` 并标
  ``recyclable=True``；**本原语不删除任何文件**（删除是写动作，须另立写类原语）。

只读本地缓存（``data_cache`` 数据访问，不在 ``local_read`` 权限范围，D-031），出厂清单
标 ``autonomous-ok``。
"""

from __future__ import annotations

from typing import Any

from st_agent.contracts.capability_types import ParameterSpec
from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.l0.info.errors import InfoValidationError
from st_agent.l1.builtin.models import BuiltinPrimitive

__all__ = ["DOCS", "DOC_DOMAINS", "DEFAULT_LIMIT", "MAX_LIMIT"]

DOC_DOMAINS: tuple[str, ...] = ("", "announcement", "sentiment_qa")
DEFAULT_LIMIT = 20
MAX_LIMIT = 200
OPERATIONS: tuple[str, ...] = ("list", "read", "search", "audit")

_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "operation": {"type": "string"},
        "columns": {"type": "array"},
        "rows": {"type": "array"},
        "doc_id": {"type": "string"},
        "domain": {"type": "string"},
        "file_path": {"type": "string"},
        "text": {"type": "string"},
        "domains": {"type": "array"},
        "consistent": {"type": "boolean"},
        "referenced_count": {"type": "integer"},
        "on_disk_count": {"type": "integer"},
        "missing_file": {"type": "array"},
        "orphan_file": {"type": "array"},
    },
}


def _executor_factory(context: Any) -> Any:
    source = context["docs"]

    def execute(_ctx: Any, params: dict[str, Any]) -> ResultEnvelope:
        operation = str(params.get("operation") or "").strip()
        domain = str(params.get("domain") or "").strip() or None
        limit = int(params.get("limit") or DEFAULT_LIMIT)
        limit = max(1, min(limit, MAX_LIMIT))
        try:
            if operation == "list":
                envelope = source.list_docs(
                    domain=domain,
                    code=str(params.get("code") or "").strip() or None,
                    limit=limit,
                )
            elif operation == "read":
                envelope = source.read_doc(
                    str(params.get("doc_id") or "").strip()
                )
            elif operation == "search":
                envelope = source.search_docs(
                    str(params.get("query") or "").strip(),
                    domain=domain, limit=limit,
                )
            elif operation == "audit":
                envelope = source.consistency(domain)
            else:
                return ResultEnvelope.validation_failed(
                    f"未知 operation {operation!r}（合法：{' / '.join(OPERATIONS)}）"
                )
        except InfoValidationError as exc:
            return ResultEnvelope.validation_failed(str(exc))
        return _tagged(operation, envelope)

    return execute


def _tagged(operation: str, envelope: ResultEnvelope) -> ResultEnvelope:
    """把 operation 并进 ok 载荷（其余状态原样透传，不改 L0 的显式结论）。"""
    if envelope.status != "ok" or not isinstance(envelope.data, dict):
        return envelope
    payload = {"operation": operation, **envelope.data}
    return ResultEnvelope.ok(
        payload, as_of=envelope.as_of, evidence_refs=envelope.evidence_refs
    )


DOCS = BuiltinPrimitive(
    base="sk_docs",
    name="文档取数",
    description=(
        "读取本地公告 / 投资者问答的元数据、正文与关键词检索结果，并可核对"
        "「表在而文件缺」（损坏）与「文件在而表缺」（孤儿，可回收）；只读，不修改文件"
    ),
    input_schema={"type": "object", "properties": {}},
    output_schema=_OUTPUT_SCHEMA,
    parameters=(
        ParameterSpec(
            name="operation", type="enum", default="list",
            choices=OPERATIONS,
            description="list 元数据 / read 正文 / search 检索 / audit 一致性核对",
        ),
        ParameterSpec(
            name="domain", type="enum", default="", choices=DOC_DOMAINS,
            description="文档域：留空=全部；announcement 公告 / sentiment_qa 投资者问答",
        ),
        ParameterSpec(
            name="code", type="string", default="",
            description="按证券代码过滤（仅 list）",
        ),
        ParameterSpec(
            name="doc_id", type="string", default="",
            description="文档标识（仅 read；取自 list / search 的 doc_id）",
        ),
        ParameterSpec(
            name="query", type="string", default="",
            description="检索关键词（仅 search）",
        ),
        ParameterSpec(
            name="limit", type="integer", default=DEFAULT_LIMIT,
            min_value=1, max_value=MAX_LIMIT,
            description="返回条数上限（list / search）",
        ),
    ),
    permissions=(),
    offline_level="full",
    requires=("docs",),
    executor=_executor_factory,
)
