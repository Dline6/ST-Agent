"""T-L0-002 测试：02-L0 §3 密钥与凭据子系统（掩码显示 + 使用记录）。

GWT 对照（任务文件 5 条）：
- GWT-1 加密落盘：三类凭据入 secrets 分区，盘上无明文
- GWT-2 掩码显示：默认只给掩码；完整值仅 use() 返回
- GWT-3 使用记录：时刻/发起方/目的/数据量可查，支持时间范围
- GWT-4 吊销重设：旧值不可恢复，使用记录保留
- GWT-5 零泄漏：repr/视图/导出物无明文
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from st_agent.l0.secrets import (
    CRED_PREFIX,
    USAGE_PREFIX,
    CredentialExistsError,
    CredentialNotFoundError,
    CredentialValidationError,
    CredentialVault,
    mask_secret,
)
from st_agent.l0.storage import Store

PASS = "correct horse battery staple"
LLM_KEY = "sk-proj-abcdef1234567890"
SMTP_PWD = "smtp-p@ss-9x8y7z"
WEBHOOK = "https://hooks.example.com/services/T123/B456/xxxx"


@pytest.fixture()
def vault(tmp_path: Path) -> CredentialVault:
    return CredentialVault(Store.create(tmp_path / "root", PASS))


def root_of(vault: CredentialVault) -> Path:
    return vault._store._root


# ───────────────────────── GWT-1 加密落盘 ─────────────────────────


class TestGwt1EncryptedAtRest:
    @pytest.mark.parametrize(
        ("cid", "kind", "value"),
        [
            ("openai-main", "llm_api_key", LLM_KEY),
            ("smtp-home", "smtp_credential", SMTP_PWD),
            ("deploy-hook", "webhook_url", WEBHOOK),
        ],
    )
    def test_three_kinds_encrypted(self, vault, cid, kind, value):
        vault.add(cid, kind, value)
        secret_files = [n for n in vault._store.list_files("secrets") if n != "manifest.bin"]
        assert secret_files == [f"{CRED_PREFIX}{cid}.json"]
        raw = (root_of(vault) / "secrets" / f"{CRED_PREFIX}{cid}.json").read_bytes()
        assert value.encode() not in raw  # 盘上无明文
        manifest_raw = (root_of(vault) / "secrets" / "manifest.bin").read_bytes()
        assert value.encode() not in manifest_raw
        assert cid.encode() not in manifest_raw  # 文件名在加密清单内

    def test_secrets_only_key_material(self, vault):
        """secrets 分区只存凭据文件（含统一前缀），无审计散落。"""
        vault.add("k1", "llm_api_key", LLM_KEY)
        for name in vault._store.list_files("secrets"):
            assert name.startswith(CRED_PREFIX)

    def test_duplicate_add_rejected(self, vault):
        vault.add("k1", "llm_api_key", LLM_KEY)
        with pytest.raises(CredentialExistsError, match="已存在"):
            vault.add("k1", "llm_api_key", "other-value")

    def test_bad_id_rejected(self, vault):
        for bad in ("", "../escape", "a/b", "x" * 65, "-lead", ".dot"):
            with pytest.raises(CredentialValidationError, match="标识"):
                vault.add(bad, "llm_api_key", LLM_KEY)

    def test_empty_value_rejected(self, vault):
        with pytest.raises(CredentialValidationError):
            vault.add("k1", "llm_api_key", "")


# ───────────────────────── GWT-2 掩码显示 ─────────────────────────


class TestGwt2Masking:
    def test_mask_rule_long(self):
        assert mask_secret(LLM_KEY) == "*" * (len(LLM_KEY) - 4) + LLM_KEY[-4:]

    def test_mask_rule_short(self):
        assert mask_secret("abcd") == "****"
        assert mask_secret("x") == "*"

    def test_get_view_only_masked(self, vault):
        vault.add("openai-main", "llm_api_key", LLM_KEY)
        view = vault.get_view("openai-main")
        assert view.masked == mask_secret(LLM_KEY)
        assert not hasattr(view, "value")
        assert LLM_KEY not in view.model_dump_json()

    def test_list_only_masked(self, vault):
        vault.add("a", "llm_api_key", LLM_KEY)
        vault.add("b", "smtp_credential", SMTP_PWD)
        views = vault.list_credentials()
        assert [v.credential_id for v in views] == ["a", "b"]
        for v in views:
            assert LLM_KEY not in v.model_dump_json()
            assert SMTP_PWD not in v.model_dump_json()

    def test_use_returns_plaintext_once(self, vault):
        vault.add("openai-main", "llm_api_key", LLM_KEY)
        assert vault.use("openai-main", initiator="l1-skill-x", purpose="日常问答") == LLM_KEY

    def test_use_missing_raises(self, vault):
        with pytest.raises(CredentialNotFoundError):
            vault.use("nope", initiator="s", purpose="p")


# ───────────────────────── GWT-3 使用记录 ─────────────────────────


class TestGwt3UsageLog:
    def test_use_writes_usage_entry(self, vault):
        vault.add("openai-main", "llm_api_key", LLM_KEY)
        before = datetime.now().astimezone() - timedelta(seconds=1)
        vault.use("openai-main", initiator="l1-skill-x", purpose="日常问答", data_bytes=1200)
        (entry,) = vault.query_usage("openai-main")
        assert entry.credential_id == "openai-main"
        assert entry.timestamp >= before
        assert entry.timestamp.tzinfo is not None
        assert entry.initiator == "l1-skill-x"
        assert entry.purpose == "日常问答"
        assert entry.data_bytes == 1200
        assert entry.status == "ok"

    def test_usage_lives_in_execution_log(self, vault):
        """凭据与审计分离：使用记录落 execution_log 分区，不在 secrets。"""
        vault.add("k1", "llm_api_key", LLM_KEY)
        vault.use("k1", initiator="s", purpose="p")
        assert vault._store.list_files("secrets") == (f"{CRED_PREFIX}k1.json",)
        names = vault._store.list_files("execution_log")
        assert len(names) == 1 and names[0].startswith(f"{USAGE_PREFIX}k1/")

    def test_query_time_range(self, vault):
        vault.add("k1", "llm_api_key", LLM_KEY)
        vault.use("k1", initiator="a", purpose="first")
        mid = datetime.now().astimezone()
        vault.use("k1", initiator="b", purpose="second")
        assert len(vault.query_usage("k1")) == 2
        assert [e.purpose for e in vault.query_usage("k1", since=mid)] == ["second"]
        assert [e.purpose for e in vault.query_usage("k1", until=mid)] == ["first"]

    def test_failed_usage_can_be_backfilled(self, vault):
        vault.add("k1", "llm_api_key", LLM_KEY)
        vault.record_usage("k1", initiator="l4-lens", purpose="跨视角调用",
                           data_bytes=300, status="failed")
        (entry,) = vault.query_usage("k1")
        assert entry.status == "failed"

    def test_naive_time_bounds_rejected(self, vault):
        vault.add("k1", "llm_api_key", LLM_KEY)
        with pytest.raises(CredentialValidationError, match="时区"):
            vault.query_usage("k1", since=datetime(2026, 1, 1))


# ───────────────────────── GWT-4 吊销重设 ─────────────────────────


class TestGwt4RevokeRotate:
    def test_revoke_removes_value_keeps_history(self, vault):
        vault.add("k1", "llm_api_key", LLM_KEY)
        vault.use("k1", initiator="s", purpose="p")
        receipt = vault.revoke("k1")
        assert receipt.masked == mask_secret(LLM_KEY)
        with pytest.raises(CredentialNotFoundError):
            vault.get_view("k1")
        with pytest.raises(CredentialNotFoundError):
            vault.use("k1", initiator="s", purpose="p")
        (entry,) = vault.query_usage("k1")  # 历史使用记录保留可查
        assert entry.purpose == "p"

    def test_rotate_bumps_version_old_value_gone(self, vault):
        vault.add("k1", "llm_api_key", LLM_KEY)
        view = vault.rotate("k1", "sk-proj-NEWVALUE9999")
        assert view.version == 2
        assert view.masked == mask_secret("sk-proj-NEWVALUE9999")
        assert vault.use("k1", initiator="s", purpose="p") == "sk-proj-NEWVALUE9999"

    def test_rotate_missing_raises(self, vault):
        with pytest.raises(CredentialNotFoundError):
            vault.rotate("nope", "v")

    def test_usage_survives_reopen(self, vault, tmp_path):
        vault.add("k1", "llm_api_key", LLM_KEY)
        vault.use("k1", initiator="s", purpose="p")
        reopened = CredentialVault(Store.open(root_of(vault), PASS))
        assert reopened.get_view("k1").masked == mask_secret(LLM_KEY)
        assert len(reopened.query_usage("k1")) == 1


# ───────────────────────── GWT-5 零泄漏 ─────────────────────────


class TestGwt5NoLeak:
    def test_repr_never_plaintext(self, vault):
        vault.add("k1", "llm_api_key", LLM_KEY)
        record = vault._load("k1")
        assert LLM_KEY not in repr(record)
        assert LLM_KEY not in str(record)
        assert mask_secret(LLM_KEY) in repr(record)

    def test_export_safe_has_no_plaintext(self, vault):
        vault.add("k1", "llm_api_key", LLM_KEY)
        vault.use("k1", initiator="s", purpose="p", data_bytes=10)
        import json
        blob = json.dumps(vault.export_safe("k1"), ensure_ascii=False)
        assert LLM_KEY not in blob
        assert "value" not in vault.export_safe("k1")["credential"]  # 视图无 value 键
        assert vault.export_safe("k1")["credential"].keys() >= {"credential_id", "masked"}

    def test_export_all_safe(self, vault):
        vault.add("a", "llm_api_key", LLM_KEY)
        vault.add("b", "smtp_credential", SMTP_PWD)
        blob = vault.export_all_safe()
        assert set(blob) == {"a", "b"}
        import json
        assert LLM_KEY not in json.dumps(blob) and SMTP_PWD not in json.dumps(blob)

    def test_usage_entry_has_no_plaintext(self, vault):
        """使用记录只记归属与计量，不记明文。"""
        vault.add("k1", "llm_api_key", LLM_KEY)
        entry = vault.record_usage("k1", initiator="s", purpose="p")
        assert LLM_KEY not in entry.model_dump_json()

    def test_no_network_dependency(self):
        """凭据子系统不 import 任何网络库（零接触的静态面；按 import 语句查）。"""
        import re
        import st_agent.l0.secrets.models as m
        import st_agent.l0.secrets.vault as v
        for mod in (m, v):
            src = Path(mod.__file__).read_text(encoding="utf-8")
            imports = "\n".join(
                line for line in src.splitlines()
                if re.match(r"\s*(import|from)\s", line)
            )
            for forbidden in ("requests", "urllib", "http", "socket", "smtplib"):
                assert forbidden not in imports, f"{mod.__name__} 引用了网络组件 {forbidden}"

    def test_disk_wide_no_plaintext(self, vault):
        """全盘扫描：所有分区文件均不含凭据明文。"""
        vault.add("openai-main", "llm_api_key", LLM_KEY)
        vault.add("smtp-home", "smtp_credential", SMTP_PWD)
        vault.use("openai-main", initiator="s", purpose="p")
        root = root_of(vault)
        for f in root.rglob("*"):
            if f.is_file() and f.name != "keyfile.json":
                assert LLM_KEY.encode() not in f.read_bytes(), f.name
                assert SMTP_PWD.encode() not in f.read_bytes(), f.name
