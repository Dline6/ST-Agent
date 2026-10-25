"""数据同步中文目录（[D-130]）：覆盖、分类顺序与载荷形状。

目录是**展示层唯一词表**（`src/st_agent/ui/data_catalog.py`）——本组钉住三件事：
每个 L0 任务恰属一个数据项、每个任务 / 表都有中文名、载荷把状态与中文名一起带给前端。
"""

from __future__ import annotations

from st_agent.l0.info.sources import INFO_TASKS
from st_agent.l0.market.tasks import SYNC_TASKS
from st_agent.ui.data_catalog import (
    CATEGORY_ORDER,
    ITEMS,
    TABLE_LABELS,
    TASK_LABELS,
    catalog_payload,
    validate_catalog,
)


def test_catalog_is_complete() -> None:
    assert validate_catalog() == []


def test_every_task_appears_exactly_once() -> None:
    covered = [key for item in ITEMS for key in item.task_keys]
    registered = [spec.task_key for spec in SYNC_TASKS]
    registered += [spec.task_key for spec in INFO_TASKS]

    assert sorted(covered) == sorted(registered)
    assert len(covered) == len(set(covered))


def test_categories_are_chinese_and_contiguous() -> None:
    labels = [label for _key, label in CATEGORY_ORDER]
    assert all(any("一" <= char <= "鿿" for char in label) for label in labels)
    known = dict(CATEGORY_ORDER)
    seen: list[str] = []
    for item in ITEMS:
        assert item.category in known
        if not seen or seen[-1] != item.category:
            assert item.category not in seen, "同一大类必须连续出现"
            seen.append(item.category)


def test_task_and_table_labels_are_chinese_not_raw_keys() -> None:
    for key, (name, description) in TASK_LABELS.items():
        assert name and name != key, f"任务 {key} 缺少中文名"
        assert description, f"任务 {key} 缺少说明"
    for key, (name, description) in TABLE_LABELS.items():
        assert name and name != key, f"表 {key} 缺少中文名"
        assert description, f"表 {key} 缺少说明"
    names = [name for name, _description in TASK_LABELS.values()]
    assert len(names) == len(set(names)), "任务中文名重复会让选择面无法区分"


def test_catalog_payload_enriches_items_and_tasks() -> None:
    tasks = [
        {
            "task_key": "bs_k_daily", "status": "ok",
            "table_rows": [{"table": "k_line_daily", "rows": 3}],
            "last_success_at": "2026-10-24T15:00:00+08:00",
            "watermark": "2026-10-24", "last_error": "",
            "default_disabled": False, "coverage": "all", "role": "sole",
            "mode": "incremental", "schedule": "每交易日",
            "window_desc": "水位次日至今日", "per_code": True, "supports_range": True,
        },
        {
            "task_key": "info_sentiment_hot", "status": "",
            "table_rows": [{"table": "sentiment_hot", "rows": None}],
            "last_success_at": None, "watermark": None, "last_error": "源不可达",
            "default_disabled": False, "coverage": "all", "role": "sole",
            "mode": "snapshot", "schedule": "盘后", "window_desc": "",
            "per_code": False, "supports_range": False,
        },
    ]

    payload = catalog_payload(tasks)
    enriched = {task["task_key"]: task for task in payload["tasks"]}
    items = {item["item_id"]: item for item in payload["items"]}

    assert enriched["bs_k_daily"]["display_name"] == "日线行情"
    assert enriched["bs_k_daily"]["category_label"] == "行情"
    assert enriched["bs_k_daily"]["table_rows"][0]["display_name"] == "日线行情"
    assert items["k_daily"]["rows"] == 3
    assert items["k_daily"]["status"] == "ok"
    assert items["k_daily"]["last_success_at"] == "2026-10-24T15:00:00+08:00"
    assert items["sentiment_hot"]["status"] == ""
    assert items["sentiment_hot"]["last_error"] == "源不可达"
    assert items["sentiment_hot"]["tables"][0]["display_name"] == "市场热度榜"


def test_advanced_items_cover_backups_and_default_disabled() -> None:
    advanced = {
        key for item in ITEMS if item.advanced for key in item.task_keys
    }

    assert "bs_k_minute" in advanced
    assert "info_announcement_szse" in advanced
    assert "info_announcement_em" in advanced
    assert "info_dragon_tiger_szse" in advanced
    assert "info_dragon_tiger_sse" in advanced
    assert "info_shareholder_num_cninfo" in advanced
    assert "info_sentiment_qa_irm" in advanced
    assert "bs_k_daily" not in advanced
