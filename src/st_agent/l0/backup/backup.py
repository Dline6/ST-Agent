"""一键备份 / 恢复 / 完全清空 / 留存执行（02 §8–§9）。

只经 ``Store`` 读写分区，不直连网络（GWT-5 零接触：本模块禁一切网络 import）。

归档格式（v1，自描述单文件）::

    backup-file.json = {
      "format": "st-agent-backup/v1",   # 外层明文元数据，无用户数据
      "kdf": "pbkdf2-sha256",
      "iterations": 600000,             # 恢复时按此数派生（前向兼容）
      "salt": "<hex>",                  # 归档独立 salt（A2）
      "verifier": "<hex sealed>",       # 密码校验锚（错密码即拒，不碰目标数据）
      "payload": "<hex sealed>",        # BackupManifest JSON（含文件明文 b64）
    }

- 归档加密独立 salt 重走 PBKDF2（与 T-L0-001 A1 同口径）+ AES-256-GCM；
  备份密码即用户重输的主密码（A2）；盘上归档无任何明文用户数据与文件名。
- 备份分区表：``BACKUP_PARTITIONS`` 四分区必含，``data_cache`` 可选；
  ``secrets`` 与 ``execution_log`` 永不进归档（A1）。
- 恢复为覆盖写：先完整校验（密码→版本→逐文件校验和），全部通过后才
  动目标存储；失败不写脏数据（A3）。目标根无存储则自动创建；
  归档密码与目标存储密码可不同（跨设备迁移，A4）。
- 完全清空只认三枚有序确认令牌（``WIPE_CONFIRM_TOKENS``），删除范围为
  存储根整目录（含 keyfile）；归档文件在存储根之外，仅提示不碰（A5）。
- 留存默认值 chat_history 365 天 / execution_log 90 天，经 01 §7
  ``ConfigEntry`` 向双通道显式；文件年龄按落盘密文 mtime（A6）。
- 凭证明文（归档密码）仅驻内存，派生后即释放引用（A7）。
"""

from __future__ import annotations

import base64
import json
import os
import shutil
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from st_agent.l0.backup.errors import (
    BackupAuthError,
    BackupCorruptedError,
    BackupVersionError,
    RetentionError,
    WipeConfirmationError,
)
from st_agent.l0.backup.models import (
    BACKUP_FORMAT_VERSION,
    BACKUP_PARTITIONS,
    POLICY_PATH,
    RETAINABLE_PARTITIONS,
    BackupFileEntry,
    BackupManifest,
    RestoreReport,
    RetentionPolicy,
    WipeReport,
)
from st_agent.l0.storage.crypto import (
    MASTER_KDF_ITERATIONS,
    check_verifier,
    derive_master_key,
    make_verifier,
    open_bytes,
    seal_bytes,
)
from st_agent.l0.storage.errors import (
    CryptoError,
    StorageCorruptionError,
    StorageOpenError,
)
from st_agent.l0.storage.manifest import compute_digest
from st_agent.l0.storage.partition import PARTITION_NAMES
from st_agent.l0.storage.store import (
    CorruptedPartitionReport,
    Store,
    StoreCorruptionReport,
)

__all__ = [
    "BACKUP_AAD",
    "BACKUP_FORMAT_TAG",
    "RETENTION_SWEEP_PARTITIONS",
    "WIPE_CONFIRM_TOKENS",
    "RetentionSweepReport",
    "apply_retention",
    "create_backup",
    "get_policy",
    "restore_backup",
    "set_policy",
    "wipe_all",
]

BACKUP_FORMAT_TAG = "st-agent-backup/v1"
"""归档外层格式标识（明文元数据，无用户数据）。"""

BACKUP_AAD = b"st-agent/backup/v1"
"""归档 payload 的 GCM 附加认证数据（防挪用到他处解密）。"""

RETENTION_SWEEP_PARTITIONS = tuple(RETAINABLE_PARTITIONS)
"""允许自动清理的分区（chat_history / execution_log；02 §9）。"""

WIPE_CONFIRM_TOKENS: tuple[str, str, str] = ("WIPE-1", "WIPE-2", "WIPE-3")
"""完全清空的三枚有序确认令牌（须按序集齐，缺一即拒；A5）。"""

