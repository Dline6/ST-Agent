"""设置区在**真 M5 组合根**上的取数与写面（[`T-UI-018.1`] / [`.2`] / [`.3`]）。

与 `tests/ui/test_ui_settings.py`（替身钉形状与失败态）分工：本组用真
[`build_m5_runtime`](../../src/st_agent/app.py) 造出的 [`SettingsFacade`] 走一遍真链路——

1. **存储页**出真 `Store` 的位置 / 格式 / 分区用量（七分区齐、`bytes` 取自真清单）；
2. **数据源页**出真注册表的 8 个源，落值经 [01 §7] 门面**真写进 `config` 分区**；
3. **隐私页**出留存两旋钮 + 出网审计开关（三族混一面的落值走同一条 apply）；
4. **LLM 提供方页**出真端点表 + 提供方主机映射；凭据分区在真口令下已解锁；
5. **网络活动**在审计关闭（出厂默认）时走**显式 unavailable**，不是空表；
6. **离线清单**的三类非空——出厂能力声明由组合根播种（此前无人注册，清单恒空）；
7. **备份 / 恢复 / 清空**：归档落在存储根之外（根内即拒）；清空的三枚有序令牌不足即拒，
   足量即删根并**置重启闸门**（其后数据面恒 `unavailable`）。

离线可跑：真 Store / 真 L1 技能目录 / 真 L2–L6，只有 LLM 出网那一跳是脚本替身（`rig_m5`）。
"""

from __future__ import annotations

import http.client
import json
from pathlib import Path

import pytest
from rig import PASS, ROOT_NAME
from rig_m5 import seeded_m5

from st_agent.ui.security import TOKEN_HEADER
from st_agent.ui.server import serve

RULES: list[tuple[str, object]] = []
ARCHIVE_PASS = "a-strong-archive-passphrase"

PARTITION_COUNT = 7


def _request(running, method: str, path: str, body: dict | None = None) -> dict:
    conn = http.client.HTTPConnection(running.host, running.port, timeout=60)
    try:
        headers = {TOKEN_HEADER: running.token}
        payload = None
        if body is not None:
            payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        conn.request(method, path, body=payload, headers=headers)
        return json.loads(conn.getresponse().read().decode("utf-8"))
    finally:
        conn.close()


def _get(running, path: str) -> dict:
    return _request(running, "GET", path)


def _post(running, path: str, body: dict) -> dict:
    return _request(running, "POST", path, body)


@pytest.fixture()
def m5(tmp_path):
    return seeded_m5(tmp_path / ROOT_NAME, rules=RULES)


def test_storage_page_names_the_real_root_and_every_partition(m5) -> None:
    """GWT-1：存储页出真位置 / 真格式 / 七分区用量（`bytes` 取自真清单）。"""
    runtime = m5.m5
    with serve(dev=False, settings=runtime.settings) as running:
        payload = _get(running, "/api/settings/storage")
        assert payload["summary"]["status"] == "ok", payload["summary"].get("reason")
        sections = payload["summary"]["data"]["slots"]["sections"]
        titles = [section["title"] for section in sections]
        assert "存储位置" in titles and "落盘格式" in titles and "凭据分区" in titles
        location = next(s for s in sections if s["title"] == "存储位置")["lines"]
        assert location == [str(runtime.settings.store.root)]

        partitions = payload["partitions"]
        assert partitions["status"] == "ok"
        rows = partitions["data"]["slots"]["rows"]
        assert len(rows) == PARTITION_COUNT
        assert {row["partition"] for row in rows} == {
            "memory", "config", "chat_history", "execution_log",
            "reflection", "data_cache", "secrets",
        }
        seeded = next(row for row in rows if row["partition"] == "memory")
        assert seeded["state"] == "正常"


