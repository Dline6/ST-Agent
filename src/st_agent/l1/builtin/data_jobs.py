"""取数 / 同步原语共用的参数 → 批次项映射（T-AGT-013.1 / .2；[D-145]）。

两条原语都按**数据集 id** 取数（[`DATASET_IDS`](../../l0/market/datasets.py)），差别只在
触发形态（后台批次 vs 同步取一批）。参数解析写在**一处**，避免两条原语对同一组输入
给出不同含义：

- ``dataset``（必填）：已登记数据集 id；未登记即拒并点名（不猜相近域）。
- ``scope``：``watchlist``（缺省，逐码数据集取关注池）/ ``all_a``（全市场）/ ``custom``
  （须配对象输入 ``codes``）。全局数据集忽略该项。
- ``start`` / ``end``：日期区间的显式窗口；只在数据集声明 ``date_range`` 时生效。
- ``quarters`` / ``years``：季度 / 年份区间的最近 N 期；缺省取数据集的默认时间口径。
- ``force_refresh``：绕开覆盖账本强制重拉（高级项，缺省关）。

``codes`` / ``groups`` 是对象类输入（走 ``input_schema`` 的 ``inputs`` 通道），其余
标量走 ``parameters``——与 [01 §2](../../../../docs/技术架构-v2/01-平台共享契约.md) 的
实参双通道一致，两处都不声明的键由 L1 显式拒绝。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from st_agent.l0.market.datasets import DATASET_IDS, get_dataset
from st_agent.l0.market.errors import MarketValidationError

__all__ = [
    "DATA_INPUT_SCHEMA",
    "DATA_PARAMETERS",
    "DataParamError",
    "items_from_params",
]

SCOPES: tuple[str, ...] = ("watchlist", "all_a", "custom")


class DataParamError(ValueError):
    """取数 / 同步参数不成立（映射为 ``validation_failed``，不静默猜默认值）。"""


DATA_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "codes": {
            "type": "array",
            "items": {"type": "string"},
            "description": "自定义证券代码（scope=custom 时必填；如 sz.000001）",
        },
        "groups": {
            "type": "array",
            "items": {"type": "string"},
            "description": "指标组（仅 K 线 / 财务数据集；缺省取默认组）",
        },
    },
}
"""对象类输入（与 ``parameters`` 的标量分开，01 §2）。"""


def _scalar_parameters() -> tuple[Any, ...]:
    from st_agent.contracts.capability_types import ParameterSpec

    return (
        ParameterSpec(
            name="dataset", type="string", default="",
            description=(
                "数据集 id（如 k_daily / fin_quarter / announcement_cninfo）；"
                "可用值见 data_status 的任务行，未登记的 id 会被显式拒绝"
            ),
        ),
        ParameterSpec(
            name="scope", type="enum", default="watchlist", choices=SCOPES,
            description=(
                "逐码数据集的范围：watchlist（关注池 + 持仓，缺省）/ all_a（全市场）/ "
                "custom（配合对象输入 codes）；全局数据集忽略"
            ),
        ),
        ParameterSpec(
            name="start", type="string", default="",
            description="日期区间起点 YYYY-MM-DD（仅日期区间数据集；留空按水位续跑）",
        ),
        ParameterSpec(
            name="end", type="string", default="",
            description="日期区间终点 YYYY-MM-DD（仅日期区间数据集；留空按水位续跑）",
        ),
        ParameterSpec(
            name="quarters", type="integer", default=0, min_value=0, max_value=40,
            description="季度区间取最近 N 季（仅季度数据集；0 = 数据集默认）",
        ),
        ParameterSpec(
            name="years", type="integer", default=0, min_value=0, max_value=40,
            description="年份区间取最近 N 年（仅年份数据集；0 = 数据集默认）",
        ),
        ParameterSpec(
            name="force_refresh", type="boolean", default=False,
            description="强制重拉已覆盖区间（缺省关；高级项）",
        ),
    )


DATA_PARAMETERS = _scalar_parameters()
"""两条原语共用的标量参数（对象类见 :data:`DATA_INPUT_SCHEMA`）。"""


def items_from_params(
    params: Mapping[str, Any], inputs: Mapping[str, Any]
) -> tuple[list[dict[str, Any]], str]:
    """参数 → ``DataSyncRunner`` 的批次项与自定义代码文本。

    :raises DataParamError: 缺数据集 / 未登记 / scope 与数据集不匹配 / custom 缺 codes
    """
    dataset = str(params.get("dataset") or "").strip()
    if not dataset:
        raise DataParamError(
            "缺少 dataset（已登记的数据集 id；先用 data_status 看现有任务与数据域）"
        )
    try:
        spec = get_dataset(dataset)
    except MarketValidationError as exc:
        raise DataParamError(
            f"未登记的取数域 {dataset!r}（可用数据集：{', '.join(DATASET_IDS)}）"
        ) from exc

    scope = str(params.get("scope") or "").strip() or "watchlist"
    if scope not in SCOPES:
        raise DataParamError(
            f"未知 scope {scope!r}（合法：{' / '.join(SCOPES)}）"
        )

    custom_text = ""
    if spec.target_kind == "target":
        if scope == "custom":
            codes = [str(code).strip() for code in (inputs.get("codes") or ()) if str(code).strip()]
            if not codes:
                raise DataParamError(
                    "scope=custom 须经对象输入 codes 给出至少一个证券代码"
                )
            custom_text = ",".join(codes)
            target = "custom"
        else:
            target = scope
    else:
        target = "other"

    groups = inputs.get("groups")
    time = _time_of(spec, params)
    item = {
        "target": target,
        "dataset": spec.dataset_id,
        "groups": list(groups) if groups else None,
        "time": time,
        "force_refresh": bool(params.get("force_refresh", False)),
    }
    return [item], custom_text


def _time_of(spec: Any, params: Mapping[str, Any]) -> dict[str, Any]:
    kind = spec.time_kind
    if kind == "date_range":
        start = str(params.get("start") or "").strip()
        end = str(params.get("end") or "").strip()
        if start or end:
            return {
                "kind": "date_range", "mode": "custom",
                "start": start or None, "end": end or None,
            }
    elif kind == "quarter_range":
        quarters = int(params.get("quarters") or 0)
        if quarters > 0:
            return {"kind": "quarter_range", "mode": "last_n", "n": quarters}
    elif kind == "year_range":
        years = int(params.get("years") or 0)
        if years > 0:
            return {"kind": "year_range", "mode": "last_n", "n": years}
    return dict(spec.default_time)
