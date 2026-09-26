"""存储主入口 ``Store``（02 §2 的对外门面）。

生命周期：
- ``Store.create(root, passphrase)`` —— 首次初始化：生成 salt（主 + secrets
  独立两把）、写校验锚、建七个空分区（各含空清单）
- ``Store.open(root, passphrase)`` —— 校验密码（锚点 GCM 认证）→ 逐分区
  校验（§2.3）：清单打开失败 / 文件丢失 / 校验和不符 → 该分区标记损坏，
  其余分区照常可用，``StorageCorruptionError`` 携带逐分区明细
- 冷启动重建：``reset_partition(name)`` —— 删目录重建空分区（§2.3「无备份 →
  明确告知丢失范围，进入冷启动流程」；备份恢复路径由 T-L0-006 在分区级
  校验和口径之上叠加）

读写（加密对上层透明，GWT-3）：
- ``put(partition, name, data)`` / ``get(partition, name)`` / ``delete``
- ``export_partition(partition)`` → ``{name: bytes}``（§2.1 独立导出）
- ``wipe_partition(partition)`` → 清空重建（§2.1 独立清空）
- ``partition_state(name)`` → ok / corrupted（损坏范围查询）

并发（02 §2.4）：
- 同一进程内，同一分区的读写以**分区级互斥锁**串行化——并发写不丢清单条目，
  读不观察到「清单登记与文件内容配对不一致」的中间态（否则会误报损坏）
- 清单与文件内容一律「同目录临时文件 + ``os.replace``」原子替换，中断只留完整
  旧版本 + 无害残留，不留半截文件
- 分区间互不阻塞；**多个进程同开同一 root 不在保证范围内**（需由上层约定单一写者）

物理布局（④ 对齐）::

    root/
      keyfile.json        # salt(master/secrets) + verifier 密文（明文元数据，无用户数据）
      memory/    manifest.bin, <加密文件>...
      config/    ...
      chat_history/ ...
      execution_log/ ...
      reflection/ ...
      data_cache/ ...     # SQLite 单文件作为 opaque blob 存
      secrets/   ...      # 独立 salt 派生的独立密钥（§2.2 单独隔离）
"""

from __future__ import annotations

import json
import os
import shutil
import threading
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from st_agent.l0.storage.crypto import (
    check_verifier,
    derive_master_key,
    derive_partition_key,
    make_verifier,
    open_bytes,
    seal_bytes,
)
from st_agent.l0.storage.errors import StorageCorruptionError, StorageOpenError
from st_agent.l0.storage.manifest import (
    MANIFEST_NAME,
    ManifestEntry,
    PartitionManifest,
    compute_digest,
)
from st_agent.l0.storage.partition import (
    PARTITIONS,
    PARTITION_NAMES,
    PartitionName,
    validate_partition_name,
)

__all__ = [
    "CorruptedPartitionReport",
    "StorageState",
    "Store",
    "StoreCorruptionReport",
]

_KEYFILE = "keyfile.json"
_AAD_FILE = b"st-agent/file/v1"


class StorageState(BaseModel):
    """存储与分区状态快照（GWT-5「明确报告损坏范围」的数据形态）。"""

    model_config = ConfigDict(frozen=True)

    partition: PartitionName
    state: str = Field(pattern="^(ok|corrupted|missing)$")
    file_count: int = 0
    detail: str = ""


class CorruptedPartitionReport(BaseModel):
    """单个分区的损坏明细（§2.3「明确告知损坏范围」）。"""

    model_config = ConfigDict(frozen=True)

    partition: PartitionName
    reason: str
    """人可读原因（中性措辞）：清单损坏 / 文件缺失 / 校验和不符。"""
    affected_files: tuple[str, ...] = ()
    """损坏涉及的分区内部相对路径（缺失或校验失败者）。"""


class StoreCorruptionReport(BaseModel):
    """打开存储时发现的全部损坏（多分区并列，互不影响）。"""

    model_config = ConfigDict(frozen=True)

    corrupted: tuple[CorruptedPartitionReport, ...]
    healthy: tuple[PartitionName, ...]

    @property
    def is_clean(self) -> bool:
        return not self.corrupted