_ARCHIVEABLE = frozenset([*BACKUP_PARTITIONS, "data_cache"])
"""允许出现在归档中的分区（四分区 + 可选 data_cache；其余一律拒收）。"""

_KEYFILE = "keyfile.json"


def _unlock_unchecked(root: Path, passphrase: str) -> Store:
    """密码正确但分区损坏的目标解锁（跳过 _verify_all；恢复覆盖写即修复）。

    错密码仍拒绝（校验锚认证失败 → StorageOpenError，不触碰数据）。
    """
    try:
        meta = json.loads((root / _KEYFILE).read_text(encoding="utf-8"))
        salt = bytes.fromhex(meta["salt"])
        secrets_salt = bytes.fromhex(meta["secrets_salt"])
        verifier = bytes.fromhex(meta["verifier"])
        verifier_secrets = bytes.fromhex(meta["verifier_secrets"])
    except (OSError, ValueError, KeyError) as exc:
        raise StorageOpenError(f"keyfile 损坏，无法打开存储: {exc}") from exc
    master = derive_master_key(passphrase, salt)
    secrets_key = derive_master_key(passphrase, secrets_salt)
    if not (check_verifier(master, verifier)
            and check_verifier(secrets_key, verifier_secrets)):
        raise StorageOpenError("主密码错误（校验锚认证失败）；目标数据未动")
    return Store._unlock(root, master, secrets_key)


class RetentionSweepReport(BaseModel):
    """一次留存清理的结果（分区 → 被删文件，供上层显式告知）。"""

    model_config = ConfigDict(frozen=True)

    deleted: dict[str, tuple[str, ...]] = {}
    scanned: int = Field(ge=0, default=0)

    @property
    def total_deleted(self) -> int:
        return sum(len(v) for v in self.deleted.values())


def _now() -> datetime:
    """用户本地时区当前时刻（01 §8：时间统一用用户本地时区存储与展示）。"""
    return datetime.now().astimezone()


# ───────────────────────── 备份 ─────────────────────────


def create_backup(
    store: Store,
    passphrase: str,
    dest: Path | str,
    *,
    include_data_cache: bool = False,
) -> Path:
    """一键备份（GWT-1）：把备份分区表导出为单个加密归档文件。

    ``secrets`` / ``execution_log`` 永不进归档（A1）；归档整体再经独立
    salt 的 PBKDF2 + AES-256-GCM 加密（A2），盘上无明文用户数据与文件名。
    """
    dest = Path(dest)
    targets = [*BACKUP_PARTITIONS] + (["data_cache"] if include_data_cache else [])
    parts: dict[str, tuple[BackupFileEntry, ...]] = {}
    for name in targets:
        try:
            files = store.export_partition(name)  # 分区损坏即显式抛，不静默跳过
        except (CryptoError, KeyError, StorageCorruptionError) as exc:
            report = store.storage_report()
            if not report.corrupted:
                report = StoreCorruptionReport(
                    corrupted=(CorruptedPartitionReport(
                        partition=name,
                        reason=f"备份读取失败：{exc}",
                        affected_files=(),
                    ),),
                    healthy=tuple(
                        n for n in PARTITION_NAMES if n != name),
                )
            raise StorageCorruptionError(report) from exc
        parts[name] = tuple(
            BackupFileEntry(
                path=p,
                size=len(d),
                digest=compute_digest(d),
                content_b64=base64.b64encode(d).decode("ascii"),
            )
            for p, d in sorted(files.items())
        )
    manifest = BackupManifest(
        created_at=_now(),
        partitions=parts,
        includes_data_cache=include_data_cache,
    )
    salt = os.urandom(32)
    key = derive_master_key(passphrase, salt)
    try:
        outer = {
            "format": BACKUP_FORMAT_TAG,
            "kdf": "pbkdf2-sha256",
            "iterations": MASTER_KDF_ITERATIONS,
            "salt": salt.hex(),
            "verifier": make_verifier(key).hex(),
            "payload": seal_bytes(
                key, manifest.model_dump_json().encode("utf-8"), aad=BACKUP_AAD
            ).hex(),
        }
    finally:
        del key
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(outer, indent=2), encoding="utf-8")
    return dest


