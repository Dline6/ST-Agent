"""``Store`` 的只读用量面（[`T-UI-018.1`]；02 §2.1「用户可查看存储位置和大小」）。

两件事：**位置可读**（`Store.root`）与**逐分区用量可读**（`Store.usage`）。用量的口径
与 :meth:`Store.partition_state` 一致——**未解锁 / 损坏的分区不谎报为 0**。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from st_agent.l0.storage import MODE_PLAIN, PARTITION_NAMES, StorageState, Store


def test_root_is_exposed_and_matches_the_creation_path(tmp_path: Path) -> None:
    root = tmp_path / "root"
    store = Store.create(root, None)
    assert store.root == root
    assert store.mode == MODE_PLAIN


def test_usage_covers_every_partition_and_sums_plaintext_bytes(store: Store) -> None:
    """逐分区一行、覆盖全部注册分区；`bytes` ＝清单登记的明文长度之和。"""
    store.put("memory", "m/one.json", b"12345")
    store.put("memory", "m/two.json", b"123")
    usage = store.usage()
    assert tuple(item.partition for item in usage) == tuple(PARTITION_NAMES)
    memory = next(item for item in usage if item.partition == "memory")
    assert memory.state == "ok"
    assert memory.file_count == 2
    assert memory.bytes == 8
    assert memory.detail == ""


def test_locked_partition_reports_zero_with_a_reason_not_a_fake_empty(
    tmp_path: Path,
) -> None:
    """已知空集 ≠ 未解锁：`secrets` 未解锁时 `bytes` 恒 0 **且 `detail` 写明原因**。"""
    store = Store.create(tmp_path / "plain", None)
    secrets = next(item for item in store.usage() if item.partition == "secrets")
    assert (secrets.state, secrets.file_count, secrets.bytes) == ("locked", 0, 0)
    assert "未解锁" in secrets.detail


def test_non_ok_partitions_never_report_usage(store: Store, monkeypatch) -> None:
    """**非 `ok` 的分区不谎报占用**——`usage` 复用 `partition_state` 的三态口径。

    「损坏」态在真实路径上会让 `Store.open` 整体被拒（`StorageCorruptionError` 携带
    报告），故此处直接注入一条损坏态，只验 `usage` 对该态的处理：`bytes` / `file_count`
    恒 0、`detail` 照传——不把它当空分区。
    """
    store.put("reflection", "r/one.json", b"{}")
    real = Store.partition_state

    def patched(self, partition):
        if partition == "reflection":
            return StorageState(
                partition="reflection", state="corrupted", file_count=0,
                detail="清单损坏（注入态）",
            )
        return real(self, partition)

    monkeypatch.setattr(Store, "partition_state", patched)
    row = next(item for item in store.usage() if item.partition == "reflection")
    assert row.state == "corrupted"
    assert row.bytes == 0 and row.file_count == 0
    assert row.detail == "清单损坏（注入态）"
    healthy = next(item for item in store.usage() if item.partition == "memory")
    assert healthy.state == "ok", "单分区损坏不影响其余分区的用量口径"
