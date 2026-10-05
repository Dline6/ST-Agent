"""存储主入口 ``Store``（02 §2 的对外门面）。

生命周期（三种起点）：
- ``Store.create(root)`` —— 首次初始化，**不加密**（默认）：只生成 `secrets`
  用的独立 salt；六分区明文落盘、免口令读写
- ``Store.create(root, 口令)`` —— 首次初始化并**开启加密**：生成 salt（主 +
  secrets 独立两把）、写校验锚，七分区全部加密
- ``Store.open(root[, 口令])`` —— 按存储根里的**格式标记**自行分派（02 §2.2）：
  明文根免口令；加密根必须给主密码（锚点 GCM 认证失败即拒）。逐分区校验
  （§2.3）：清单打开失败 / 文件丢失 / 校验和不符 → 该分区标记损坏，其余分区
  照常可用，``StorageCorruptionError`` 携带逐分区明细
- 冷启动重建：``reset_partition(name)`` —— 删目录重建空分区（§2.3）；备份恢复
  路径由 T-L0-006 在分区级校验和口径之上叠加

**凭据恒加密、惰性解锁**（02 §2.2 / §3）：`secrets` 分区无论存储整体是否加密
一律密文落盘，且**未解锁时不可读写**（``StorageSecretsLockedError``）。解锁靠
`unlock_secrets(口令)`；明文根的该口令**首次解锁时设定**（TOFU——此时凭据分区
必为空，故不会顶替任何既有数据）。加密根在 `open` 给出主密码时，若该密码同时
满足凭据校验锚则一并解锁，否则凭据保持锁定（凭据口令可与主密码不同；转换
（:meth:`Store.convert`）原样搬运凭据材料，故转换不改凭据口令）。

读写（加密对上层透明，GWT-3）：
- ``put(partition, name, data)`` / ``get(partition, name)`` / ``delete``
- ``export_partition(partition)`` → ``{name: bytes}``（§2.1 独立导出）
- ``wipe_partition(partition)`` → 清空重建（§2.1 独立清空）
- ``partition_state(name)`` → ok / corrupted / locked（损坏范围与解锁态查询）

**「不加密」不等于「不校验」**（§2.3）：明文模式照旧保留逐文件校验和、分区
清单与原子替换，降的只是机密性。

并发（02 §2.4）：
- 同一进程内，同一分区的读写以**分区级互斥锁**串行化——并发写不丢清单条目，
  读不观察到「清单登记与文件内容配对不一致」的中间态（否则会误报损坏）
- 清单与文件内容一律「临时文件 + ``os.replace``」原子替换，中断只留完整旧版本
  + 无害残留，不留半截文件；中间产物写在**专属临时目录** ``root/.st-agent-tmp/``
  （不污染分区目录），其崩溃残留由 ``Store.open`` 整体回收（E2）
- 分区间互不阻塞；**多个进程同开同一 root 不在保证范围内**（需由上层约定单一写者）

物理布局（④ 对齐）::

    root/
      store.json          # 格式标记（明文：格式名 + 模式，无密钥材料）
      keyfile.json        # 加密根：主 + secrets 两把 salt 与校验锚；
                          # 明文根：只有 secrets_salt（+ 首次解锁后补的校验锚）
      .st-agent-tmp/      # 原子替换的中间产物（仅密文；open 时整体回收）
      memory/    manifest.bin, <文件>...       # 明文根 = 明文；加密根 = 密文
      config/    ...
      chat_history/ ...
      execution_log/ ...
      reflection/ ...
      data_cache/ ...     # SQLite 单文件作为 opaque blob 存
      secrets/   ...      # 恒密文（独立 salt 派生的独立密钥，§2.2 单独隔离）
"""

from __future__ import annotations

import json
import os
import shutil
import threading
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from st_agent.l0.storage.crypto import (
    check_verifier,
    derive_master_key,
    derive_partition_key,
    make_verifier,
    open_bytes,
    seal_bytes,
)
from st_agent.l0.storage.errors import (
    StorageCorruptionError,
    StorageOpenError,
    StoragePassphraseRequired,
    StorageSecretsLockedError,
)
from st_agent.l0.storage.format import (
    MODE_ENCRYPTED,
    MODE_PLAIN,
    STORE_MARKER,
    read_mode,
    write_marker,
)
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
_SECRETS = "secrets"
_AAD_FILE = b"st-agent/file/v1"
_AAD_MANIFEST = b"st-agent/manifest"