def _open_archive(archive: Path | str, passphrase: str) -> BackupManifest:
    """打开并完整校验归档（密码→版本→分区表→逐文件校验和），失败即抛。"""
    archive = Path(archive)
    try:
        outer = json.loads(archive.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise BackupCorruptedError(f"归档文件不可读或非 JSON：{exc}") from exc
    if not isinstance(outer, dict):
        raise BackupCorruptedError("归档外层须为 JSON 对象")
    try:
        if outer["format"] != BACKUP_FORMAT_TAG:
            raise BackupCorruptedError(
                f"未知归档格式 {outer.get('format')!r}（期望 {BACKUP_FORMAT_TAG}）"
            )
        salt = bytes.fromhex(outer["salt"])
        verifier = bytes.fromhex(outer["verifier"])
        sealed = bytes.fromhex(outer["payload"])
        iterations = int(outer.get("iterations", MASTER_KDF_ITERATIONS))
    except (KeyError, ValueError, TypeError) as exc:
        raise BackupCorruptedError(f"归档外层元数据缺失或非法：{exc}") from exc
    key = derive_master_key(passphrase, salt, iterations=iterations)
    try:
        if not check_verifier(key, verifier):
            raise BackupAuthError("归档密码错误（校验锚认证失败）；目标数据未动")
        try:
            raw = open_bytes(key, sealed, aad=BACKUP_AAD)
        except Exception as exc:
            raise BackupCorruptedError(f"归档载荷解密失败（损坏或被篡改）：{exc}") from exc
    finally:
        del key
    try:
        manifest = BackupManifest.model_validate_json(raw.decode("utf-8"))
    except (ValueError, ValidationError) as exc:
        raise BackupCorruptedError(f"归档清单解析失败：{exc}") from exc
    if manifest.format_version != BACKUP_FORMAT_VERSION:
        raise BackupVersionError(
            f"归档版本 v{manifest.format_version} 与当前 v{BACKUP_FORMAT_VERSION} "
            "不兼容，拒绝恢复（02 §8.2 版本兼容性校验）"
        )
    unknown = set(manifest.partitions) - _ARCHIVEABLE
    if unknown:
        raise BackupCorruptedError(f"归档含非法分区 {sorted(unknown)}，拒绝恢复")
    for part, entries in manifest.partitions.items():
        for e in entries:
            try:
                content = base64.b64decode(e.content_b64.encode("ascii"))
            except ValueError as exc:
                raise BackupCorruptedError(
                    f"归档文件 {part}/{e.path} 内容解码失败"
                ) from exc
            if not e.verify_content(content):
                raise BackupCorruptedError(
                    f"归档文件 {part}/{e.path} 校验和不符（归档已损坏），拒绝恢复"
                )
    return manifest


# ───────────────────────── 恢复 ─────────────────────────


def restore_backup(
    archive: Path | str,
    archive_passphrase: str,
    target_root: Path | str,
    *,
    target_passphrase: str | None = None,
) -> RestoreReport:
    """导入恢复（GWT-2）：校验通过后覆盖写目标存储。

    目标根无存储则自动创建（密码取 ``target_passphrase``，缺省与归档同口令）；
    已有存储则用 ``target_passphrase or archive_passphrase`` 打开。
    校验失败（密码/版本/损坏）不写任何脏数据（A3）。
    """
    manifest = _open_archive(archive, archive_passphrase)
    target_root = Path(target_root)
    if (target_root / _KEYFILE).exists():
        passwd = target_passphrase or archive_passphrase
        try:
            store = Store.open(target_root, passwd)
        except StorageCorruptionError:
            # GWT-5 损坏联动：目标已损坏时 open 被拒——用内部解锁拿句柄，
            # 恢复覆盖写即为「从最近备份恢复」（T-L0-001 §2.3）。
            store = _unlock_unchecked(target_root, passwd)
    else:
        store = Store.create(target_root, target_passphrase or archive_passphrase)
    try:
        for part, entries in manifest.partitions.items():
            # 覆盖写 = 先清空目标分区再写入（A4）；归档外分区不动。
            store.wipe_partition(part)
            for e in entries:
                content = base64.b64decode(e.content_b64.encode("ascii"))
                store.put(part, e.path, content)
    except StorageOpenError:
        raise
    except Exception as exc:  # 覆盖写中途失败：显式化，不留半截语义给调用方猜
        raise BackupCorruptedError(f"恢复写入失败（目标可能为半截状态）：{exc}") from exc
    return RestoreReport(
        restored_partitions=tuple(sorted(manifest.partitions)),
        file_count=manifest.file_count,
        archived_at=manifest.created_at,
    )


# ───────────────────────── 完全清空 ─────────────────────────


def wipe_all(root: Path | str, confirmations: list[str] | tuple[str, ...]) -> WipeReport:
    """完全清空（GWT-3）：三枚确认令牌按序集齐后删除存储根整目录。

    备份文件在存储根之外，仅提示、不触碰（A5）。
    """
    if tuple(confirmations) != WIPE_CONFIRM_TOKENS:
        raise WipeConfirmationError(
            "完全清空需三次确认（按序集齐三枚确认令牌）；确认不足，拒绝执行"
        )
    root = Path(root)
    if not (root / _KEYFILE).exists():
        raise StorageOpenError(f"{root} 无存储（keyfile 不存在）；无需清空")
    shutil.rmtree(root)
    return WipeReport(
        deleted_partitions=tuple(PARTITION_NAMES),
        detail="存储根整目录已删除（含 keyfile 与全部分区密文）；"
        "备份归档在存储根之外，未触碰",
    )


# ───────────────────────── 留存策略 ─────────────────────────


def get_policy(store: Store) -> RetentionPolicy:
    """读取留存策略（无配置即默认值；损坏显式抛，不静默回退）。"""
    try:
        raw = store.get("config", POLICY_PATH)
    except KeyError:
        return RetentionPolicy()
    try:
        return RetentionPolicy.model_validate_json(raw.decode("utf-8"))
    except (ValueError, ValidationError) as exc:
        raise RetentionError(f"留存策略文件损坏：{exc}") from exc


def set_policy(
    store: Store,
    *,
    chat_history_days: int | None = None,
    execution_log_days: int | None = None,
) -> RetentionPolicy:
    """更新留存策略（天数须 ≥0；reflection/memory/备份永不可配清理——A6）。"""
    current = get_policy(store).model_dump()
    for field, value in (
        ("chat_history_days", chat_history_days),
        ("execution_log_days", execution_log_days),
    ):
        if value is None:
            continue
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise RetentionError(f"{field} 须为 ≥0 的整数（0 = 不自动清理），实际 {value!r}")
        current[field] = value
    policy = RetentionPolicy(**current)
    store.put("config", POLICY_PATH, policy.model_dump_json().encode("utf-8"))
    return policy


def apply_retention(
    store: Store,
    policy: RetentionPolicy | None = None,
    *,
    now: datetime | None = None,
) -> RetentionSweepReport:
    """执行留存清理（GWT-4）：删除超期文件；0 天 = 不清理；损坏分区跳过。"""
    policy = policy or get_policy(store)
    now = now or _now()
    if now.tzinfo is None:
        raise RetentionError("now 必须带时区语义（01 §8）")
    deleted: dict[str, list[str]] = {}
    scanned = 0
    for part in RETENTION_SWEEP_PARTITIONS:
        days = getattr(policy, RETAINABLE_PARTITIONS[part])
        if days <= 0:
            continue
        if store.partition_state(part).state != "ok":
            continue  # 损坏分区不碰（恢复/重建路径处理）
        cutoff_days = days
        for name in store.list_files(part):
            scanned += 1
            try:
                age_days = (now - datetime.fromtimestamp(
                    store.sealed_mtime(part, name)).astimezone()).days
            except OSError:
                continue
            if age_days > cutoff_days:
                store.delete(part, name)
                deleted.setdefault(part, []).append(name)
    return RetentionSweepReport(
        deleted={k: tuple(sorted(v)) for k, v in deleted.items()},
        scanned=scanned,
    )