class Store:
    """已解锁的存储句柄（02 §2 门面；不可 pickle——持内存密钥）。"""

    def __init__(self, root: Path, keys: dict[str, bytes]) -> None:
        self._root = Path(root)
        self._keys = keys  # 分区名 → 分区密钥（secrets 为独立派生）
        self._manifests: dict[str, PartitionManifest] = {}
        self._states: dict[str, str] = {p.name: "ok" for p in PARTITIONS}
        self._reports: dict[str, CorruptedPartitionReport] = {}
        # 分区级互斥（02 §2.4）：同分区读写串行化，分区间互不阻塞
        self._part_locks: dict[str, threading.RLock] = {}
        self._part_locks_guard = threading.Lock()

    def _part_lock(self, partition: PartitionName) -> threading.RLock:
        """取该分区的互斥锁（延迟创建 + 双检；``_part_locks`` 自身由 guard 保护）。

        用 ``RLock`` 而非 ``Lock``：``export_partition`` 在持有分区锁时仍会调
        ``get`` / ``list_files``（同一线程重入须放行）。
        """
        lock = self._part_locks.get(partition)
        if lock is None:
            with self._part_locks_guard:
                lock = self._part_locks.get(partition)
                if lock is None:
                    lock = threading.RLock()
                    self._part_locks[partition] = lock
        return lock

    @staticmethod
    def _atomic_write(target: Path, data: bytes) -> None:
        """同目录临时文件 + ``os.replace`` 原子替换（02 §2.4 落盘原子性）。

        失败（含中断）只可能留下一个不在清单口径内的临时文件，**不会**让
        ``target`` 处于半截状态——旧内容完整保留。
        """
        tmp = target.parent / f".{target.name}.tmp-{os.urandom(6).hex()}"
        try:
            tmp.write_bytes(data)
            os.replace(tmp, target)
        except OSError:
            tmp.unlink(missing_ok=True)
            raise

    # ───────────────────────── 初始化与打开 ─────────────────────────

    @classmethod
    def create(cls, root: Path | str, passphrase: str) -> "Store":
        """首次初始化（盘上无存储时）。已存在 keyfile 则拒绝（防误覆盖）。"""
        root = Path(root)
        kf = root / _KEYFILE
        if kf.exists():
            raise StorageOpenError(
                f"{root} 已有存储（keyfile 存在）；初始化新存储请换目录或先完全清空"
            )
        salt = os.urandom(32)
        secrets_salt = os.urandom(32)                     # §2.2 单独隔离
        master = derive_master_key(passphrase, salt)
        secrets_key = derive_master_key(passphrase, secrets_salt)
        keyfile = {
            "version": 1,
            "kdf": "pbkdf2-sha256",
            "iterations_hint": 600000,
            "salt": salt.hex(),
            "secrets_salt": secrets_salt.hex(),
            "verifier": make_verifier(master).hex(),
            "verifier_secrets": make_verifier(secrets_key).hex(),
        }
        root.mkdir(parents=True, exist_ok=True)
        kf.write_text(json.dumps(keyfile, indent=2), encoding="utf-8")
        store = cls._unlock(root, master, secrets_key)
        for p in PARTITIONS:
            d = root / p.name
            d.mkdir(exist_ok=True)
            store._write_manifest(p.name, PartitionManifest(entries=()))
        return store

    @classmethod
    def open(cls, root: Path | str, passphrase: str) -> "Store":
        """打开既有存储：密码错 → 拒绝；分区损坏 → 详见异常报告。"""
        root = Path(root)
        kf = root / _KEYFILE
        if not kf.exists():
            raise StorageOpenError(f"{root} 无存储（keyfile 不存在）；先 Store.create")
        try:
            meta = json.loads(kf.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise StorageOpenError(f"keyfile 损坏，无法打开存储: {exc}") from exc
        try:
            salt = bytes.fromhex(meta["salt"])
            secrets_salt = bytes.fromhex(meta["secrets_salt"])
            verifier = bytes.fromhex(meta["verifier"])
            verifier_secrets = bytes.fromhex(meta["verifier_secrets"])
        except (KeyError, ValueError) as exc:
            raise StorageOpenError(f"keyfile 字段缺失或非法: {exc}") from exc
        master = derive_master_key(passphrase, salt)
        secrets_key = derive_master_key(passphrase, secrets_salt)
        if not (check_verifier(master, verifier)
                and check_verifier(secrets_key, verifier_secrets)):
            raise StorageOpenError(
                "主密码错误（校验锚认证失败）；密码丢失即数据不可恢复（02 §2.2，"
                "无产品方恢复通道）"
            )
        store = cls._unlock(root, master, secrets_key)
        report = store._verify_all()
        if not report.is_clean:
            raise StorageCorruptionError(report)
        return store

    @classmethod
    def _unlock(cls, root: Path, master: bytes, secrets_key: bytes) -> "Store":
        keys: dict[str, bytes] = {}
        for p in PARTITIONS:
            if p.secrets_isolated:
                keys[p.name] = secrets_key          # 独立派生，不混入主密钥树
            else:
                keys[p.name] = derive_partition_key(master, p.name)
        return cls(root, keys)

    # ───────────────────────── 读写（加密透明） ─────────────────────────

    def put(self, partition: PartitionName, name: str, data: bytes) -> None:
        """写入/覆盖一个文件（清单与密文同批更新，GWT-3 往返一致）。"""
        validate_partition_name(partition)
        with self._part_lock(partition):
            self._require_ok(partition)
            self._check_rel_name(name)
            manifest = self._manifest(partition)
            sealed = seal_bytes(self._keys[partition], data, aad=_AAD_FILE)
            target = self._root / partition / name
            target.parent.mkdir(parents=True, exist_ok=True)
            self._atomic_write(target, sealed)
            entries = {e.path: e for e in manifest.entries}
            entries[name] = ManifestEntry(path=name, size=len(data), digest=compute_digest(data))
            self._write_manifest(partition, PartitionManifest(entries=tuple(entries.values())))

    def get(self, partition: PartitionName, name: str) -> bytes:
        """读回一个文件；篡改 → CryptoError（GCM）或清单校验失败。"""
        validate_partition_name(partition)
        with self._part_lock(partition):
            self._require_ok(partition)
            manifest = self._manifest(partition)
            entry = manifest.entry_for(name)
            if entry is None:
                raise KeyError(f"分区 {partition} 无文件 {name!r}")
            sealed = (self._root / partition / name).read_bytes()
            plain = open_bytes(self._keys[partition], sealed, aad=_AAD_FILE)
            if compute_digest(plain) != entry.digest or len(plain) != entry.size:
                raise StorageCorruptionError(
                    StoreCorruptionReport(corrupted=(CorruptedPartitionReport(
                        partition=partition,
                        reason="文件内容与清单校验和不符",
                        affected_files=(name,),
                    ),), healthy=tuple(n for n in PARTITION_NAMES if n != partition))
                )
            return plain

    def delete(self, partition: PartitionName, name: str) -> None:
        """删除一个文件并同步清单。

        顺序为「**先写清单、后删文件**」：中断只会留下一个不在清单口径内的孤儿
        文件（无害、不产生损坏报告）；反序则会让清单指向已删文件，重开时被误判
        为分区损坏（GWT-5）。
        """
        validate_partition_name(partition)
        with self._part_lock(partition):
            self._require_ok(partition)
            manifest = self._manifest(partition)
            if manifest.entry_for(name) is None:
                raise KeyError(f"分区 {partition} 无文件 {name!r}")
            remaining = tuple(e for e in manifest.entries if e.path != name)
            self._write_manifest(partition, PartitionManifest(entries=remaining))
            f = self._root / partition / name
            if f.exists():
                f.unlink()

    def list_files(self, partition: PartitionName) -> tuple[str, ...]:
        """列出分区内全部文件（清单口径，非目录扫描）。"""
        validate_partition_name(partition)
        with self._part_lock(partition):
            return tuple(sorted(self._manifest(partition).paths()))

    def sealed_mtime(self, partition: PartitionName, name: str) -> float:
        """分区内文件的落盘密文 mtime（epoch 秒；T-L0-006 留存年龄口径）。"""
        validate_partition_name(partition)
        with self._part_lock(partition):
            if self._manifest(partition).entry_for(name) is None:
                raise KeyError(f"分区 {partition} 无文件 {name!r}")
            return (self._root / partition / name).stat().st_mtime

    # ───────────────────────── 分区级操作（§2.1 独立导出/清空） ─────────────────────────

    def export_partition(self, partition: PartitionName) -> dict[str, bytes]:
        """导出整个分区为 ``{相对路径: 明文}``（独立导出，GWT-1）。

        全程持分区锁，导出的是一份**一致快照**（不会混入并发写的中间态）。
        """
        with self._part_lock(partition):
            return {name: self.get(partition, name) for name in self.list_files(partition)}

    def wipe_partition(self, partition: PartitionName) -> None:
        """清空分区（独立清空，GWT-1）：删目录、重建空清单。"""
        with self._part_lock(partition):
            self._rebuild_partition(partition)

    def reset_partition(self, partition: PartitionName) -> CorruptedPartitionReport:
        """冷启动重建（§2.3 GWT-4 无备份路径）：损坏分区重建为空。

        返回重建前的损坏明细（供上层显式告知用户丢失范围）。
        只对**损坏**分区放行——健康分区不得被静默清空（误用即抛）。
        """
        validate_partition_name(partition)
        with self._part_lock(partition):
            if self._states.get(partition) == "ok":
                raise ValueError(
                    f"分区 {partition} 健康，无需冷启动重建；要清空请用 wipe_partition"
                )
            report = self._reports.get(partition) or CorruptedPartitionReport(
                partition=partition, reason="未知损坏（重建前未登记）"
            )
            self._rebuild_partition(partition)
            return report

    def partition_state(self, partition: PartitionName) -> StorageState:
        """查询分区状态（ok / corrupted；GWT-5 损坏范围查询）。"""
        validate_partition_name(partition)
        with self._part_lock(partition):
            report = self._reports.get(partition)
            if report:
                return StorageState(
                    partition=partition, state="corrupted",
                    file_count=len(self._manifests.get(partition, PartitionManifest()).entries),
                    detail=report.reason,
                )
            return StorageState(
                partition=partition, state="ok",
                file_count=len(self._manifest(partition).entries),
            )

    def storage_report(self) -> StoreCorruptionReport:
        """全存储损坏报告（打开失败被 catch 后仍可查询；正常打开时恒 clean）。"""
        return StoreCorruptionReport(
            corrupted=tuple(self._reports.values()),
            healthy=tuple(n for n in PARTITION_NAMES
                          if self._states.get(n, "ok") == "ok"),
        )

    # ───────────────────────── 损坏校验（§2.3） ─────────────────────────

    def _verify_all(self) -> StoreCorruptionReport:
        """逐分区校验：清单可解 + 文件齐全 + 逐文件校验和一致。

        单分区失败不影响其余分区（GWT-5「其余分区不受影响」）。
        """
        reports: list[CorruptedPartitionReport] = []
        healthy: list[PartitionName] = []
        for p in PARTITIONS:
            rep = self._verify_partition(p.name)
            if rep is None:
                healthy.append(p.name)
            else:
                reports.append(rep)
                self._states[p.name] = "corrupted"
                self._reports[p.name] = rep
        return StoreCorruptionReport(corrupted=tuple(reports), healthy=tuple(healthy))

    def _verify_partition(self, partition: PartitionName) -> CorruptedPartitionReport | None:
        d = self._root / partition
        if not d.is_dir():
            return CorruptedPartitionReport(
                partition=partition, reason="分区目录缺失", affected_files=()
            )
        mf = d / MANIFEST_NAME
        if not mf.is_file():
            return CorruptedPartitionReport(
                partition=partition, reason="分区清单缺失", affected_files=()
            )
        try:
            manifest = PartitionManifest.open(self._keys[partition], mf.read_bytes())
        except Exception as exc:
            return CorruptedPartitionReport(
                partition=partition,
                reason=f"分区清单解密/解析失败（篡改或密钥不符）: {exc}",
                affected_files=(),
            )
        self._manifests[partition] = manifest
        missing: list[str] = []
        mismatched: list[str] = []
        for e in manifest.entries:
            f = d / e.path
            if not f.is_file():
                missing.append(e.path)
                continue
            try:
                plain = open_bytes(self._keys[partition], f.read_bytes(), aad=_AAD_FILE)
            except Exception:
                mismatched.append(e.path)
                continue
            if compute_digest(plain) != e.digest:
                mismatched.append(e.path)
        if missing or mismatched:
            return CorruptedPartitionReport(
                partition=partition,
                reason="文件缺失" if missing else "文件校验和不符",
                affected_files=tuple(missing + mismatched),
            )
        return None

    # ───────────────────────── 内部工具 ─────────────────────────

    def _manifest(self, partition: PartitionName) -> PartitionManifest:
        m = self._manifests.get(partition)
        if m is None:
            mf = self._root / partition / MANIFEST_NAME
            m = PartitionManifest.open(self._keys[partition], mf.read_bytes())
            self._manifests[partition] = m
        return m

    def _write_manifest(self, partition: PartitionName, manifest: PartitionManifest) -> None:
        self._atomic_write(
            self._root / partition / MANIFEST_NAME, manifest.seal(self._keys[partition])
        )
        self._manifests[partition] = manifest

    def _rebuild_partition(self, partition: PartitionName) -> None:
        validate_partition_name(partition)
        d = self._root / partition
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True)
        self._write_manifest(partition, PartitionManifest(entries=()))
        self._states[partition] = "ok"
        self._reports.pop(partition, None)

    def _require_ok(self, partition: PartitionName) -> None:
        if self._states.get(partition) != "ok":
            raise StorageCorruptionError(self.storage_report())

    @staticmethod
    def _check_rel_name(name: str) -> None:
        if (not name or "\\" in name or name.startswith(("/", "~"))
                or ".." in name.split("/") or name == MANIFEST_NAME):
            raise ValueError(f"非法文件名（须为分区内安全相对路径，且不得占用 {MANIFEST_NAME}）: {name!r}")