def test_sources_page_lists_registered_sources_and_writes_through_the_registry(m5) -> None:
    """GWT-1 + GWT-2：8 个源逐条可开关；落值经统一注册表**真写进 `config` 分区**。"""
    runtime = m5.m5
    settings = runtime.settings
    with serve(dev=False, settings=settings) as running:
        panel = _get(running, "/api/settings/sources")["panel"]
        assert panel["status"] == "ok"
        description = panel["data"]
        assert description["component_type"] == "setting_panel"
        assert description["slots"]["surface"] == "settings-config"
        entries = description["slots"]["entries"]
        assert len(entries) == 8
        assert all(entry["actions"] == ["enable", "disable"] for entry in entries)
        assert all(entry["current"] == "启用" for entry in entries), "缺省＝开"

        first = entries[0]["config_id"]
        applied = _post(running, "/api/settings/apply", {"config_id": first, "action": "disable"})
        assert applied["status"] == "ok", applied.get("reason")
        assert applied["data"]["changed"] is True
        assert applied["data"]["change_id"], "落值必须产生变更留痕（[01 §7]）"

        # 真写进了存储：owner 侧读回同一值，而不是只在描述里改了个字
        from st_agent.l0.info.switch_config import get_source_switches

        source_id = first.rsplit("/", 1)[-1]
        assert get_source_switches(settings.store)[source_id] is False

        # 取值未变不复落值（各 owner 的既有取向）
        again = _post(running, "/api/settings/apply", {"config_id": first, "action": "disable"})
        assert again["data"]["changed"] is False


def test_privacy_page_mixes_retention_and_audit_and_coerces_form_values(m5) -> None:
    """GWT-2：留存（数值）与审计（布尔）同面；`<select>` 回传的**字符串**被还原成声明类型。"""
    runtime = m5.m5
    with serve(dev=False, settings=runtime.settings) as running:
        panel = _get(running, "/api/settings/privacy")["panel"]
        assert panel["status"] == "ok"
        entries = panel["data"]["slots"]["entries"]
        config_ids = {entry["config_id"] for entry in entries}
        assert config_ids == {
            "retention.chat_history_days", "retention.execution_log_days", "net-audit/enabled",
        }
        audit = next(e for e in entries if e["config_id"] == "net-audit/enabled")
        assert audit["current"] == "已关闭" and audit["actions"] == ["enable", "disable"]

        # 数值条目：表单回传的是字符串 "30"，落值面要求的是整数
        ok = _post(running, "/api/settings/apply", {
            "config_id": "retention.chat_history_days", "action": "set", "value": "30",
        })
        assert ok["status"] == "ok", ok.get("reason")
        assert ok["data"]["changed"] is True

        bad = _post(running, "/api/settings/apply", {
            "config_id": "retention.chat_history_days", "action": "set", "value": "不是数",
        })
        assert bad["status"] == "validation_failed"

        unknown = _post(running, "/api/settings/apply", {
            "config_id": "不存在的条目", "action": "disable",
        })
        assert unknown["status"] == "validation_failed"

        # 审计开启后网络活动面板从 unavailable 变为可查（同一开关，两处同源）
        on = _post(running, "/api/settings/apply", {"config_id": "net-audit/enabled",
                                                    "action": "enable"})
        assert on["status"] == "ok" and runtime.settings.gateway.audit_enabled is True


def test_network_panel_distinguishes_audit_off_from_no_requests(m5) -> None:
    """GWT：三态互不混同——出厂（审计默认关）走 **unavailable**，不得呈现为空表。"""
    runtime = m5.m5
    with serve(dev=False, settings=runtime.settings) as running:
        assert runtime.settings.gateway.audit_enabled is False
        payload = _get(running, "/api/settings/network")
        assert payload["status"] == "unavailable"
        assert "未开启" in payload["reason"]

        _post(running, "/api/settings/apply", {"config_id": "net-audit/enabled", "action": "enable"})
        opened = _get(running, "/api/settings/network")
        assert opened["status"] == "empty", "审计已开但窗口内没有请求 ⇒ empty，不是 unavailable"
        assert "没有" in opened["reason"]


