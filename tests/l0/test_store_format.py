"""T-L0-018.1 测试：存储加密可选（默认关）与明密双向转换（02 §2.2 / §2.3 / §3）。

GWT 对照（任务文件 5 条）：
- GWT-1 加密默认可关且格式自辨：不带口令即明文落盘；两种根都由 ``open`` 自行
  分派；格式标记人可读、不含密钥材料；既有加密根行为逐字节不变（无标记按加密读）
- GWT-2 明密双向转换：分区集合 / 文件相对路径 / 读回明文逐字节等价；可逆；
  中断不留半格式根；**不改凭据口令**
- GWT-3 完整性不随降密而降：明文模式篡改仍报损坏（digest + 原子替换保留）；
  ``secrets`` 仍为密文（惰性解锁）
- GWT-4 落盘往返降耗：明文模式六分区**零加解密调用**（结构性证据；实测对比见执行日志）
- GWT-5 契约先行：由 ``verify_docs.py --strict`` 与文档变更覆盖（无代码面）

「零加解密」：明文模式下 `secrets` 仍加密（02 §2.2 凭据恒加密），故其读写照常
调用原语——本条只对**其余六分区**成立。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from st_agent.l0.storage import (
    MODE_ENCRYPTED,
    MODE_PLAIN,
    PARTITION_NAMES,
    STORE_MARKER,
    StorageCorruptionError,
    StorageOpenError,
    StorageSecretsLockedError,
    Store,
    read_mode,
)
from st_agent.l0.storage import store as store_mod

PASS = "correct horse battery staple"
CRED = "credential-passphrase"
PLAIN = b"hello-plain-store"
SECRET = b"sk-super-secret-value"
SIX = tuple(p for p in PARTITION_NAMES if p != "secrets")


def _fill(store: Store, *, with_secret: bool = False, cred: str = CRED) -> None:
    store.put("memory", "nodes/a.json", b'{"n":1}')
    store.put("memory", "nodes/b.json", b'{"n":2}')
    store.put("config", "app.json", b'{"v":3}')
    store.put("data_cache", "deep/k.bin", PLAIN)
    if with_secret:
        store.unlock_secrets(cred)
        store.put("secrets", "cred/k1.json", SECRET)


def _snapshot(store: Store, partitions=SIX) -> dict[str, dict[str, bytes]]:
    """逐分区逐路径的**读回明文**（对比转换前后等价性用）。"""
    return {p: {n: store.get(p, n) for n in store.list_files(p)} for p in partitions}


def _cipher(root: Path, rel: str) -> bytes:
    return (root / rel).read_bytes()


# ───────────────────────── GWT-1 默认关 + 格式自辨 ─────────────────────────


class TestGwt1DefaultOff:
    def test_create_without_passphrase_is_plain(self, tmp_path: Path):
        root = tmp_path / "root"
        store = Store.create(root)
        assert store.mode == MODE_PLAIN
        store.put("memory", "a.json", PLAIN)
        assert _cipher(root, "memory/a.json") == PLAIN        # 明文可读、免口令
        assert read_mode(root) == MODE_PLAIN

    def test_marker_is_readable_and_has_no_key_material(self, tmp_path: Path):
        Store.create(tmp_path / "root")
        raw = (tmp_path / "root" / STORE_MARKER).read_text(encoding="utf-8")
        meta = json.loads(raw)
        assert meta["mode"] == MODE_PLAIN and "format" in meta
        assert "salt" not in raw and "verifier" not in raw and PASS not in raw

    def test_open_dispatches_without_caller_specifying_format(self, tmp_path: Path):
        plain, enc = tmp_path / "p", tmp_path / "e"
        Store.create(plain)
        Store.create(enc, PASS)
        assert Store.open(plain).mode == MODE_PLAIN           # 明文根免口令
        assert Store.open(enc, PASS).mode == MODE_ENCRYPTED
        with pytest.raises(StorageOpenError, match="加密"):
            Store.open(enc)                                   # 加密根免口令即拒

    def test_encrypted_root_behavior_unchanged(self, tmp_path: Path):
        root = tmp_path / "enc"
        store = Store.create(root, PASS)
        store.put("memory", "a.json", PLAIN)
        assert _cipher(root, "memory/a.json") != PLAIN        # 仍为密文
        with pytest.raises(StorageOpenError, match="主密码错误"):
            Store.open(root, "wrong")
        assert Store.open(root, PASS).get("memory", "a.json") == PLAIN

    def test_legacy_root_without_marker_reads_as_encrypted(self, tmp_path: Path):
        root = tmp_path / "legacy"
        Store.create(root, PASS).put("memory", "a.json", PLAIN)
        (root / STORE_MARKER).unlink()                        # 模拟机制落地前的既有根
        assert read_mode(root) is None
        with pytest.raises(StorageOpenError):
            Store.open(root)                                  # 无标记 ⇒ 按加密口径，须口令
        assert Store.open(root, PASS).get("memory", "a.json") == PLAIN

    def test_create_refuses_existing_store(self, tmp_path: Path):
        root = tmp_path / "root"
        Store.create(root)
        with pytest.raises(StorageOpenError, match="已有存储"):
            Store.create(root)


# ───────────────────────── GWT-2 明密双向转换 ─────────────────────────


class TestGwt2Convert:
    def test_plain_to_encrypted_roundtrip_equivalent(self, tmp_path: Path):
        src = tmp_path / "plain"
        _fill(Store.create(src), with_secret=True)
        before = _snapshot(Store.open(src, CRED), PARTITION_NAMES)

        enc = tmp_path / "enc"
        Store.convert(src, enc, to_format="encrypted", target_passphrase=PASS)
        assert read_mode(enc) == MODE_ENCRYPTED
        assert _cipher(enc, "memory/nodes/a.json") != b'{"n":1}'   # 目标确实加密了
        enc_store = Store.open(enc, PASS)
        enc_store.unlock_secrets(CRED)
        assert _snapshot(enc_store, PARTITION_NAMES) == before

        back = tmp_path / "plain-again"
        Store.convert(enc, back, to_format="plain", source_passphrase=PASS)
        assert read_mode(back) == MODE_PLAIN
        assert _snapshot(Store.open(back, CRED), PARTITION_NAMES) == before

    def test_conversion_keeps_credential_passphrase(self, tmp_path: Path):
        src = tmp_path / "plain"
        _fill(Store.create(src), with_secret=True)

        enc = tmp_path / "enc"
        Store.convert(src, enc, to_format="encrypted", target_passphrase=PASS)
        reopened = Store.open(enc, PASS)
        assert not reopened.secrets_unlocked                  # 凭据口令 ≠ 目标主密码
        with pytest.raises(StorageOpenError, match="凭据口令错误"):
            reopened.unlock_secrets(PASS)                     # 目标主密码开不了凭据
        reopened.unlock_secrets(CRED)                         # 源凭据口令照旧
        assert reopened.get("secrets", "cred/k1.json") == SECRET

    def test_secrets_material_moved_verbatim(self, tmp_path: Path):
        src = tmp_path / "plain"
        _fill(Store.create(src), with_secret=True)

        enc = tmp_path / "enc"
        Store.convert(src, enc, to_format="encrypted", target_passphrase=PASS)
        assert _cipher(enc, "secrets/cred/k1.json") == _cipher(src, "secrets/cred/k1.json")
        salts = [
            json.loads((r / "keyfile.json").read_text(encoding="utf-8"))["secrets_salt"]
            for r in (src, enc)
        ]
        assert salts[0] == salts[1]                           # 凭据 salt 原样搬运

    def test_repeated_conversion_is_equivalent(self, tmp_path: Path):
        src = tmp_path / "plain"
        _fill(Store.create(src))
        once, twice = tmp_path / "once", tmp_path / "twice"
        Store.convert(src, once, to_format="encrypted", target_passphrase=PASS)
        Store.convert(src, twice, to_format="encrypted", target_passphrase=PASS)
        # 幂等 = 对同一源重复转换得到**等价**根（密文因随机 nonce / salt 不同，
        # 但分区集合、相对路径与读回明文一致）
        assert _snapshot(Store.open(once, PASS)) == _snapshot(Store.open(twice, PASS))

    def test_target_must_be_fresh(self, tmp_path: Path):
        src = tmp_path / "plain"
        _fill(Store.create(src))
        busy = tmp_path / "busy"
        busy.mkdir()
        (busy / "junk").write_text("x", encoding="utf-8")
        with pytest.raises(StorageOpenError, match="非空"):
            Store.convert(src, busy, to_format="encrypted", target_passphrase=PASS)
        with pytest.raises(StorageOpenError, match="同一目录"):
            Store.convert(src, src, to_format="encrypted", target_passphrase=PASS)

    def test_bad_to_format_rejected(self, tmp_path: Path):
        src = tmp_path / "plain"
        _fill(Store.create(src))
        with pytest.raises(StorageOpenError, match="to_format"):
            Store.convert(src, tmp_path / "x", to_format="rot13")

    def test_encrypted_target_requires_passphrase(self, tmp_path: Path):
        src = tmp_path / "plain"
        _fill(Store.create(src))
        with pytest.raises(StorageOpenError, match="target_passphrase"):
            Store.convert(src, tmp_path / "x", to_format="encrypted")
        assert not (tmp_path / "x").exists()                  # 不悄悄降级为明文根

    def test_encrypted_source_requires_passphrase(self, tmp_path: Path):
        src = tmp_path / "enc"
        Store.create(src, PASS).put("memory", "a.json", PLAIN)
        with pytest.raises(StorageOpenError, match="加密"):
            Store.convert(src, tmp_path / "x", to_format="plain")

    def test_interrupted_conversion_leaves_no_half_root(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        src, target = tmp_path / "plain", tmp_path / "out"
        _fill(Store.create(src))

        def boom(self, partition, name):
            raise OSError("模拟中途失败")

        monkeypatch.setattr(store_mod.Store, "get", boom)
        with pytest.raises(OSError):
            Store.convert(src, target, to_format="encrypted", target_passphrase=PASS)
        monkeypatch.undo()
        assert not target.exists()                            # 要么完整新根、要么无
        assert not list(tmp_path.glob(".out.convert-*"))      # 临时目录已清
        assert Store.open(src).get("memory", "nodes/a.json") == b'{"n":1}'   # 源完好

    def test_source_missing_keyfile_rejected(self, tmp_path: Path):
        src = tmp_path / "empty"
        src.mkdir()
        with pytest.raises(StorageOpenError, match="无存储"):
            Store.convert(src, tmp_path / "out", to_format="plain")


# ───────────────────────── GWT-3 降密不降完整性 ─────────────────────────


class TestGwt3IntegrityKept:
    def test_plain_mode_tamper_is_detected(self, tmp_path: Path):
        root = tmp_path / "root"
        store = Store.create(root)
        store.put("memory", "a.json", PLAIN)
        (root / "memory" / "a.json").write_bytes(PLAIN + b"tampered")
        with pytest.raises(StorageCorruptionError):
            Store.open(root)
        with pytest.raises(StorageCorruptionError):
            store.get("memory", "a.json")

    def test_plain_mode_keeps_manifest_and_atomic_replace(self, tmp_path: Path):
        root = tmp_path / "root"
        store = Store.create(root)
        store.put("memory", "a.json", PLAIN)
        manifest = json.loads((root / "memory" / "manifest.bin").read_text("utf-8"))
        entry = manifest["entries"][0]
        assert entry["size"] == len(PLAIN)                     # 清单照旧是明文可解析的
        assert entry["digest"] == hashlib.sha256(PLAIN).hexdigest()
        assert (root / store_mod.TMP_DIR_NAME).is_dir()         # 原子替换中间产物目录
        (root / store_mod.TMP_DIR_NAME / "leftover.tmp").write_bytes(b"x")
        Store.open(root)
        assert list((root / store_mod.TMP_DIR_NAME).iterdir()) == []   # 打开即回收

    def test_secrets_stays_encrypted_and_lazily_unlocked(self, tmp_path: Path):
        root = tmp_path / "root"
        store = Store.create(root)
        assert not store.secrets_unlocked
        with pytest.raises(StorageSecretsLockedError):
            store.get("secrets", "cred/k1.json")
        assert store.partition_state("secrets").state == "locked"

        store.unlock_secrets(CRED)                            # 首次解锁即设定（TOFU）
        store.put("secrets", "cred/k1.json", SECRET)
        assert SECRET not in _cipher(root, "secrets/cred/k1.json")
        assert store.partition_state("secrets").state == "ok"

        reopened = Store.open(root)
        assert not reopened.secrets_unlocked                  # 免口令开根，但凭据仍锁
        with pytest.raises(StorageOpenError, match="凭据口令错误"):
            reopened.unlock_secrets("nope")
        reopened.unlock_secrets(CRED)
        assert reopened.get("secrets", "cred/k1.json") == SECRET

    def test_secrets_manifest_lost_with_data_is_corruption(self, tmp_path: Path):
        root = tmp_path / "root"
        store = Store.create(root)
        store.unlock_secrets(CRED)
        store.put("secrets", "cred/k1.json", SECRET)
        (root / "secrets" / "manifest.bin").unlink()
        with pytest.raises(StorageCorruptionError):
            Store.open(root).unlock_secrets(CRED)


# ───────────────────────── GWT-4 落盘往返降耗 ─────────────────────────


class TestGwt4NoCryptoInPlainMode:
    @staticmethod
    def _boom(*args, **kwargs):  # pragma: no cover - 命中即失败
        raise AssertionError("明文模式六分区不得触达加解密原语")

    def test_plain_mode_never_calls_crypto(self, tmp_path: Path, monkeypatch):
        root = tmp_path / "root"
        Store.create(root).put("memory", "a.json", PLAIN)
        monkeypatch.setattr(store_mod, "seal_bytes", self._boom)
        monkeypatch.setattr(store_mod, "open_bytes", self._boom)
        # 免口令打开 ⇒ 含逐分区清单解析 + 逐文件校验和比对，全程零加解密
        assert Store.open(root).get("memory", "a.json") == PLAIN

    def test_encrypted_mode_does_call_crypto(self, tmp_path: Path, monkeypatch):
        calls = {"n": 0}
        real_seal = store_mod.seal_bytes

        def counting(key, data, *, aad=b""):
            calls["n"] += 1
            return real_seal(key, data, aad=aad)

        monkeypatch.setattr(store_mod, "seal_bytes", counting)
        Store.create(tmp_path / "root", PASS).put("memory", "a.json", PLAIN)
        assert calls["n"] > 0                                  # 加密模式照旧加解密


class TestPassphraseRequirementProbe:
    """`Store.passphrase_requirement` 与 `StoragePassphraseRequired`（[`T-UI-002.4.1`]）。

    探测是「**要不要问口令**」的只读判据（不打开、不解密）；「给口令就能过」的两族
    失败另有专类，故调用方只对它们重试。
    """

    def test_probe_reports_none_for_missing_and_plain_roots(self, tmp_path: Path):
        assert Store.passphrase_requirement(tmp_path / "nope") is None   # 新根 ⇒ 明文
        plain = tmp_path / "plain"
        Store.create(plain)
        assert Store.passphrase_requirement(plain) is None               # 从未设过凭据口令

    def test_probe_reports_main_passphrase_for_encrypted_root(self, tmp_path: Path):
        root = tmp_path / "enc"
        Store.create(root, PASS)
        assert Store.passphrase_requirement(root) == "main_passphrase"

    def test_probe_reports_credentials_once_established(self, tmp_path: Path):
        """凭据已建立（TOFU 出校验锚）⇒ `credentials`；这是「装配幂等」留下的那个缺口。"""
        root = tmp_path / "plain"
        Store.create(root)
        Store.open(root, CRED)                                           # 首次解锁即 TOFU
        assert Store.passphrase_requirement(root) == "credentials"

    def test_probe_does_not_open_or_verify(self, tmp_path: Path):
        """探测不动分区：篡改一个数据文件后它照旧只谈「要不要口令」。"""
        root = tmp_path / "enc"
        Store.create(root, PASS).put("memory", "a.json", PLAIN)
        (root / "memory" / "a.json").write_bytes(b"tampered")
        assert Store.passphrase_requirement(root) == "main_passphrase"
        with pytest.raises(StorageCorruptionError):
            Store.open(root, PASS)                                       # 真校验仍在 open 里

    def test_probe_raises_on_corrupt_keyfile(self, tmp_path: Path):
        root = tmp_path / "plain"
        Store.create(root)
        (root / "keyfile.json").write_text("{ 这不是 JSON", encoding="utf-8")
        with pytest.raises(StorageOpenError, match="keyfile 损坏"):
            Store.passphrase_requirement(root)

    def test_open_failures_carry_machine_readable_kind(self, tmp_path: Path):
        """两族「给口令就能过」的失败携 `kind`（与「结构坏」区分开，供调用方定向重试）。"""
        from st_agent.l0.storage import StoragePassphraseRequired

        enc = tmp_path / "enc"
        Store.create(enc, PASS)
        with pytest.raises(StoragePassphraseRequired) as no_pass:
            Store.open(enc)
        assert no_pass.value.kind == "main_passphrase"
        with pytest.raises(StoragePassphraseRequired) as wrong:
            Store.open(enc, "wrong")
        assert wrong.value.kind == "main_passphrase"
        assert isinstance(wrong.value, StorageOpenError)                 # 子类，旧捕获面不变

        plain = tmp_path / "plain"
        Store.create(plain).unlock_secrets(CRED)
        with pytest.raises(StoragePassphraseRequired) as cred:
            Store.open(plain, "wrong")
        assert cred.value.kind == "credentials"