TMP_DIR_NAME = ".st-agent-tmp"
"""原子替换的中间产物目录名（存储根下；`Store.open` 时整体回收，见 02 §2.4）。

独立成目录而非「同目录临时文件」：回收对象因而是一个**专属目录**，无需按文件名
模式匹配，不可能误删用户数据；分区目录也随之保持「只有清单 + 数据文件」。
必须与目标同卷——``os.replace`` 的原子性以此为前提。
"""

_REPLACE_ATTEMPTS = 3
"""``os.replace`` 的尝试次数（**只**对 ``PermissionError`` 重试）。"""
_REPLACE_BACKOFF_S = 0.02
"""重试间隔基数（第 n 次前停 ``n × 基数``）。"""

KEYFILE_ITERATIONS_HINT = 600_000
"""写入 keyfile 的迭代次数提示（与 crypto.MASTER_KDF_ITERATIONS 同源）。"""


def _read_keyfile_meta(root: Path) -> dict[str, Any]:
    """读 keyfile 的元数据（``open`` 与只读探测**共用同一读法**，免两处口径漂移）。"""
    kf = root / _KEYFILE
    if not kf.exists():
        raise StorageOpenError(f"{root} 无存储（keyfile 不存在）；先 Store.create")
    try:
        return json.loads(kf.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StorageOpenError(f"keyfile 损坏，无法打开存储: {exc}") from exc


def _has_files_under(directory: Path) -> bool:
    """目录下是否存在清单之外的**数据文件**（递归：凭据按 ``cred/<id>.json`` 分层）。

    只看顶层会把「清单丢了但数据还在」误判成空分区，故必须递归。
    """
    return directory.is_dir() and any(
        p.is_file() and p.name != MANIFEST_NAME for p in directory.rglob("*")
    )


class StorageState(BaseModel):
    """存储与分区状态快照（GWT-5「明确报告损坏范围」的数据形态）。

    ``locked`` ＝ 该分区未解锁（明文模式的 `secrets`；02 §2.2 惰性解锁）——
    与 ``ok`` / ``corrupted`` 三态**互不混淆**：未解锁既不报告为损坏，也不
    谎报为空分区（``file_count`` 此时恒 0 且 ``detail`` 写明原因）。
    """

    model_config = ConfigDict(frozen=True)

    partition: PartitionName
    state: str = Field(pattern="^(ok|corrupted|missing|locked)$")
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
    """已校验通过的分区（未解锁的分区**不计入**——它没被校验过，02 §2.2）。"""

    @property
    def is_clean(self) -> bool:
        return not self.corrupted


class Store:
    """已解锁的存储句柄（02 §2 门面；不可 pickle——持内存密钥）。"""

    def __init__(
        self,
        root: Path,
        keys: dict[str, bytes],
        *,
        mode: str = MODE_ENCRYPTED,
        secrets_salt: bytes | None = None,
        secrets_verifier: bytes | None = None,
    ) -> None:
        self._root = Path(root)
        self._mode = mode
        self._keys = keys  # 加密分区名 → 分区密钥（明文分区分区不在其中）
        self._secrets_salt = secrets_salt
        self._secrets_verifier = secrets_verifier
        self._manifests: dict[str, PartitionManifest] = {}
        self._states: dict[str, str] = {
            p.name: "locked" if self._is_locked(p.name) else "ok" for p in PARTITIONS
        }
        self._reports: dict[str, CorruptedPartitionReport] = {}
        self._tmp_dir = self._root / TMP_DIR_NAME  # 原子替换的中间产物（同卷，见 02 §2.4）
        # 分区级互斥（02 §2.4）：同分区读写串行化，分区间互不阻塞
        self._part_locks: dict[str, threading.RLock] = {}
        self._part_locks_guard = threading.Lock()

    # ───────────────────────── 格式与解锁态 ─────────────────────────

    @property
    def mode(self) -> str:
        """本根的落盘格式（``plain`` / ``encrypted``；02 §2.2）。"""
        return self._mode

    @property
    def secrets_unlocked(self) -> bool:
        """`secrets` 分区当前是否可读写（惰性解锁态）。"""
        return not self._is_locked(_SECRETS)

    def _is_locked(self, partition: PartitionName) -> bool:
        """该分区是否因未解锁而不可读写（只有 `secrets` 可能出现，02 §2.2）。"""
        return partition == _SECRETS and partition not in self._keys

    def _part_key(self, partition: PartitionName) -> bytes | None:
        """该分区的落盘密钥；``None`` ＝ 明文分区（免密钥，02 §2.2）。

        未解锁的 `secrets` → :class:`StorageSecretsLockedError`（显式，不静默）。
        """
        if partition == _SECRETS:
            key = self._keys.get(partition)
            if key is None:
                raise StorageSecretsLockedError(
                    "secrets 分区未解锁（凭据恒加密、惰性解锁，02 §2.2）；"
                    "请先 Store.unlock_secrets(凭据口令)"
                )
            return key
        if self._mode == MODE_PLAIN:
            return None
        return self._keys[partition]

    def unlock_secrets(self, passphrase: str) -> None:
        """解锁 `secrets` 分区（02 §2.2 / §3 惰性解锁）。

        明文根的该口令**首次解锁时设定**（TOFU）：此时凭据分区必为空（写入
        凭据的前提就是已解锁），故不会顶替任何既有数据；此后错口令即拒。
        解密后的 `secrets` 若有损坏，随本调用显式上抛。
        """
        if not isinstance(passphrase, str) or not passphrase.strip():
            raise StoragePassphraseRequired(
                "凭据口令不得为空", kind="credentials"
            )
        if self._secrets_salt is None:
            raise StorageOpenError("存储缺少 secrets salt，无法解锁凭据分区")
        key = derive_master_key(passphrase, self._secrets_salt)
        if self._secrets_verifier is None:
            if self._has_any_file(_SECRETS):
                raise StorageOpenError(
                    "凭据分区有数据却无校验锚：存储结构异常，拒绝解锁（02 §2.3）"
                )
            self._secrets_verifier = make_verifier(key)
            self._write_keyfile()
        elif not check_verifier(key, self._secrets_verifier):
            raise StoragePassphraseRequired(
                "凭据口令错误（校验锚认证失败）；凭据口令丢失即该分区不可恢复",
                kind="credentials",
            )
        self._keys[_SECRETS] = key
        self._states[_SECRETS] = "ok"
        self._reports.pop(_SECRETS, None)
        # 明文根首次解锁：凭据清单随密钥一并就位（create 时尚无密钥可封清单）。
        # 若清单缺失而数据文件已在，那是清单真丢了 —— 按 §2.3 报损坏，不静默重建。
        if not (self._root / _SECRETS / MANIFEST_NAME).is_file():
            if self._has_any_file(_SECRETS):
                report = CorruptedPartitionReport(
                    partition=_SECRETS, reason="分区清单缺失（数据文件仍在）",
                    affected_files=(),
                )
                self._states[_SECRETS] = "corrupted"
                self._reports[_SECRETS] = report
                raise StorageCorruptionError(self.storage_report())
            self._write_manifest(_SECRETS, PartitionManifest(entries=()))
        report = self._verify_partition(_SECRETS)
        if report is not None:
            self._states[_SECRETS] = "corrupted"
            self._reports[_SECRETS] = report
            raise StorageCorruptionError(self.storage_report())

    def _has_any_file(self, partition: PartitionName) -> bool:
        """分区目录下是否存在清单之外的数据文件（TOFU 前置判定用，不解密）。"""
        return _has_files_under(self._root / partition)

    def _write_keyfile(self) -> None:
        """回写 keyfile（仅在补写凭据校验锚时发生；主字段原样保留）。"""
        kf = self._root / _KEYFILE
        try:
            meta = json.loads(kf.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise StorageOpenError(f"keyfile 损坏，无法写入校验锚: {exc}") from exc
        if self._secrets_verifier is not None:
            meta["verifier_secrets"] = self._secrets_verifier.hex()
        self._atomic_write(kf, json.dumps(meta, indent=2).encode("utf-8"))

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

    def _reset_tmp_dir(self) -> None:
        """(重)建中间产物目录并清空其中的崩溃残留（02 §2.4）。

        整目录回收而非按文件名模式匹配：该目录**只**由 ``_atomic_write`` 写入，
        故清空不可能触及用户数据。明文根下残留可能是明文（明文分区不经
        ``seal_bytes``），但该目录**只**承载「正在替换中的临时副本」，与目标
        文件同源同权限，不引入新的暴露面；回收不涉数据主权。
        """
        if self._tmp_dir.exists():
            shutil.rmtree(self._tmp_dir)
        self._tmp_dir.mkdir(parents=True)

    def _atomic_write(self, target: Path, data: bytes) -> None:
        """中间产物目录内临时文件 + ``os.replace`` 原子替换（02 §2.4 落盘原子性）。

        中间产物写在 ``root/.st-agent-tmp/``（与目标同卷，保证替换原子）、不落
        分区目录；失败（含中断）只可能在那里留下一个残留，**不会**让 ``target``
        处于半截状态——旧内容完整保留。残留由下次 ``Store.open`` 回收。

        ``PermissionError`` 有界重试：Windows 的 ``MoveFileEx`` 会在目标 / 临时
        文件被**外部句柄短暂持有**时报 ``EACCES``（实时扫描、索引器是常见来源），
        这是 tmp+replace 模式在 Windows 上的已知代价、CI（Linux）照不到。重试次数
        有界，**持续冲突照样显式失败**（不静默、不降级为非原子写）；其余 ``OSError``
        （磁盘满、路径不存在等）不重试。
        """
        tmp = self._tmp_dir / f"{target.name}.tmp-{os.urandom(6).hex()}"
        try:
            tmp.write_bytes(data)
            for attempt in range(_REPLACE_ATTEMPTS):
                try:
                    os.replace(tmp, target)
                except PermissionError:
                    if attempt == _REPLACE_ATTEMPTS - 1:
                        raise
                    time.sleep(_REPLACE_BACKOFF_S * (attempt + 1))
                else:
                    break
        except OSError:
            tmp.unlink(missing_ok=True)
            raise

    # ───────────────────────── 初始化与打开 ─────────────────────────

    @classmethod
    def exists(cls, root: Path | str) -> bool:
        """盘上是否已有存储（``store.json`` 或 ``keyfile.json`` 任一存在即算）。

        供上层入口判定「打开既有根」还是「初始化新根」。判据与 :meth:`create`
        的拒绝条件、:meth:`open` 的接受条件**同源**（同一对文件名常量），故收在 L0——
        调用方各自拼文件名迟早与这里漂移。
        """
        root = Path(root)
        return (root / STORE_MARKER).exists() or (root / _KEYFILE).exists()

    @classmethod
    def passphrase_requirement(cls, root: Path | str) -> str | None:
        """**只读探测**：本根要「打开 + 凭据可用」需要哪种口令（[02 §2.2 / §3]）。

        - ``None``——免口令：根不存在（将按明文新建）、或明文根且凭据分区**从未建立**
        - ``"main_passphrase"``——**加密根**的主密码（标记缺失的历史根同此，02 §2.2）
        - ``"credentials"``——明文根的**凭据口令**（凭据恒加密、惰性解锁；已建立才需要）

        **为什么需要它**：装配对凭据的播种是**幂等**的（端点已存在即整组跳过），故
        「凭据已建立」的明文根**装配能过、不进交互**，却在**第一次读凭据**（如首次 LLM
        调用）才撞上未解锁——把失败推到用户看不见的地方。启动期据此提前索取，口令在
        `open` 时就位。

        **只读标记与 keyfile**：不打开、不解密、不校验分区——它是「要不要问口令」的判据，
        不是安全检查（真校验仍在 :meth:`open` 里）。标记 / keyfile 损坏照旧抛
        :class:`StorageOpenError`（不猜模式、不猜口令）。
        """
        root = Path(root)
        if not cls.exists(root):
            return None
        meta = _read_keyfile_meta(root)
        mode = read_mode(root)
        if mode == MODE_ENCRYPTED or (mode is None and "salt" in meta):
            return "main_passphrase"
        if meta.get("verifier_secrets") or _has_files_under(root / _SECRETS):
            return "credentials"
        return None

    @classmethod
    def create(cls, root: Path | str, passphrase: str | None = None) -> "Store":
        """首次初始化（盘上无存储时）。已存在存储则拒绝（防误覆盖）。

        ``passphrase=None`` ⇒ **明文模式（默认）**：免口令、六分区明文落盘；
        给出 ⇒ **加密模式**：全部分区由该主密码派生的密钥加密（与现状同）。
        """
        root = Path(root)
        kf = root / _KEYFILE
        if kf.exists() or (root / STORE_MARKER).exists():
            raise StorageOpenError(
                f"{root} 已有存储（keyfile / store.json 存在）；初始化新存储请换目录或先完全清空"
            )
        secrets_salt = os.urandom(32)                     # §2.2 单独隔离
        root.mkdir(parents=True, exist_ok=True)
        if passphrase is None:
            write_marker(root, MODE_PLAIN)
            kf.write_text(json.dumps({
                "version": 1,
                "kdf": "pbkdf2-sha256",
                "iterations_hint": KEYFILE_ITERATIONS_HINT,
                "secrets_salt": secrets_salt.hex(),
            }, indent=2), encoding="utf-8")
            store = cls(root, {}, mode=MODE_PLAIN, secrets_salt=secrets_salt)
        else:
            if not passphrase.strip():
                raise StorageOpenError("主密码不得为空串（要不加密请不传口令）")
            salt = os.urandom(32)
            master = derive_master_key(passphrase, salt)
            secrets_key = derive_master_key(passphrase, secrets_salt)
            write_marker(root, MODE_ENCRYPTED)
            kf.write_text(json.dumps({
                "version": 1,
                "kdf": "pbkdf2-sha256",
                "iterations_hint": KEYFILE_ITERATIONS_HINT,
                "salt": salt.hex(),
                "secrets_salt": secrets_salt.hex(),
                "verifier": make_verifier(master).hex(),
                "verifier_secrets": make_verifier(secrets_key).hex(),
            }, indent=2), encoding="utf-8")
            store = cls._unlock(root, master, secrets_key, secrets_salt=secrets_salt)
        store._reset_tmp_dir()          # 中间产物目录（02 §2.4）须先于任何写入就位
        for p in PARTITIONS:
            d = root / p.name
            d.mkdir(exist_ok=True)
            if p.name == _SECRETS and not store.secrets_unlocked:
                continue        # 凭据分区无密钥 → 其清单在首次解锁时就位（TOFU）
            store._write_manifest(p.name, PartitionManifest(entries=()))
        return store

    @classmethod
    def open(cls, root: Path | str, passphrase: str | None = None) -> "Store":
        """打开既有存储：按格式标记分派；密码错 → 拒绝；分区损坏 → 详见异常报告。

        明文根免口令（02 §2.2）；加密根必须给主密码，且标记缺失的历史根按加密
        口径读（A2）。给出主密码时若该密码同时满足凭据校验锚则一并解锁 `secrets`，
        否则凭据保持锁定（02 §3）。打开时**回收**中间产物目录（02 §2.4）。
        """
        root = Path(root)
        meta = _read_keyfile_meta(root)
        mode = read_mode(root)
        encrypted = mode == MODE_ENCRYPTED or (mode is None and "salt" in meta)
        try:
            secrets_salt = bytes.fromhex(meta["secrets_salt"])
        except (KeyError, ValueError) as exc:
            raise StorageOpenError(f"keyfile 字段缺失或非法（secrets_salt）: {exc}") from exc
        if encrypted:
            if passphrase is None:
                raise StoragePassphraseRequired(
                    "该存储为加密格式，打开需提供主密码（02 §2.2）",
                    kind="main_passphrase",
                )
            try:
                salt = bytes.fromhex(meta["salt"])
                verifier = bytes.fromhex(meta["verifier"])
            except (KeyError, ValueError) as exc:
                raise StorageOpenError(f"keyfile 字段缺失或非法: {exc}") from exc
            # 凭据校验锚**可缺**：源明文根从未设过凭据口令时，转换到加密根也不带锚，
            # 待首次 unlock_secrets 时补写（TOFU，02 §3）——故此处不当作损坏。
            raw_secrets = meta.get("verifier_secrets")
            try:
                verifier_secrets = (bytes.fromhex(raw_secrets)
                                    if raw_secrets else None)
            except ValueError as exc:
                raise StorageOpenError(f"keyfile 字段非法（verifier_secrets）: {exc}") from exc
            master = derive_master_key(passphrase, salt)
            if not check_verifier(master, verifier):
                raise StoragePassphraseRequired(
                    "主密码错误（校验锚认证失败）；密码丢失即数据不可恢复（02 §2.2，"
                    "无产品方恢复通道）",
                    kind="main_passphrase",
                )
            store = cls._unlock(root, master, None, secrets_salt=secrets_salt,
                                secrets_verifier=verifier_secrets)
            store._reset_tmp_dir()                 # 回收上次的崩溃残留（E2 / 02 §2.4）
            store._try_unlock_secrets(passphrase)  # 凭据口令可与主密码不同（§3）
        else:
            try:
                raw_verifier = meta.get("verifier_secrets")
                secrets_verifier = (bytes.fromhex(raw_verifier)
                                    if raw_verifier else None)
            except ValueError as exc:
                raise StorageOpenError(f"keyfile 字段非法（verifier_secrets）: {exc}") from exc
            store = cls(root, {}, mode=MODE_PLAIN, secrets_salt=secrets_salt,
                        secrets_verifier=secrets_verifier)
            store._reset_tmp_dir()
            if passphrase is not None:
                store.unlock_secrets(passphrase)   # 惰性解锁（TOFU，§2.2）
        report = store._verify_all()
        if not report.is_clean:
            raise StorageCorruptionError(report)
        return store

    def _try_unlock_secrets(self, passphrase: str) -> None:
        """加密根上尝试解锁凭据分区：口令不匹配凭据锚 ⇒ 保持锁定（不报错）。

        既有加密根（主密码 = 凭据口令）行为逐字节不变：两锚同时通过 → 全解锁。
        凭据口令与主密码不同的根（转换产物，见 :meth:`convert`）在此保持
        `secrets` 锁定，由调用方显式 ``unlock_secrets``。
        """
        if self._secrets_verifier is None or self._secrets_salt is None:
            return
        key = derive_master_key(passphrase, self._secrets_salt)
        if not check_verifier(key, self._secrets_verifier):
            return
        self._keys[_SECRETS] = key
        self._states[_SECRETS] = "ok"

    @classmethod
    def _unlock(
        cls,
        root: Path,
        master: bytes,
        secrets_key: bytes | None,
        *,
        secrets_salt: bytes | None = None,
        secrets_verifier: bytes | None = None,
    ) -> "Store":
        """加密根的句柄构造：逐分区派生密钥（secrets 用独立盐派生的那把）。"""
        keys: dict[str, bytes] = {}
        for p in PARTITIONS:
            if p.secrets_isolated:
                if secrets_key is not None:
                    keys[p.name] = secrets_key
            else:
                keys[p.name] = derive_partition_key(master, p.name)
        return cls(root, keys, mode=MODE_ENCRYPTED,
                   secrets_salt=secrets_salt, secrets_verifier=secrets_verifier)

    # ───────────────────────── 明密双向转换（GWT-2） ─────────────────────────

    @classmethod
    def convert(
        cls,
        source: Path | str,
        target: Path | str,
        *,
        to_format: str,
        source_passphrase: str | None = None,
        target_passphrase: str | None = None,
    ) -> "Store":
        """把 ``source`` 根转换到 ``target`` 格式（**源不动、写新根**，A4）。

        - `to_format="plain"` ⇒ 目标六分区明文；``target_passphrase`` 不需要
        - `to_format="encrypted"` ⇒ 全分区加密，``target_passphrase`` 必填
        - ``source_passphrase``：加密源必填（要读六分区）；明文源不需要

        `secrets` 分区的**密钥材料与密文原样搬运**，故**转换不改凭据口令**；
        加密目标上凭据口令与目标主密码不同时，目标根的 `secrets` 保持锁定，
        由调用方 ``unlock_secrets`` 打开（02 §3）。

        中断安全：全过程写在一个临时目录，收尾整目录 ``os.replace`` —— 要么
        完整新根、要么无（不留半格式根）。
        """
        if to_format not in (MODE_PLAIN, MODE_ENCRYPTED):
            raise StorageOpenError(
                f"to_format 须为 {MODE_PLAIN!r} 或 {MODE_ENCRYPTED!r}：{to_format!r}"
            )
        if to_format == MODE_ENCRYPTED and not (target_passphrase or "").strip():
            # 不设口令就等于建出**明文**根——静默偏离调用方的「要加密」意图，
            # 属安全面不得发生：显式拒绝（既不空口令加密，也不悄悄降级）
            raise StorageOpenError(
                "to_format='encrypted' 须给出 target_passphrase（目标根主密码）"
            )
        source, target = Path(source), Path(target)
        if not (source / _KEYFILE).exists():
            raise StorageOpenError(f"源 {source} 无存储（keyfile 不存在）")
        if source.resolve() == target.resolve():
            raise StorageOpenError("源与目标不得为同一目录（转换恒「源不动、写新根」）")
        if target.exists():
            if any(target.iterdir()):
                raise StorageOpenError(f"目标根 {target} 非空；转换只写全新根")
            target.rmdir()
        staging = target.parent / f".{target.name}.convert-{os.urandom(6).hex()}"
        src = cls.open(source, source_passphrase)
        try:
            cls._copy_into(src, staging, to_format, target_passphrase)
            os.replace(staging, target)
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        return cls.open(target, target_passphrase if to_format == MODE_ENCRYPTED else None)

    @classmethod
    def _copy_into(
        cls, src: "Store", staging: Path, to_format: str, target_passphrase: str | None
    ) -> None:
        """在 ``staging`` 建出目标根：六分区按目标格式重写，`secrets` 原样搬运。"""
        target = cls.create(staging, target_passphrase if to_format == MODE_ENCRYPTED else None)
        # 凭据材料原样搬运：salt + 校验锚（+ 若无锚则目标也无锚，保持 TOFU 语义）
        src_meta = json.loads((src._root / _KEYFILE).read_text(encoding="utf-8"))
        target_meta = json.loads((staging / _KEYFILE).read_text(encoding="utf-8"))
        target_meta["secrets_salt"] = src_meta["secrets_salt"]
        if "verifier_secrets" in src_meta:
            target_meta["verifier_secrets"] = src_meta["verifier_secrets"]
        else:
            target_meta.pop("verifier_secrets", None)
        (staging / _KEYFILE).write_text(
            json.dumps(target_meta, indent=2), encoding="utf-8"
        )
        for p in PARTITIONS:
            if p.name == _SECRETS:
                continue
            for name in src.list_files(p.name):
                target.put(p.name, name, src.get(p.name, name))
        # secrets 目录整目录原样搬运（含其清单密文）；目标分区目录已由 create 建出。
        # 目标句柄自此作废（其内存清单/密钥与搬运后的盘上内容不再一致），
        # 故 :meth:`convert` 收尾一律重新 ``open`` 目标根取新句柄。
        secrets_dir = staging / _SECRETS
        if secrets_dir.exists():
            shutil.rmtree(secrets_dir)
        src_secrets = src._root / _SECRETS
        if src_secrets.exists():
            shutil.copytree(src_secrets, secrets_dir)
        else:  # pragma: no cover - create 必建该目录
            secrets_dir.mkdir(parents=True, exist_ok=True)

    # ───────────────────────── 读写（加密透明） ─────────────────────────

    def put(self, partition: PartitionName, name: str, data: bytes) -> None:
        """写入/覆盖一个文件（清单与密文同批更新，GWT-3 往返一致）。"""
        validate_partition_name(partition)
        with self._part_lock(partition):
            self._require_ok(partition)
            self._check_rel_name(name)
            manifest = self._manifest(partition)
            sealed = self._seal(partition, data)
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
            plain = self._open(partition, sealed)
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
            self._require_ok(partition)
            return tuple(sorted(self._manifest(partition).paths()))

    def sealed_mtime(self, partition: PartitionName, name: str) -> float:
        """分区内文件的**落盘** mtime（epoch 秒；T-L0-006 留存年龄口径）。

        明文根下该文件是明文，故名字里的「sealed」只表示「落盘形态」——
        口径本身（按落盘文件的实际时间计龄）不变。
        """
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
        """查询分区状态（ok / corrupted / locked；GWT-5 损坏范围查询）。"""
        validate_partition_name(partition)
        with self._part_lock(partition):
            report = self._reports.get(partition)
            if report:
                return StorageState(
                    partition=partition, state="corrupted",
                    file_count=len(self._manifests.get(partition, PartitionManifest()).entries),
                    detail=report.reason,
                )
            if self._states.get(partition) == "locked":
                return StorageState(
                    partition=partition, state="locked", file_count=0,
                    detail="未解锁（凭据恒加密、惰性解锁；请先 unlock_secrets 提供凭据口令）",
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

        单分区失败不影响其余分区（GWT-5「其余分区不受影响」）。未解锁的分区
        **不校验、不报告损坏**——它既没被读，也不该被谎报为健康（02 §2.2）。
        """
        reports: list[CorruptedPartitionReport] = []
        healthy: list[PartitionName] = []
        for p in PARTITIONS:
            if self._is_locked(p.name):
                self._states[p.name] = "locked"
                continue
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
            manifest = self._load_manifest(partition, mf.read_bytes())
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
                plain = self._open(partition, f.read_bytes())
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

    def _seal(self, partition: PartitionName, plaintext: bytes) -> bytes:
        """文件载荷按分区格式落盘形态（明文分区分区原样）。"""
        key = self._part_key(partition)
        return plaintext if key is None else seal_bytes(key, plaintext, aad=_AAD_FILE)

    def _open(self, partition: PartitionName, sealed: bytes) -> bytes:
        """文件载荷的读回（明文分区原样；完整性由清单 digest 兜底，§2.3）。"""
        key = self._part_key(partition)
        return sealed if key is None else open_bytes(key, sealed, aad=_AAD_FILE)

    def _load_manifest(self, partition: PartitionName, raw: bytes) -> PartitionManifest:
        key = self._part_key(partition)
        if key is None:
            return PartitionManifest.from_json(raw.decode("utf-8"))
        return PartitionManifest.open(key, raw)

    def _dump_manifest(self, partition: PartitionName, manifest: PartitionManifest) -> bytes:
        key = self._part_key(partition)
        if key is None:
            return manifest.to_json().encode("utf-8")
        return seal_bytes(key, manifest.to_json().encode("utf-8"), aad=_AAD_MANIFEST)

    def _manifest(self, partition: PartitionName) -> PartitionManifest:
        m = self._manifests.get(partition)
        if m is None:
            mf = self._root / partition / MANIFEST_NAME
            m = self._load_manifest(partition, mf.read_bytes())
            self._manifests[partition] = m
        return m

    def _write_manifest(self, partition: PartitionName, manifest: PartitionManifest) -> None:
        self._atomic_write(self._root / partition / MANIFEST_NAME,
                           self._dump_manifest(partition, manifest))
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
        if self._states.get(partition) == "locked":
            self._part_key(partition)      # 抛 StorageSecretsLockedError（显式）
        if self._states.get(partition) != "ok":
            raise StorageCorruptionError(self.storage_report())

    @staticmethod
    def _check_rel_name(name: str) -> None:
        if (not name or "\\" in name or name.startswith(("/", "~"))
                or ".." in name.split("/") or name == MANIFEST_NAME):
            raise ValueError(f"非法文件名（须为分区内安全相对路径，且不得占用 {MANIFEST_NAME}）: {name!r}")