def test_offline_report_is_populated_by_the_seeded_declarations(m5) -> None:
    """GWT-5：三类清单**非空**——出厂能力声明由组合根播种（此前 `register_capability`
    只有测试在调，真装配下 `offline_report()` 恒空）。"""
    runtime = m5.m5
    with serve(dev=False, settings=runtime.settings) as running:
        payload = _get(running, "/api/settings/offline")
        assert payload["status"] == "ok"
        sections = payload["data"]["slots"]["sections"]
        by_title = {section["title"]: section["lines"] for section in sections}
        assert by_title["连接状态"] == ["在线"]
        for title in ("离线可用", "可用但受限", "必须联网"):
            assert by_title[title] and by_title[title] != ["（无）"], title
        limited = by_title["可用但受限"]
        assert any("本地推理端点" in line for line in limited), "受限项须带降级标注（02 §7）"


def test_llm_page_shows_endpoints_providers_and_unlocked_credentials(m5) -> None:
    """GWT-1：三段并列（提供方映射 / 端点 / 凭据）；真口令下凭据分区已解锁。"""
    runtime = m5.m5
    with serve(dev=False, settings=runtime.settings) as running:
        payload = _get(running, "/api/settings/llm")
        assert payload["panel"]["status"] == "ok"
        assert payload["panel"]["data"]["slots"]["surface"] == "settings-config"
        # 提供方主机是**自由文本**条目（该族 `panel_form_spec.widget` 为 `text`、无候选值）：
        # 条目须带 `input: "text"`，否则渲染件只出一个「应用」按钮、永远提交不出值。
        entries = payload["panel"]["data"]["slots"]["entries"]
        assert entries, "真注册表里应有 provider-host 条目"
        assert all(e["input"] == "text" and e["options"] == [] for e in entries)
        assert runtime.settings.store.secrets_unlocked is True
        assert payload["credentials"]["status"] in ("ok", "empty")
        # 真注册表里至少有一个端点（rig 引导装载了一个脚本端点）
        assert payload["endpoints"]["status"] == "ok"
        rows = payload["endpoints"]["data"]["slots"]["rows"]
        assert rows and all(row["credential"] for row in rows)


def test_maintenance_declares_all_three_operations_with_the_real_tokens(m5) -> None:
    """GWT-1 / GWT-3：三条声明各有载体；清空的三枚**有序**令牌如实摆出（L0 的硬门）。"""
    runtime = m5.m5
    with serve(dev=False, settings=runtime.settings) as running:
        payload = _get(running, "/api/settings/maintenance")
        for key in ("backup", "restore", "wipe"):
            assert payload[key]["status"] == "ok", key
            assert payload[key]["data"]["component_type"] == "permission_approval_card"
        wipe = payload["wipe"]["data"]["slots"]
        assert {item["permission"] for item in wipe["items"]} >= {
            "wipe-scope", "wipe-confirm", "wipe-outside",
            "wipe-token-1", "wipe-token-2", "wipe-token-3",
        }
        labels = "；".join(item["description"] for item in wipe["labels"].values())
        assert "WIPE-1" in labels and "WIPE-3" in labels


def test_backup_refuses_a_destination_inside_the_storage_root(m5) -> None:
    """GWT-4：归档不得落在存储根之内——清空删的是整个根，放进去等于一起删。"""
    runtime = m5.m5
    root = runtime.settings.store.root
    with serve(dev=False, settings=runtime.settings) as running:
        inside = _post(running, "/api/settings/backup", {
            "passphrase": ARCHIVE_PASS, "dest": str(root / "inner.stbackup"),
        })
        assert inside["status"] == "validation_failed"
        assert "存储根之内" in inside["reason"]

        outside = root.parent / "elsewhere.stbackup"
        ok = _post(running, "/api/settings/backup", {
            "passphrase": ARCHIVE_PASS, "dest": str(outside),
        })
        assert ok["status"] == "ok", ok.get("reason")
        assert ok["data"]["archive"] == str(outside)
        assert outside.is_file() and outside.stat().st_size > 0

        wrong = _post(running, "/api/settings/restore", {
            "archive": str(outside), "passphrase": "definitely-not-the-passphrase",
        })
        assert wrong["status"] != "ok", "密码错误必须如实报错，不得静默"


