"""T-L0-024：存量存储根对新增分区的兼容迁移（02 §2.1 / §2.3）。

GWT 对照：
- GWT-1 明文存量根：缺 ``workspace`` 目录仍可打开，其余数据逐字节可读
- GWT-2 加密存量根：同上，空清单按分区密钥落盘、可再次打开
- GWT-3 已初始化分区被删：仍按损坏处理，不静默重建
- GWT-4 旧根首开：补建缺失分区并把分区表刷新为当前注册表
- GWT-5 其余损坏语义不变（清单缺失 / 清空的根不复活）
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from st_agent.l0.storage import (
    PARTITION_NAMES,
    STORE_MARKER,
    StorageCorruptionError,
    Store,
    read_partition_set,
)
from st_agent.l0.storage.manifest import MANIFEST_NAME

PASS = "correct horse battery staple"
NEW = "workspace"


def _legacy_root(tmp_path: Path, *, encrypted: bool) -> Path:
    """建一个当前代码的根，再改造成「workspace 引入前」的形态。

    旧根 = 标记里没有 ``partitions`` 字段 + 没有 ``workspace`` 目录。
    """
    root = tmp_path / ("enc" if encrypted else "plain")
    store = Store.create(root, PASS if encrypted else None)
    store.put("memory", "nodes/a.json", b'{"n":1}')
    store.put("config", "app.json", b'{"v":3}')
    import shutil

    shutil.rmtree(root / NEW)
    marker = json.loads((root / STORE_MARKER).read_text(encoding="utf-8"))
    marker.pop("partitions", None)
    (root / STORE_MARKER).write_text(json.dumps(marker, indent=2), encoding="utf-8")
    return root


def _assert_migrated(root: Path, *, encrypted: bool) -> None:
    store = Store.open(root, PASS if encrypted else None)
    assert store.list_files(NEW) == ()
    assert store.partition_state(NEW).state == "ok"
    assert store.get("memory", "nodes/a.json") == b'{"n":1}'
    assert store.get("config", "app.json") == b'{"v":3}'
    assert read_partition_set(root) == PARTITION_NAMES
    # 迁移出来的空清单读写正常（加密根验证分区密钥可用）
    store.put(NEW, "note.txt", b"hello")
    assert store.get(NEW, "note.txt") == b"hello"
    # 再开一次：清单可解、不再触发迁移
    again = Store.open(root, PASS if encrypted else None)
    assert again.get(NEW, "note.txt") == b"hello"
    assert read_partition_set(root) == PARTITION_NAMES


class TestLegacyRootMigration:
    def test_plain_root_missing_workspace_opens(self, tmp_path: Path):
        _assert_migrated(_legacy_root(tmp_path, encrypted=False), encrypted=False)

    def test_encrypted_root_missing_workspace_opens(self, tmp_path: Path):
        _assert_migrated(_legacy_root(tmp_path, encrypted=True), encrypted=True)

    def test_legacy_root_without_marker_also_migrates(self, tmp_path: Path):
        """标记缺失的历史根（按加密口径读）同样补建并补写分区表。"""
        root = tmp_path / "no-marker"
        store = Store.create(root, PASS)
        store.put("memory", "a.bin", b"data")
        import shutil

        shutil.rmtree(root / NEW)
        (root / STORE_MARKER).unlink()
        store = Store.open(root, PASS)
        assert store.list_files(NEW) == ()
        assert store.get("memory", "a.bin") == b"data"
        assert read_partition_set(root) == PARTITION_NAMES


class TestInitializedPartitionDeletionStillCorrupts:
    def test_deleted_registered_partition_is_not_recreated(self, tmp_path: Path):
        """分区表登记过的分区被删 → 损坏，不静默重建（02 §2.3）。"""
        root = tmp_path / "root"
        store = Store.create(root)
        store.put("memory", "a.bin", b"data")
        import shutil

        shutil.rmtree(root / "memory")
        with pytest.raises(StorageCorruptionError) as ei:
            Store.open(root)
        assert [c.partition for c in ei.value.report.corrupted] == ["memory"]
        assert "分区目录缺失" in ei.value.report.corrupted[0].reason
        assert not (root / "memory" / MANIFEST_NAME).exists()

    def test_missing_manifest_with_data_stays_corrupt(self, tmp_path: Path):
        """目录在、清单缺失（可能残留数据）→ 仍损坏，不当作未初始化。"""
        root = tmp_path / "root"
        store = Store.create(root)
        store.put(NEW, "kept.txt", b"data")
        (root / NEW / MANIFEST_NAME).unlink()
        with pytest.raises(StorageCorruptionError) as ei:
            Store.open(root)
        assert ei.value.report.corrupted[0].partition == NEW
        assert "清单缺失" in ei.value.report.corrupted[0].reason

    def test_wiped_root_is_not_resurrected(self, tmp_path: Path):
        """整个根没有任何有效清单 → 不补建（不把误删的根复活成空存储）。"""
        root = tmp_path / "root"
        Store.create(root)
        import shutil

        for p in PARTITION_NAMES:
            shutil.rmtree(root / p, ignore_errors=True)
        with pytest.raises(StorageCorruptionError):
            Store.open(root)
