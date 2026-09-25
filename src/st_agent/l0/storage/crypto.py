"""口令派生与 AEAD 加密原语（02 §2.2 加密要求）。

方案（④ 对齐确认，记执行日志）：
- 主密码 → 主密钥：PBKDF2-HMAC-SHA256，salt 32 字节随机（明文存于存储根的
  keyfile，salt 不泄密——攻击者需要主密码本身）；迭代 600_000（OWASP 2023
  推荐值；02 §2.2 明示「具体算法由实现阶段安全评审确定，不在此锁定」）
- 主密钥 → 分区密钥：HKDF-SHA256 派生（info = 分区名），每分区独立密钥；
  ``secrets`` 分区在此基础上**换独立 salt 重新走 PBKDF2**实现 §2.2「单独隔离」
- 内容加密：AES-256-GCM（AEAD）——同密钥下每个文件独立随机 nonce，
  认证失败即检测篡改（§2.3 损坏检测的第一道防线）

密码丢失 = 数据不可恢复（§2.2）：校验锚（verifier）本身是密文，错密码在
打开 verifier 时 GCM 认证失败，据此拒绝且不触碰任何分区数据。
"""

from __future__ import annotations

import os

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from st_agent.l0.storage.errors import CryptoError

__all__ = [
    "MASTER_KDF_ITERATIONS",
    "derive_master_key",
    "derive_partition_key",
    "seal_bytes",
    "open_bytes",
]

MASTER_KDF_ITERATIONS = 600_000
"""PBKDF2 迭代次数（OWASP 2023 推荐；安全评审可调，改动即主密钥不兼容）。"""

_SALT_LEN = 32
_NONCE_LEN = 12
_KEY_LEN = 32          # AES-256
_VERIFIER_PLAINTEXT = b"st-agent-storage-verifier-v1"


def derive_master_key(
    passphrase: str, salt: bytes, *, iterations: int = MASTER_KDF_ITERATIONS
) -> bytes:
    """主密码 → 主密钥（§2.2「加密密钥由用户主密码派生」）。"""
    if len(salt) != _SALT_LEN:
        raise CryptoError(f"salt 须为 {_SALT_LEN} 字节，实际 {len(salt)}")
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=_KEY_LEN,
        salt=salt,
        iterations=iterations,
    )
    return kdf.derive(passphrase.encode("utf-8"))


def derive_partition_key(master_key: bytes, partition: str) -> bytes:
    """主密钥 → 分区密钥（HKDF，info = 分区名）。

    每分区独立子密钥：单分区密钥泄露不波及其他分区；
    ``secrets`` 分区的隔离不在此处——见 :class:`Store` 的独立派生路径。
    """
    return HKDF(
        algorithm=hashes.SHA256(),
        length=_KEY_LEN,
        salt=b"st-agent/partition/v1",
        info=partition.encode("utf-8"),
    ).derive(master_key)


def seal_bytes(key: bytes, plaintext: bytes, *, aad: bytes = b"") -> bytes:
    """AES-256-GCM 加密（nonce 前置拼入密文，落盘格式 = nonce || ciphertext||tag）。"""
    nonce = os.urandom(_NONCE_LEN)
    ct = AESGCM(key).encrypt(nonce, plaintext, aad if aad else None)
    return nonce + ct


def open_bytes(key: bytes, sealed: bytes, *, aad: bytes = b"") -> bytes:
    """解密并认证；篡改/错密钥 → ``InvalidTag`` 包成 :class:`CryptoError`。"""
    if len(sealed) < _NONCE_LEN + 16:
        raise CryptoError("密文长度不足（nonce+tag 最小 28 字节），数据损坏或非本格式")
    nonce, ct = sealed[:_NONCE_LEN], sealed[_NONCE_LEN:]
    try:
        return AESGCM(key).decrypt(nonce, ct, aad if aad else None)
    except Exception as exc:  # InvalidTag 等认证失败统一显式化（01 §5 失败显式化）
        raise CryptoError("解密失败：密钥错误或数据被篡改（GCM 认证失败）") from exc


def make_verifier(key: bytes) -> bytes:
    """构造密码正确性校验锚——一段已知明文的密文。"""
    return seal_bytes(key, _VERIFIER_PLAINTEXT, aad=b"st-agent/verifier")


def check_verifier(key: bytes, sealed: bytes) -> bool:
    """校验主密码是否正确（不改数据，只验证锚点）。"""
    try:
        return open_bytes(key, sealed, aad=b"st-agent/verifier") == _VERIFIER_PLAINTEXT
    except CryptoError:
        return False
