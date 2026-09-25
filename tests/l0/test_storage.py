"""T-L0-001 测试：02-L0 §2 存储分区 · 落盘加密 · 损坏恢复。

GWT 对照（任务文件 5 条）：
- GWT-1 分区独立：七分区各占目录、独立导出、独立清空
- GWT-2 落盘加密+密码校验：PBKDF2 派生、AES-256-GCM、secrets 隔离、
  错密码拒绝且不破坏数据、无产品方恢复通道
- GWT-3 读写闭环：写入读回一致、校验和通过
- GWT-4 损坏检测与恢复：定位分区+原因、其余不受影响、冷启动重建
- GWT-5 零接触：无出网、无产品方密钥
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from st_agent.l0.storage import (
    PARTITIONS,
    PARTITION_NAMES,
    CryptoError,
    StorageCorruptionError,
    StorageOpenError,
    Store,
    validate_partition_name,
)
from st_agent.l0.storage.crypto import derive_master_key, derive_partition_key
from st_agent.l0.storage.manifest import MANIFEST_NAME, PartitionManifest

PASS = "correct horse battery staple"


@pytest.fixture()
def store(tmp_path: Path) -> Store:
    return Store.create(tmp_path / "root", PASS)


def root_of(store: Store) -> Path:
    return store._root


# ───────────────────────── GWT-1 分区独立 ─────────────────────────


class TestGwt1Partitions:
    def test_seven_partitions_registered(self):
        names = [p.name for p in PARTITIONS]
        assert names == [
            "memory", "config", "chat_history", "execution_log",
            "reflection", "data_cache", "secrets",
        ]
        assert PARTITION_NAMES == tuple(names)

    def test_each_partition_owns_directory(self, store, tmp_path):
        for name in PARTITION_NAMES:
            d = root_of(store) / name
            assert d.is_dir(), f"分区 {name} 无独立目录"
            assert (d / MANIFEST_NAME).is_file()

    def test_independent_export(self, store):
        store.put("memory", "m1.bin", b"memory-data")
        store.put("config", "c1.bin", b"config-data")
        assert store.export_partition("memory") == {"m1.bin": b"memory-data"}
        assert store.export_partition("config") == {"c1.bin": b"config-data"}

    def test_independent_wipe(self, store):
        store.put("memory", "m1.bin", b"memory-data")
        store.put("config", "c1.bin", b"config-data")
        store.wipe_partition("memory")
        assert store.list_files("memory") == ()
        assert store.list_files("config") == ("c1.bin",)
        assert store.get("config", "c1.bin") == b"config-data"

    def test_unknown_partition_rejected(self, store):
        with pytest.raises(ValueError, match="未知分区"):
            store.put("evil", "x", b"")
        with pytest.raises(ValueError, match="未知分区"):
            store.put("../escape", "x", b"")

    def test_no_crosstalk_between_partitions(self, store):
        """分区内文件名冲突于其他分区不受影响（隔离）。"""
        for p in PARTITION_NAMES:
            store.put(p, "same-name.bin", f"data-of-{p}".encode())
        for p in PARTITION_NAMES:
            assert store.get(p, "same-name.bin") == f"data-of-{p}".encode()


# ───────────────────────── GWT-2 落盘加密 + 密码校验 ─────────────────────────


class TestGwt2Encryption:
    def test_no_plaintext_on_disk(self, store):
        secret_payload = b"PLAINTEXT-MARKER-6f3a"
        store.put("memory", "a.bin", secret_payload)
        raw = (root_of(store) / "memory" / "a.bin").read_bytes()
        assert secret_payload not in raw
        manifest_raw = (root_of(store) / "memory" / MANIFEST_NAME).read_bytes()
        assert b"PLAINTEXT-MARKER" not in manifest_raw
        assert b"a.bin" not in manifest_raw          # 文件名也在密文里

    def test_keyfile_has_no_secret_material(self, store):
        """keyfile 只有 salt/verifier（明文元数据），无主密钥或密码。"""
        meta = json.loads((root_of(store) / "keyfile.json").read_text(encoding="utf-8"))
        assert set(meta) >= {"salt", "secrets_salt", "verifier", "verifier_secrets"}
        assert PASS.encode() not in json.dumps(meta).encode()
        master = derive_master_key(PASS, bytes.fromhex(meta["salt"]))
        assert master.hex() not in json.dumps(meta)

    def test_wrong_passphrase_rejected_and_data_intact(self, store, tmp_path):
        store.put("memory", "a.bin", b"data")
        with pytest.raises(StorageOpenError, match="主密码错误"):
            Store.open(root_of(store), "wrong-pass")
        again = Store.open(root_of(store), PASS)
        assert again.get("memory", "a.bin") == b"data"   # 数据未被破坏

    def test_secrets_partition_isolated_keys(self, store):
        """secrets 分区独立派生密钥——主密钥树派生不出它（§2.2 单独隔离）。"""
        meta = json.loads((root_of(store) / "keyfile.json").read_text(encoding="utf-8"))
        master = derive_master_key(PASS, bytes.fromhex(meta["salt"]))
        secrets_key_from_master = derive_partition_key(master, "secrets")
        actual = store._keys["secrets"]
        assert actual != secrets_key_from_master
        assert actual != derive_partition_key(master, "memory")
        # 且其他分区密钥派生不出 secrets 密文（用其他分区密钥打开失败）
        store.put("secrets", "k.bin", b"key-material")
        sealed = (root_of(store) / "secrets" / "k.bin").read_bytes()
        with pytest.raises(CryptoError):
            from st_agent.l0.storage.crypto import open_bytes
            open_bytes(store._keys["memory"], sealed, aad=b"st-agent/file/v1")

    def test_create_refuses_existing_storage(self, store):
        with pytest.raises(StorageOpenError, match="已有存储"):
            Store.create(root_of(store), PASS)

    def test_open_missing_storage(self, tmp_path):
        with pytest.raises(StorageOpenError, match="无存储"):
            Store.open(tmp_path / "nowhere", PASS)


# ───────────────────────── GWT-3 读写闭环 ─────────────────────────


class TestGwt3Roundtrip:
    @pytest.mark.parametrize("partition", PARTITION_NAMES)
    def test_roundtrip_all_partitions(self, store, partition):
        payload = os.urandom(1024)
        store.put(partition, "data.bin", payload)
        assert store.get(partition, "data.bin") == payload

    def test_roundtrip_after_reopen(self, store):
        store.put("chat_history", "s/1.md", "# 对话历史".encode("utf-8"))
        reopened = Store.open(root_of(store), PASS)
        assert reopened.get("chat_history", "s/1.md") == "# 对话历史".encode("utf-8")

    def test_nested_paths(self, store):
        store.put("data_cache", "market/2026/09.db", b"sqlite-bytes")
        assert store.get("data_cache", "market/2026/09.db") == b"sqlite-bytes"
        assert store.list_files("data_cache") == ("market/2026/09.db",)

    def test_overwrite_updates_manifest(self, store):
        store.put("memory", "a.bin", b"v1")
        store.put("memory", "a.bin", b"v2-longer")
        assert store.get("memory", "a.bin") == b"v2-longer"
        assert store.list_files("memory") == ("a.bin",)

    def test_delete(self, store):
        store.put("memory", "a.bin", b"v1")
        store.delete("memory", "a.bin")
        assert store.list_files("memory") == ()
        with pytest.raises(KeyError):
            store.get("memory", "a.bin")

    def test_get_missing_file(self, store):
        with pytest.raises(KeyError):
            store.get("memory", "nope.bin")

    def test_manifest_name_reserved(self, store):
        with pytest.raises(ValueError, match="非法文件名"):
            store.put("memory", MANIFEST_NAME, b"fake-manifest")

    def test_path_traversal_rejected(self, store):
        for bad in ("../escape.bin", "/abs.bin", "a/../../b.bin", "~home.bin", "a\\b.bin"):
            with pytest.raises(ValueError):
                store.put("memory", bad, b"x")


# ───────────────────────── GWT-4 损坏检测与恢复 ─────────────────────────


class TestGwt4Corruption:
    def test_tampered_file_detected_and_localized(self, store):
        store.put("memory", "a.bin", b"good-data")
        store.put("config", "c.bin", b"untouched")
        f = root_of(store) / "memory" / "a.bin"
        sealed = bytearray(f.read_bytes())
        sealed[-1] ^= 0xFF                       # 翻转密文末字节（GCM tag 区）
        f.write_bytes(bytes(sealed))
        with pytest.raises(StorageCorruptionError) as ei:
            Store.open(root_of(store), PASS)
        report = ei.value.report
        assert [c.partition for c in report.corrupted] == ["memory"]
        assert "config" in report.healthy
        assert report.corrupted[0].affected_files == ("a.bin",)

    def test_deleted_file_detected(self, store):
        store.put("memory", "a.bin", b"data")
        (root_of(store) / "memory" / "a.bin").unlink()
        with pytest.raises(StorageCorruptionError) as ei:
            Store.open(root_of(store), PASS)
        assert ei.value.report.corrupted[0].reason == "文件缺失"

    def test_tampered_manifest_detected(self, store):
        store.put("memory", "a.bin", b"data")
        mf = root_of(store) / "memory" / MANIFEST_NAME
        sealed = bytearray(mf.read_bytes())
        sealed[5] ^= 0xFF
        mf.write_bytes(bytes(sealed))
        with pytest.raises(StorageCorruptionError) as ei:
            Store.open(root_of(store), PASS)
        assert ei.value.report.corrupted[0].partition == "memory"
        assert "清单" in ei.value.report.corrupted[0].reason

    def test_missing_partition_dir_detected(self, store):
        import shutil
        shutil.rmtree(root_of(store) / "reflection")
        with pytest.raises(StorageCorruptionError) as ei:
            Store.open(root_of(store), PASS)
        assert ei.value.report.corrupted[0].reason == "分区目录缺失"

    def test_corruption_in_one_partition_only(self, store):
        """GWT-4 核心：单分区损坏，其余分区照常可用（多分区并列报告）。"""
        for p in PARTITION_NAMES:
            store.put(p, "f.bin", b"data")
        f = root_of(store) / "reflection" / "f.bin"
        sealed = bytearray(f.read_bytes())
        sealed[-1] ^= 0xFF
        f.write_bytes(bytes(sealed))
        f2 = root_of(store) / "secrets" / "f.bin"
        sealed2 = bytearray(f2.read_bytes())
        sealed2[0] ^= 0xFF
        f2.write_bytes(bytes(sealed2))
        with pytest.raises(StorageCorruptionError) as ei:
            Store.open(root_of(store), PASS)
        report = ei.value.report
        assert set(c.partition for c in report.corrupted) == {"reflection", "secrets"}
        assert set(report.healthy) == set(PARTITION_NAMES) - {"reflection", "secrets"}

    def test_cold_start_rebuild(self, store):
        """无备份路径：损坏分区冷启动重建为空 + 明确告知丢失范围（GWT-4/5）。"""
        store.put("memory", "a.bin", b"lost-data")
        store.put("config", "c.bin", b"kept-data")
        f = root_of(store) / "memory" / "a.bin"
        sealed = bytearray(f.read_bytes())
        sealed[-1] ^= 0xFF
        f.write_bytes(bytes(sealed))
        with pytest.raises(StorageCorruptionError) as ei:
            Store.open(root_of(store), PASS)
        # 损坏报告先落地，再冷启动
        report = ei.value.report
        assert report.corrupted[0].affected_files == ("a.bin",)
        # 冷启动入口：损坏存储 open 被拒 → 用内部解锁路径拿句柄执行 reset
        s = Store._unlock(root_of(store), *_derive_keys(root_of(store), PASS))
        s._verify_all()
        assert s.partition_state("memory").state == "corrupted"
        lost = s.reset_partition("memory")
        assert lost.affected_files == ("a.bin",)         # 明确告知丢失范围
        assert s.partition_state("memory").state == "ok"
        assert s.list_files("memory") == ()               # 重建为空
        assert s.get("config", "c.bin") == b"kept-data"   # 其余分区不受影响
        # 重建后正常 open 恢复
        clean = Store.open(root_of(store), PASS)
        assert clean.get("config", "c.bin") == b"kept-data"

    def test_healthy_partition_rejects_silent_reset(self, store):
        with pytest.raises(ValueError, match="健康"):
            store.reset_partition("memory")   # 健康分区不得被静默清空

    def test_partition_state_query(self, store):
        store.put("memory", "a.bin", b"x")
        st = store.partition_state("memory")
        assert st.state == "ok" and st.file_count == 1
        st2 = store.partition_state("config")
        assert st2.state == "ok" and st2.file_count == 0


def _derive_keys(root: Path, passphrase: str) -> tuple[bytes, bytes]:
    """测试辅助：从 keyfile 复算 (master, secrets_key)。"""
    from st_agent.l0.storage.crypto import check_verifier
    meta = json.loads((root / "keyfile.json").read_text(encoding="utf-8"))
    master = derive_master_key(passphrase, bytes.fromhex(meta["salt"]))
    secrets_key = derive_master_key(passphrase, bytes.fromhex(meta["secrets_salt"]))
    assert check_verifier(master, bytes.fromhex(meta["verifier"]))
    return master, secrets_key


# ───────────────────────── GWT-5 零接触 ─────────────────────────


class TestGwt5ZeroTouch:
    def test_no_network_module_dependency(self):
        """存储子系统不 import 任何网络库（零接触的静态面）。"""
        import st_agent.l0.storage.store as store_mod
        import st_agent.l0.storage.crypto as crypto_mod
        for mod in (store_mod, crypto_mod):
            src = Path(mod.__file__).read_text(encoding="utf-8")
            for forbidden in ("requests", "urllib", "http.client", "socket", "smtp"):
                assert forbidden not in src, f"{mod.__name__} 引用了网络组件 {forbidden}"

    def test_no_vendor_recovery_channel(self, store):
        """keyfile 无第三方可解密字段：只有 salt + 双 verifier（无厂商密钥封装）。"""
        meta = json.loads((root_of(store) / "keyfile.json").read_text(encoding="utf-8"))
        assert set(meta) == {"version", "kdf", "iterations_hint",
                             "salt", "secrets_salt", "verifier", "verifier_secrets"}
        assert meta["version"] == 1 and meta["kdf"] == "pbkdf2-sha256"

    def test_kdf_parameters_recorded(self, store):
        """派生参数显式登记（可审计），迭代次数达 OWASP 2023 推荐量级。"""
        from st_agent.l0.storage.crypto import MASTER_KDF_ITERATIONS
        assert MASTER_KDF_ITERATIONS >= 600_000
        meta = json.loads((root_of(store) / "keyfile.json").read_text(encoding="utf-8"))
        assert meta["iterations_hint"] == MASTER_KDF_ITERATIONS
