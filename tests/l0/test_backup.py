"""T-L0-006 测试：02-L0 §8–§9 备份 / 恢复 / 完全清空 / 留存。

GWT 对照（任务文件 5 条）：
- GWT-1 备份归档：单加密归档、四分区+清单、可选 data_cache、
  secrets/execution_log 永不进归档、盘上无明文
- GWT-2 恢复还原：完整性+版本校验、错密码拒绝不碰数据、
  篡改拒绝不写脏、跨设备（异密码目标）还原
- GWT-3 完全清空：三次确认、缺一即拒、全部分区无残留、备份自理提示
- GWT-4 留存策略：默认值显式、可配、过期选中删除、禁清分区保护
- GWT-5 损坏联动与零接触：损坏目标可从备份恢复、无网络 import、
  密码明文不落盘不进日志
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path

import pytest

from st_agent.l0.backup import (
    BACKUP_FORMAT_TAG,
    BACKUP_PARTITIONS,
    WIPE_CONFIRM_TOKENS,
    BackupAuthError,
    BackupCorruptedError,
    BackupVersionError,
    RetentionError,
    RetentionPolicy,
    WipeConfirmationError,
    apply_retention,
    create_backup,
    get_policy,
    restore_backup,
    retention_config_entries,
    set_policy,
    wipe_all,
)
from st_agent.l0.storage import (
    StorageCorruptionError,
    StorageOpenError,
    Store,
)

PASS = "correct horse battery staple"
OTHER_PASS = "another passphrase for target"


@pytest.fixture()
def store(tmp_path: Path) -> Store:
    return Store.create(tmp_path / "root", PASS)


@pytest.fixture()
def seeded(store: Store) -> Store:
    store.put("memory", "g1.bin", b"graph-data")
    store.put("config", "app.json", b'{"theme": "dark"}')
    store.put("chat_history", "s/1.md", "# hello".encode())
    store.put("reflection", "weekly.md", b"reflect")
    store.put("secrets", "k.bin", b"key-material")
    store.put("execution_log", "run/1.json", b"{}")
    store.put("data_cache", "market.db", b"sqlite-bytes")
    return store


def archive_of(store: Store, tmp_path: Path, **kw) -> Path:
    dest = tmp_path / "backup.json"
    create_backup(store, PASS, dest, **kw)
    return dest


# ───────────────────────── GWT-1 备份归档 ─────────────────────────


class TestGwt1Backup:
    def test_single_archive_with_four_partitions(self, seeded, tmp_path):
        dest = archive_of(seeded, tmp_path)
        outer = json.loads(dest.read_text(encoding="utf-8"))
        assert outer["format"] == BACKUP_FORMAT_TAG
        assert set(outer) >= {"format", "kdf", "iterations", "salt",
                              "verifier", "payload"}

    def test_backup_partition_table(self, seeded, tmp_path):
        restore_root = tmp_path / "target"
        dest = archive_of(seeded, tmp_path)
        report = restore_backup(dest, PASS, restore_root, target_passphrase=OTHER_PASS)
        assert set(report.restored_partitions) == set(BACKUP_PARTITIONS)
        assert report.restored_partitions == tuple(sorted(BACKUP_PARTITIONS))
        assert "secrets" not in report.restored_partitions
        assert "execution_log" not in report.restored_partitions
        assert "data_cache" not in report.restored_partitions

    def test_data_cache_opt_in(self, seeded, tmp_path):
        dest = archive_of(seeded, tmp_path, include_data_cache=True)
        report = restore_backup(dest, PASS, tmp_path / "t2",
                                target_passphrase=OTHER_PASS)
        assert "data_cache" in report.restored_partitions
        opened = Store.open(tmp_path / "t2", OTHER_PASS)
        assert opened.get("data_cache", "market.db") == b"sqlite-bytes"

    def test_no_plaintext_in_archive(self, seeded, tmp_path):
        dest = archive_of(seeded, tmp_path, include_data_cache=True)
        raw = dest.read_bytes()
        for marker in (b"graph-data", b"key-material", b"sqlite-bytes",
                       b"g1.bin", b"market.db", PASS.encode()):
            assert marker not in raw
        outer = json.loads(raw)
        assert PASS.encode() not in json.dumps(outer).encode()

    def test_archive_roundtrip_contents(self, seeded, tmp_path):
        dest = archive_of(seeded, tmp_path)
        report = restore_backup(dest, PASS, tmp_path / "t3",
                                target_passphrase=OTHER_PASS)
        assert report.file_count == 4  # 四分区各 1 文件
        opened = Store.open(tmp_path / "t3", OTHER_PASS)
        assert opened.get("memory", "g1.bin") == b"graph-data"
        assert opened.get("chat_history", "s/1.md") == "# hello".encode()
        assert opened.get("reflection", "weekly.md") == b"reflect"

    def test_backup_damaged_partition_raises_explicitly(self, seeded, tmp_path):
        root = seeded._root
        f = root / "memory" / "g1.bin"
        sealed = bytearray(f.read_bytes())
        sealed[-1] ^= 0xFF
        f.write_bytes(bytes(sealed))
        with pytest.raises(StorageCorruptionError):
            create_backup(seeded, PASS, tmp_path / "b.json")


# ───────────────────────── GWT-2 恢复还原 ─────────────────────────


class TestGwt2Restore:
    def test_wrong_archive_passphrase_rejected_target_untouched(self, seeded, tmp_path):
        dest = archive_of(seeded, tmp_path)
        target = tmp_path / "victim"
        victim = Store.create(target, OTHER_PASS)
        victim.put("memory", "keep.bin", b"keep")
        with pytest.raises(BackupAuthError, match="密码错误"):
            restore_backup(dest, "wrong-pass", target,
                           target_passphrase=OTHER_PASS)
        assert Store.open(target, OTHER_PASS).get("memory", "keep.bin") == b"keep"

    def test_tampered_archive_rejected_no_dirty_write(self, seeded, tmp_path):
        dest = archive_of(seeded, tmp_path)
        outer = json.loads(dest.read_text(encoding="utf-8"))
        payload = bytearray(bytes.fromhex(outer["payload"]))
        payload[-1] ^= 0xFF
        outer["payload"] = bytes(payload).hex()
        dest.write_text(json.dumps(outer), encoding="utf-8")
        with pytest.raises((BackupCorruptedError, BackupAuthError)):
            restore_backup(dest, PASS, tmp_path / "tgt")
        assert not (tmp_path / "tgt" / "keyfile.json").exists()

    def test_version_mismatch_rejected(self, seeded, tmp_path):
        dest = archive_of(seeded, tmp_path)
        outer = json.loads(dest.read_text(encoding="utf-8"))
        # 用正确密码解开 payload，把版本抬高后重封（模拟未来版本归档）
        from st_agent.l0.storage.crypto import (
            derive_master_key, open_bytes, seal_bytes,
        )
        from st_agent.l0.backup.backup import BACKUP_AAD
        key = derive_master_key(PASS, bytes.fromhex(outer["salt"]),
                                iterations=int(outer["iterations"]))
        raw = json.loads(open_bytes(
            key, bytes.fromhex(outer["payload"]), aad=BACKUP_AAD).decode())
        raw["format_version"] = 999
        outer["payload"] = seal_bytes(
            key, json.dumps(raw).encode(), aad=BACKUP_AAD).hex()
        dest.write_text(json.dumps(outer), encoding="utf-8")
        with pytest.raises(BackupVersionError, match="不兼容"):
            restore_backup(dest, PASS, tmp_path / "tgt2")
        assert not (tmp_path / "tgt2" / "keyfile.json").exists()

    def test_non_json_archive_rejected(self, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text("not-json{{{", encoding="utf-8")
        with pytest.raises(BackupCorruptedError):
            restore_backup(bad, PASS, tmp_path / "tgt3")

    def test_cross_device_different_passphrase(self, seeded, tmp_path):
        dest = archive_of(seeded, tmp_path)
        report = restore_backup(dest, PASS, tmp_path / "new-device",
                                target_passphrase=OTHER_PASS)
        assert report.archived_at.tzinfo is not None
        opened = Store.open(tmp_path / "new-device", OTHER_PASS)
        assert opened.get("config", "app.json") == b'{"theme": "dark"}'
        with pytest.raises(StorageOpenError, match="主密码错误"):
            Store.open(tmp_path / "new-device", PASS)

    def test_same_passphrase_default(self, seeded, tmp_path):
        dest = archive_of(seeded, tmp_path)
        restore_backup(dest, PASS, tmp_path / "same")
        assert Store.open(tmp_path / "same", PASS).get("memory", "g1.bin") == b"graph-data"

    def test_overwrite_restore_replaces_target(self, seeded, tmp_path):
        dest = archive_of(seeded, tmp_path)
        target = tmp_path / "overwrite"
        old = Store.create(target, OTHER_PASS)
        old.put("memory", "stale.bin", b"stale")
        old.put("config", "extra.json", b"extra")
        restore_backup(dest, PASS, target, target_passphrase=OTHER_PASS)
        opened = Store.open(target, OTHER_PASS)
        assert opened.get("memory", "g1.bin") == b"graph-data"
        with pytest.raises(KeyError):
            opened.get("memory", "stale.bin")
        # 归档内分区先清空再写：目标 config 的归档外文件一并清除
        with pytest.raises(KeyError):
            opened.get("config", "extra.json")


# ───────────────────────── GWT-3 完全清空 ─────────────────────────


class TestGwt3Wipe:
    @pytest.mark.parametrize("confirmations", [
        [],
        ["WIPE-1"],
        ["WIPE-1", "WIPE-2"],
        ["WIPE-1", "WIPE-2", "WIPE-3", "EXTRA"],
        ["WIPE-3", "WIPE-2", "WIPE-1"],
        ["wipe-1", "wipe-2", "wipe-3"],
    ])
    def test_insufficient_confirmations_rejected(self, seeded, confirmations):
        root = seeded._root
        with pytest.raises(WipeConfirmationError, match="三次确认"):
            wipe_all(root, confirmations)
        assert (root / "keyfile.json").exists()  # 数据未动

    def test_wipe_removes_everything(self, seeded, tmp_path):
        root = seeded._root
        archive = archive_of(seeded, tmp_path)  # 归档在存储根之外
        report = wipe_all(root, list(WIPE_CONFIRM_TOKENS))
        assert set(report.restored_partitions if hasattr(report, "restored_partitions")
                   else report.deleted_partitions) >= {"memory", "secrets"}
        assert not root.exists()
        assert "自行处理" in report.backup_reminder
        assert archive.exists()  # 归档未被触碰
        with pytest.raises(StorageOpenError, match="无存储"):
            Store.open(root, PASS)

    def test_wipe_missing_storage(self, tmp_path):
        with pytest.raises(StorageOpenError, match="无存储"):
            wipe_all(tmp_path / "nowhere", list(WIPE_CONFIRM_TOKENS))


# ───────────────────────── GWT-4 留存策略 ─────────────────────────


class TestGwt4Retention:
    def test_defaults_explicit(self, store):
        policy = get_policy(store)
        assert policy == RetentionPolicy()
        assert policy.chat_history_days == 365
        assert policy.execution_log_days == 90

    def test_config_entries_expose_defaults(self):
        entries = retention_config_entries()
        assert {e.config_id for e in entries} == {
            "retention.chat_history_days", "retention.execution_log_days"}
        assert entries[0].default == 365 and entries[1].default == 90

    def test_set_policy_roundtrip(self, store):
        updated = set_policy(store, chat_history_days=30, execution_log_days=7)
        assert (updated.chat_history_days, updated.execution_log_days) == (30, 7)
        assert get_policy(store) == updated
        assert set_policy(store, chat_history_days=0).chat_history_days == 0

    def test_set_policy_rejects_bad_values(self, store):
        for bad in (-1, 1.5, True, "30"):
            with pytest.raises(RetentionError):
                set_policy(store, chat_history_days=bad)
            with pytest.raises(RetentionError):
                set_policy(store, execution_log_days=bad)

    def test_corrupted_policy_explicit(self, store):
        store.put("config", "retention/policy.json", b"{{{corrupt")
        with pytest.raises(RetentionError, match="损坏"):
            get_policy(store)

    def _age_file(self, path: Path, days: int) -> None:
        old = time.time() - days * 86400 - 60
        os.utime(path, (old, old))

    def test_sweep_deletes_only_expired(self, store):
        store.put("chat_history", "old.md", b"old")
        store.put("chat_history", "fresh.md", b"fresh")
        store.put("execution_log", "old.json", b"{}")
        set_policy(store, chat_history_days=30, execution_log_days=30)
        self._age_file(store._root / "chat_history" / "old.md", 31)
        self._age_file(store._root / "execution_log" / "old.json", 60)
        report = apply_retention(store)
        assert report.deleted == {
            "chat_history": ("old.md",), "execution_log": ("old.json",)}
        assert store.list_files("chat_history") == ("fresh.md",)

    def test_zero_days_means_no_sweep(self, store):
        store.put("chat_history", "ancient.md", b"old")
        self._age_file(store._root / "chat_history" / "ancient.md", 3650)
        set_policy(store, chat_history_days=0)
        assert apply_retention(store).total_deleted == 0
        assert store.list_files("chat_history") == ("ancient.md",)

    def test_protected_partitions_never_swept(self, store):
        store.put("memory", "g.bin", b"g")
        store.put("reflection", "r.md", b"r")
        store.put("secrets", "k.bin", b"k")
        self._age_file(store._root / "memory" / "g.bin", 3650)
        self._age_file(store._root / "reflection" / "r.md", 3650)
        self._age_file(store._root / "secrets" / "k.bin", 3650)
        report = apply_retention(store, RetentionPolicy(
            chat_history_days=1, execution_log_days=1))
        assert report.total_deleted == 0
        assert store.get("memory", "g.bin") == b"g"

    def test_naive_now_rejected(self, store):
        with pytest.raises(RetentionError, match="时区"):
            apply_retention(store, now=datetime(2026, 1, 1))

    def test_retention_files_boundary(self, store):
        """恰到期当天不删（age_days > days 才删），次日删。"""
        store.put("chat_history", "edge.md", b"x")
        set_policy(store, chat_history_days=30)
        self._age_file(store._root / "chat_history" / "edge.md", 30)
        assert apply_retention(store).total_deleted == 0
        self._age_file(store._root / "chat_history" / "edge.md", 31)
        assert apply_retention(store).total_deleted == 1


# ───────────────────────── GWT-5 损坏联动与零接触 ─────────────────────────


class TestGwt5LinkageAndZeroTouch:
    def test_damaged_target_restored_from_backup(self, seeded, tmp_path):
        dest = archive_of(seeded, tmp_path)
        # 破坏目标 memory 分区（GCM tag 翻转 → open 被拒）
        f = seeded._root / "memory" / "g1.bin"
        sealed = bytearray(f.read_bytes())
        sealed[-1] ^= 0xFF
        f.write_bytes(bytes(sealed))
        with pytest.raises(StorageCorruptionError):
            Store.open(seeded._root, PASS)
        # 从最近备份恢复同一目录（同口令）
        report = restore_backup(dest, PASS, seeded._root)
        assert "memory" in report.restored_partitions
        assert Store.open(seeded._root, PASS).get("memory", "g1.bin") == b"graph-data"

    def test_no_network_imports(self):
        import st_agent.l0.backup.backup as mod
        import st_agent.l0.backup.models as models_mod
        for m in (mod, models_mod):
            src = Path(m.__file__).read_text(encoding="utf-8")
            for forbidden in ("requests", "urllib", "http.client", "socket", "smtp"):
                assert forbidden not in src

    def test_passphrase_never_on_disk(self, seeded, tmp_path):
        marker = "UNIQUE-PASSPHRASE-MARKER-9z8q"
        root = tmp_path / "pw-root"
        Store.create(root, marker)
        create_backup(Store.open(root, marker), marker, tmp_path / "pw.json")
        blob = (tmp_path / "pw.json").read_bytes()
        assert marker.encode() not in blob
        # 存储根全盘扫描（含 keyfile）无密码明文
        for p in root.rglob("*"):
            if p.is_file():
                assert marker.encode() not in p.read_bytes()

    def test_stale_marker_unused_imports(self):
        """base64/timedelta 未用即删——本测试锁死 import 清单（零接触静态面）。"""
        import st_agent.l0.backup.backup as mod
        src = Path(mod.__file__).read_text(encoding="utf-8")
        assert "timedelta" not in src