def test_convert_writes_a_new_root_and_leaves_the_source_alone(m5) -> None:
    """GWT：明密双向转换**源不动、写新根**（02 §2.2）——当前根的句柄因此仍然有效。"""
    runtime = m5.m5
    root = Path(runtime.settings.store.root)
    before = str(runtime.settings.store.mode)
    target = root.parent / "plain-copy"
    with serve(dev=False, settings=runtime.settings) as running:
        same = _post(running, "/api/settings/convert", {
            "to_format": before, "target": str(target),
        })
        assert same["status"] == "validation_failed", "已是该格式即拒，不做无谓搬运"

        inside = _post(running, "/api/settings/convert", {
            "to_format": "plain", "target": str(root / "inner"),
        })
        assert inside["status"] == "validation_failed"
        assert "存储根之内" in inside["reason"]

        ok = _post(running, "/api/settings/convert", {
            "to_format": "plain", "target": str(target),
            "source_passphrase": PASS,
        })
        assert ok["status"] == "ok", ok.get("reason")
        assert ok["data"]["to_format"] == "plain"
        assert (target / "keyfile.json").is_file(), "新根已写出"
        assert root.is_dir(), "源根不动"
        # 当前进程未被闸门拦下——转换没碰运行中的根
        assert _get(running, "/api/settings/storage")["summary"]["status"] == "ok"


def test_agent_authorization_page_writes_and_rolls_back_through_the_real_facade(m5) -> None:
    """T-AGT-012.2：出厂来源可辨、修改落 `agent-change/`、回滚本身再落一条。"""
    runtime = m5.m5
    with serve(dev=False, settings=runtime.settings, reflection=runtime.reflection) as running:
        first = _get(running, "/api/settings/agent-authorization")
        assert first["panel"]["status"] == "ok", first["panel"].get("reason")
        tier, grading = first["panel"]["data"]["slots"]["entries"]
        assert tier["config_id"] == "agent.authorization"
        assert tier["current"] == "autonomous"
        assert "出厂缺省" in tier["note"]
        assert [option["value"] for option in tier["options"]] == [
            "manual", "collaborative", "autonomous",
        ]
        assert grading["config_id"] == "evolution.risk-grading"
        assert grading["actions"] == []                      # 清单只呈现
        assert "skill.sk_local_sql" in {
            rule["prefix"] for rule in grading["detail"]
        }

        applied = _post(
            running, "/api/settings/apply",
            {"config_id": "agent.authorization", "value": "manual"},
        )
        assert applied["status"] == "ok" and applied["data"]["changed"] is True

        second = _get(running, "/api/settings/agent-authorization")
        assert second["panel"]["data"]["slots"]["entries"][0]["current"] == "manual"
        assert "用户设置" in second["panel"]["data"]["slots"]["entries"][0]["note"]
        changes = second["trail"]["data"]["slots"]["values"]["changes"]
        assert [item["old_value"] for item in changes] == ["autonomous"]
        assert any(
            name.startswith("agent-change/")
            for name in runtime.store.list_files("execution_log")
        )

        rolled = _post(
            running, "/api/settings/agent-authorization/rollback",
            {"change_id": changes[0]["change_id"]},
        )
        assert rolled["status"] == "ok" and rolled["data"]["applied"] is True

        final = _get(running, "/api/settings/agent-authorization")
        assert final["panel"]["data"]["slots"]["entries"][0]["current"] == "autonomous"
        assert len(final["trail"]["data"]["slots"]["values"]["changes"]) == 2


