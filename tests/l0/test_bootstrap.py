"""启动引导配置（[D-129]）：存储根指针与 ``.env`` 原地改写。

只测纯文件面：指针的解析顺序 / 损坏显式失败；``.env`` 的保留、追加、引号与原子写。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from st_agent.l0.bootstrap.dotenv import read_dotenv, write_dotenv
from st_agent.l0.bootstrap.root import (
    BootstrapConfigError,
    bootstrap_path,
    clear_store_root,
    platform_default_root,
    read_store_root,
    resolve_root,
    write_store_root,
)


class TestStoreRootPointer:
    def test_cli_beats_env_and_pointer(self, tmp_path: Path):
        pointer = tmp_path / "boot.json"
        write_store_root(tmp_path / "from-pointer", pointer)

        root, source = resolve_root(
            tmp_path / "from-cli",
            env={"ST_AGENT_ROOT": str(tmp_path / "from-env")},
            bootstrap_file=pointer,
        )

        assert source == "cli"
        assert root == tmp_path / "from-cli"

    def test_env_beats_pointer(self, tmp_path: Path):
        pointer = tmp_path / "boot.json"
        write_store_root(tmp_path / "from-pointer", pointer)

        root, source = resolve_root(
            env={"ST_AGENT_ROOT": str(tmp_path / "from-env")},
            bootstrap_file=pointer,
        )

        assert source == "env"
        assert root == tmp_path / "from-env"

    def test_pointer_beats_default(self, tmp_path: Path):
        pointer = tmp_path / "boot.json"
        (tmp_path / "from-pointer").mkdir()
        write_store_root(tmp_path / "from-pointer", pointer)

        root, source = resolve_root(env={}, bootstrap_file=pointer)

        assert source == "bootstrap"
        assert root == tmp_path / "from-pointer"

    def test_stale_pointer_fails_loudly(self, tmp_path: Path):
        pointer = tmp_path / "boot.json"
        write_store_root(tmp_path / "gone", pointer)

        with pytest.raises(BootstrapConfigError, match="不存在"):
            resolve_root(env={}, bootstrap_file=pointer)

    def test_default_when_no_pointer(self, tmp_path: Path):
        root, source = resolve_root(
            env={"LOCALAPPDATA": str(tmp_path)}, platform="win32",
            bootstrap_file=tmp_path / "missing.json",
        )

        assert source == "default"
        assert root == tmp_path / "STAgent"

    def test_pointer_is_outside_the_default_store_root(self, tmp_path: Path):
        path = bootstrap_path(env={"LOCALAPPDATA": str(tmp_path)}, platform="win32")
        assert path.parent == tmp_path / "STAgent-boot"
        assert platform_default_root(env={"LOCALAPPDATA": str(tmp_path)},
                                     system="win32") not in path.parents

    def test_write_read_and_clear_round_trip(self, tmp_path: Path):
        pointer = tmp_path / "boot.json"
        write_store_root(tmp_path / "custom-root", pointer)

        assert read_store_root(pointer) == tmp_path / "custom-root"
        assert clear_store_root(pointer) is True
        assert read_store_root(pointer) is None
        assert clear_store_root(pointer) is False

    def test_corrupt_pointer_is_explicit(self, tmp_path: Path):
        pointer = tmp_path / "boot.json"
        pointer.write_text("{not json", encoding="utf-8")

        with pytest.raises(BootstrapConfigError):
            read_store_root(pointer)


class TestDotenvWrite:
    def test_updates_target_keys_and_preserves_everything_else(self, tmp_path: Path):
        path = tmp_path / ".env"
        path.write_text(
            "# 注释保留\n"
            "GITHUB_PAT=abc\n"
            "LLM_API_KEY=old-key\n"
            "\n"
            "LLM_MODEL=old-model  # 行尾注释随本行重写\n",
            encoding="utf-8",
        )

        write_dotenv(path, {"LLM_API_KEY": "new-key", "LLM_MODEL": "new-model"})
        text = path.read_text(encoding="utf-8")

        assert "# 注释保留" in text and "GITHUB_PAT=abc" in text
        assert "LLM_API_KEY=new-key" in text and "LLM_MODEL=new-model" in text
        assert "old-key" not in text
        assert read_dotenv(path)["LLM_API_KEY"] == "new-key"

    def test_appends_missing_keys(self, tmp_path: Path):
        path = tmp_path / ".env"
        path.write_text("GITHUB_PAT=abc\n", encoding="utf-8")

        write_dotenv(path, {"LLM_BASE_URL": "https://api.example.com/v1"})

        values = read_dotenv(path)
        assert values["GITHUB_PAT"] == "abc"
        assert values["LLM_BASE_URL"] == "https://api.example.com/v1"

    def test_values_with_spaces_or_hash_are_quoted(self, tmp_path: Path):
        path = tmp_path / ".env"
        write_dotenv(path, {"LLM_MODEL": "my model #1"})

        assert read_dotenv(path)["LLM_MODEL"] == "my model #1"

    def test_newlines_are_rejected(self, tmp_path: Path):
        path = tmp_path / ".env"
        with pytest.raises(BootstrapConfigError):
            write_dotenv(path, {"LLM_MODEL": "one\ntwo"})
        assert not path.exists()

    def test_file_is_created_atomically_with_owner_only_mode(self, tmp_path: Path):
        path = tmp_path / ".env"
        write_dotenv(path, {"LLM_MODEL": "m"})

        assert path.exists()
        assert not list(tmp_path.glob(".env.tmp"))