def test_wipe_needs_three_ordered_tokens_then_stops_the_data_face(m5) -> None:
    """GWT-3：三次确认是 L0 的硬门；足量后删根并**置重启闸门**——不假装进程仍健康。"""
    runtime = m5.m5
    root = Path(runtime.settings.store.root)
    with serve(dev=False, settings=runtime.settings) as running:
        short = _post(running, "/api/settings/wipe", {"confirmations": ["WIPE-1", "WIPE-2"]})
        assert short["status"] == "validation_failed"
        assert root.is_dir(), "确认不足时不得有任何删除"

        done = _post(running, "/api/settings/wipe", {
            "confirmations": ["WIPE-1", "WIPE-2", "WIPE-3"],
        })
        assert done["status"] == "ok", done.get("reason")
        assert done["data"]["restart_required"] is True
        assert not root.exists(), "足量确认后存储根整目录被删除"

        # 闸门此后拦下**全部**数据面端点——清空后的读数无法再可信（含多段端点，
        # 此时整条响应就是一支 unavailable，不再分「段」）
        blocked = _get(running, "/api/settings/storage")
        assert blocked["status"] == "unavailable"
        assert "重启" in blocked["reason"]
        chat = _get(running, "/api/chat/context")
        assert chat["status"] == "unavailable" and "重启" in chat["reason"]
        # 健康面**不拦**——壳要靠它判断服务是否还活着
        assert _get(running, "/api/health")["status"] == "ok"


# ───────────────────── 启动引导配置与数据同步（[D-129]） ─────────────────────

def test_storage_page_names_location_source_and_pointer(m5) -> None:
    runtime = m5.m5
    with serve(dev=False, settings=runtime.settings) as running:
        sections = _get(running, "/api/settings/storage")["summary"]["data"]["slots"]["sections"]

    titles = [section["title"] for section in sections]
    assert "位置来源" in titles and "启动指针" in titles


def test_data_page_reports_real_tasks_tables_and_rows(m5) -> None:
    runtime = m5.m5
    with serve(dev=False, settings=runtime.settings) as running:
        payload = _get(running, "/api/settings/data")

    assert payload["summary"]["status"] == "ok"
    summary = payload["summary"]["data"]["slots"]["values"]
    assert summary["db_exists"] is True
    assert summary["total_rows"] >= 4
    assert summary["catalog"]["item_count"] >= 20
    catalog = payload["tasks"]["data"]["slots"]["values"]
    tasks = catalog["tasks"]
    items = catalog["items"]
    assert any(task["task_key"] == "bs_k_daily" and task["status"] == "ok"
               for task in tasks)
    assert any(item["item_id"] == "k_daily" and item["status"] == "ok"
               for item in items)
    assert all(task["display_name"] and task["display_name"] != task["task_key"]
               for task in tasks)
    assert all(item["display_name"] for item in items)
    tables = {row["table"]: row for row in payload["tables"]["data"]["slots"]["rows"]}
    assert tables["k_line_daily"]["rows"] == 4
    assert tables["k_line_daily"]["display_name"] == "日线行情"
    assert payload["job"]["data"]["slots"]["values"] == {}


def test_data_job_snapshot_is_light_and_empty_before_a_batch(m5) -> None:
    runtime = m5.m5
    with serve(dev=False, settings=runtime.settings) as running:
        payload = _get(running, "/api/settings/data/job")

    assert payload["status"] == "ok"
    assert payload["data"] == {"job": {}}


def test_data_preflight_reports_order_and_scope(m5) -> None:
    runtime = m5.m5
    with serve(dev=False, settings=runtime.settings) as running:
        payload = _post(running, "/api/settings/data/preflight", {
            "items": [{
                "target": "st", "dataset": "k_daily", "groups": ["price"],
                "time": {"kind": "date_range", "mode": "custom",
                         "start": "2026-09-01", "end": None},
            }],
        })

    assert payload["status"] == "ok"
    plan = payload["data"]
    assert plan["order"][:4] == [
        "bs_calendar", "bs_st_list", "bs_all_stock", "bs_k_daily",
    ]
    assert set(plan["auto_added"]) == {
        "bs_calendar", "bs_st_list", "bs_all_stock",
    }
    assert plan["targets"]["st"]["count"] == 2
    assert plan["targets"]["st"]["as_of"].startswith("2026-09-25")
    assert plan["errors"] == []
    assert plan["plan"][0]["groups"] == ["price"]


def test_data_preflight_items_use_dataset_default_time(m5) -> None:
    runtime = m5.m5
    with serve(dev=False, settings=runtime.settings) as running:
        payload = _post(running, "/api/settings/data/preflight", {
            "items": [{"target": "st", "dataset": "k_daily"}],
        })

    plan = payload["data"]
    assert plan["errors"] == []
    assert plan["plan"][0]["time"]["kind"] == "date_range"


def test_data_clear_requires_the_confirmation_phrase(m5) -> None:
    runtime = m5.m5
    with serve(dev=False, settings=runtime.settings) as running:
        payload = _post(running, "/api/settings/data/clear", {"level": 1, "phrase": ""})

    assert payload["status"] == "validation_failed"
    assert "清除市场数据" in payload["reason"]


def test_data_cancel_without_a_job_is_an_honest_noop(m5) -> None:
    runtime = m5.m5
    with serve(dev=False, settings=runtime.settings) as running:
        payload = _post(running, "/api/settings/data/sync/cancel", {})

    assert payload["status"] == "ok"
    assert payload["data"]["cancelled"] is False
    assert "没有运行中的批次" in payload["data"]["reason"]



def test_data_sync_rejects_unknown_task_without_touching_the_network(m5) -> None:
    runtime = m5.m5
    with serve(dev=False, settings=runtime.settings) as running:
        payload = _post(running, "/api/settings/data/sync", {"task_keys": ["no-such-task"]})

    assert payload["status"] == "validation_failed"
    assert "未知同步任务" in payload["reason"]


def test_llm_page_exposes_bootstrap_values_without_plaintext_key(m5) -> None:
    runtime = m5.m5
    with serve(dev=False, settings=runtime.settings) as running:
        payload = _get(running, "/api/settings/llm")

    boot = payload["boot"]["data"]["slots"]["values"]
    assert "base_url" in boot and "model" in boot
    assert "sk-test-0123456789-abcdef" not in str(boot)


def test_llm_bootstrap_rejects_invalid_base_url(m5) -> None:
    runtime = m5.m5
    with serve(dev=False, settings=runtime.settings) as running:
        payload = _post(running, "/api/settings/llm/bootstrap", {
            "base_url": "not-a-url", "model": "m", "api_key": "sk-new",
        })

    assert payload["status"] == "validation_failed"


def test_store_relocate_reset_clears_pointer_and_gates_the_process(m5, tmp_path) -> None:
    from st_agent.l0.bootstrap.root import write_store_root

    runtime = m5.m5
    pointer = runtime.bootstrap_file
    write_store_root(tmp_path / "elsewhere", pointer)

    with serve(dev=False, settings=runtime.settings) as running:
        payload = _post(running, "/api/settings/store/relocate", {"mode": "reset"})
        assert payload["status"] == "ok", payload.get("reason")
        assert payload["data"]["restart_required"] is True
        assert pointer.exists() is False
        blocked = _get(running, "/api/settings/storage")
        assert blocked["status"] == "unavailable"
        assert "重启" in blocked["reason"]


def test_store_relocate_migrate_copies_the_root_and_gates_the_process(m5, tmp_path) -> None:
    from st_agent.l0.storage import Store

    runtime = m5.m5
    target = tmp_path / "migrated-root"

    with serve(dev=False, settings=runtime.settings) as running:
        payload = _post(running, "/api/settings/store/relocate", {
            "mode": "migrate", "target": str(target), "passphrase": PASS,
        })

    assert payload["status"] == "ok", payload.get("reason")
    assert payload["data"]["target_root"] == str(target)
    assert payload["data"]["restart_required"] is True
    assert Store.exists(target) is True
    copied = Store.open(target, PASS)
    assert sorted(copied.list_files("config")) == sorted(runtime.store.list_files("config"))
    assert sorted(copied.list_files("memory")) == sorted(runtime.store.list_files("memory"))
